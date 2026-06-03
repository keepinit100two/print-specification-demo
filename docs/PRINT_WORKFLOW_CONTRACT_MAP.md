# Print Workflow Contract Map

Subsystem-to-contract ownership for the print-specification demo. All contracts
referenced here are defined in `app/domain/print_schemas.py`. This document maps
*which subsystem owns which contract*, what it consumes/produces, the legal
`PrintWorkflowState` transitions it may drive, and explicit boundaries.

## Conventions

- **State** values come from `PrintWorkflowState`; **Stage** values from `PrintWorkflowStage`.
- Subsystems perform work; the **Orchestration Spine** owns the actual state transition. A subsystem "drives" a transition by producing the output that authorizes it.
- Every produced contract should carry `ContractProvenance` (`source_id`, `derived_from_ids`, `created_by_stage`, `config_version`).
- The AI is an **actuator**, not a controller: it executes a `GenerationRequest` and records outcomes; it never decides routing.

### Pipeline overview

```
RawSubmission
  -> DesignJob / NormalizationResult
  -> PrintSpecification
  -> ComplianceResult
  -> AdaptationPlan
  -> GenerationRequest
  -> GeneratedCandidate + ModelInvocationRecord
  -> ValidationResult
  -> ApprovalPackage -> ApprovalDecision
  -> ProductionPackage
  -> PrintWorkflowRunResult (bundles all of the above)
```

---

## 1. Submission Intake

- **Owns:** `RawSubmission`, `SubmittedAsset`, `ImageProperties` (as captured at intake).
- **Consumes:** Raw external input (API payload / upload). No upstream contracts.
- **Produces:** `RawSubmission`.
- **Allowed state transitions:** `SUBMITTED -> INGESTED`; `SUBMITTED -> NORMALIZATION_PENDING`.
- **Must not do:** Interpret or clean the brief, infer `ProductType`, resolve any production requirements, or reject on content quality. Intake is loose and faithful to what was received.
- **Future test candidates:** Missing required asset rejected at the boundary; `RawSubmission` preserves unknown fields in `raw_fields`; idempotent intake by `submission_id`.

## 2. Normalization

- **Owns:** `DesignJob`, `NormalizationResult`.
- **Consumes:** `RawSubmission`.
- **Produces:** `DesignJob` and `NormalizationResult` **only**.
- **Allowed state transitions:** `NORMALIZATION_PENDING -> NORMALIZED`; `-> NORMALIZATION_NEEDS_REVIEW`; `-> NORMALIZATION_FAILED`.
- **Must not do:** Produce `PrintSpecification`. Must not set dimensions, bleed, DPI, color profile, or any production requirement. It captures typed *intent and content* (resolved `ProductType`, `normalized_brief`, attributes) — nothing about how it will be printed.
- **Future test candidates:** Free-text product maps to a valid `ProductType`; ambiguous brief yields `NEEDS_REVIEW` with `reasons`; `NormalizationResult.design_job` is `None` on `FAILED`.

## 3. Specification Resolution

- **Owns:** `PrintSpecification`, `DimensionRequirement`, `ColorRequirement`.
- **Consumes:** `DesignJob` **+ Configuration** (print-shop policy/rules).
- **Produces:** `PrintSpecification`.
- **Allowed state transitions:** `SPECIFICATION_PENDING -> SPECIFICATION_RESOLVED`; `-> SPECIFICATION_FAILED`.
- **Must not do:** Re-derive design intent, measure submitted assets, or run inside normalization. Spec is strictly **downstream** of `DesignJob` + config; it is the resolved production requirement, not a normalization artifact.
- **Future test candidates:** Same `product_type` + config version resolves deterministically; `config_source`/`config_version` recorded; unsupported `ProductType` -> `SPECIFICATION_FAILED`.

## 4. Technical Compliance

- **Owns:** `ComplianceResult`, `ComplianceFinding`.
- **Consumes:** `DesignJob` + `PrintSpecification` + submitted image properties (`ImageProperties`).
- **Produces:** `ComplianceResult`.
- **Allowed state transitions:** `COMPLIANCE_PENDING -> COMPLIANCE_COMPLETE`; `-> COMPLIANCE_FAILED`.
- **Must not do:** Transform or fix assets, plan adaptations, or invoke generation. It only **measures** submitted-image readiness against the spec (`is_print_ready`, per-requirement `findings`).
- **Future test candidates:** DPI below `min_dpi` flagged non-compliant; color-mode mismatch produces a `ComplianceFinding`; `is_print_ready=True` only when all findings are compliant.

## 5. Adaptation Planning

- **Owns:** `AdaptationPlan`, `TransformationStep`.
- **Consumes:** `ComplianceResult` + `PrintSpecification` + `DesignJob`.
- **Produces:** `AdaptationPlan`.
- **Allowed state transitions:** `COMPLIANCE_COMPLETE -> ADAPTATION_PLANNED`.
- **Must not do:** Execute transformations or call any model. It defines **deterministic transformation intent** only (ordered `steps`, `requires_generation` flag) — a reviewable plan with no side effects.
- **Future test candidates:** Each non-compliant finding maps to a `TransformationStep`; `requires_generation=True` only when deterministic transforms are insufficient; plan is deterministic for identical inputs.

## 6. Prompt Construction

- **Owns:** `GenerationRequest` (assembly of).
- **Consumes:** `AdaptationPlan` + `PrintSpecification`.
- **Produces:** `GenerationRequest`.
- **Allowed state transitions:** `ADAPTATION_PLANNED -> GENERATION_PENDING`.
- **Must not do:** Call the model, emit loose prompt text only, or relax spec constraints. It builds a **strict, model-ready** request (resolved `output_width_px`/`output_height_px`, `target_dpi`, `color_mode`, `output_format`, `candidate_count`, references) derived from the spec.
- **Future test candidates:** Pixel dimensions derive from spec mm + DPI; `color_mode`/`output_format` match `PrintSpecification`; required fields present and non-empty.

## 7. AI Generation

- **Owns:** `GeneratedCandidate`, `ModelInvocationRecord` (and `InvocationStatus`).
- **Consumes:** `GenerationRequest`.
- **Produces:** `GeneratedCandidate` **plus** `ModelInvocationRecord`.
- **Allowed state transitions:** `GENERATION_PENDING -> GENERATION_RUNNING -> GENERATION_COMPLETE`; `-> GENERATION_FAILED`.
- **Must not do:** Decide control flow, validate its own output, approve, or package. It is an **actuator**: execute the request, emit candidates, and record provider/model/timing/cost/errors in `ModelInvocationRecord`.
- **Future test candidates:** Each candidate links back to `request_id`; `ModelInvocationRecord.status` reflects success/failure; `generated_candidate_ids` matches produced candidates; `retry_count`/`cost_estimate` recorded.

## 8. Output Validation

- **Owns:** `ValidationResult`.
- **Consumes:** `GeneratedCandidate` + `PrintSpecification` + `AdaptationPlan`.
- **Produces:** `ValidationResult`.
- **Allowed state transitions:** `VALIDATION_PENDING -> VALIDATION_COMPLETE`; `-> VALIDATION_FAILED`.
- **Must not do:** Approve, request human review directly, or package. It is the **automated gate before approval**: measure candidates against spec/plan and report `passed_candidate_ids`.
- **Future test candidates:** Candidate violating spec excluded from `passed_candidate_ids`; empty pass set -> `VALIDATION_FAILED` with `next_steps='regenerate'`; findings reference candidate ids.

## 9. Approval Workflow

- **Owns:** `ApprovalPackage`, `ApprovalDecision` (and `ApprovalStatus`).
- **Consumes:** `ValidationResult` + passed `GeneratedCandidate`s.
- **Produces:** `ApprovalPackage` (request-for-decision) and `ApprovalDecision` (recorded human outcome).
- **Allowed state transitions:** `VALIDATION_COMPLETE -> OWNER_REVIEW_PENDING`; `OWNER_REVIEW_PENDING -> APPROVED`; `-> REJECTED`; `-> REVISION_REQUESTED`.
- **Must not do:** Auto-approve, generate or transform assets, or build the production package. The decision is **human**; the subsystem only routes (`routed_to`) and records.
- **Future test candidates:** `ApprovalPackage.candidate_ids` ⊆ validation pass set; `REVISION_REQUESTED` carries actionable `reasons`; `ApprovalDecision` records `approver` and `decided_at`.

## 10. Production Packaging

- **Owns:** `ProductionPackage`.
- **Consumes:** `ApprovalDecision` + approved `GeneratedCandidate` + `PrintSpecification`.
- **Produces:** `ProductionPackage`.
- **Allowed state transitions:** `APPROVED -> PRODUCTION_PACKAGING_PENDING -> PRODUCTION_PACKAGE_CREATED`; `PRODUCTION_PACKAGE_CREATED -> COMPLETED`.
- **Must not do:** Re-validate, re-generate, or alter the approved candidate. It assembles the final bundle (`output_uris`, `manifest`) for the print shop.
- **Future test candidates:** Package only assembled when `ApprovalStatus.APPROVED`; `manifest` matches `PrintSpecification`; `candidate_id`/`decision_id` traceable.

## 11. Orchestration Spine

- **Owns:** `PrintWorkflowRunResult`; the `PrintWorkflowStage`/`PrintWorkflowState` state machine.
- **Consumes:** Stage outputs from every subsystem.
- **Produces:** `PrintWorkflowRunResult` (bundles all stage outputs + `model_invocations` + `approval_package`).
- **Allowed state transitions:** All transitions across the run, including terminal `-> CANCELLED` and `-> FAILED` from any stage.
- **Must not do:** Perform subsystem work (no normalization, spec resolution, compliance, generation, validation, packaging logic). It **coordinates** transitions and aggregates results only.
- **Future test candidates:** Illegal transitions rejected; `stage` and `state` stay consistent; failure in any stage drives `FAILED` with run-level `reasons`; partial runs serialize (optional stage outputs).

## 12. Audit / Observation

- **Owns:** Nothing (no contracts).
- **Consumes:** `PrintWorkflowRunResult`, `ModelInvocationRecord`, and `ContractProvenance` across contracts (read-only).
- **Produces:** Logs / reports only (e.g. the existing `logs/events.jsonl` + `ops/weekly_report.py` style observation).
- **Allowed state transitions:** None.
- **Must not do:** Mutate any contract, change state, or influence control flow. **Observe only.**
- **Future test candidates:** Audit reconstructs lineage from `derived_from_ids`; observation never alters state; every state transition emits an event.

## 13. Storage / Asset

- **Owns:** Nothing semantically (no domain contracts).
- **Consumes:** Asset URIs referenced by `SubmittedAsset`, `GeneratedCandidate`, `ProductionPackage`.
- **Produces:** Stored bytes + resolvable URIs only.
- **Allowed state transitions:** None.
- **Must not do:** Interpret, validate, or transform asset content; make routing decisions. **Store assets only.**
- **Future test candidates:** URIs resolve to stored bytes; storage is idempotent per asset id; missing asset surfaces a clear error to the consuming subsystem (not a state change).

## 14. Configuration

- **Owns:** Rules/policy inputs (e.g. routing/spec config such as `configs/routing.json`); `config_version` semantics.
- **Consumes:** Nothing at runtime.
- **Produces:** Rule values consumed by Specification Resolution (and any policy-driven subsystem).
- **Allowed state transitions:** None.
- **Must not do:** Hold runtime state, perform subsystem work, or make per-run decisions. **Supply rules only**; decisions belong to the subsystems that read them.
- **Future test candidates:** Spec resolution is reproducible for a pinned `config_version`; missing/invalid config fails fast; config changes are observable via `ContractProvenance.config_version`.

---

## Ownership summary

| Subsystem | Owns (contracts) |
| --- | --- |
| Submission Intake | `RawSubmission`, `SubmittedAsset` |
| Normalization | `DesignJob`, `NormalizationResult` |
| Specification Resolution | `PrintSpecification`, `DimensionRequirement`, `ColorRequirement` |
| Technical Compliance | `ComplianceResult`, `ComplianceFinding` |
| Adaptation Planning | `AdaptationPlan`, `TransformationStep` |
| Prompt Construction | `GenerationRequest` |
| AI Generation | `GeneratedCandidate`, `ModelInvocationRecord` |
| Output Validation | `ValidationResult` |
| Approval Workflow | `ApprovalPackage`, `ApprovalDecision` |
| Production Packaging | `ProductionPackage` |
| Orchestration Spine | `PrintWorkflowRunResult`, state machine |
| Audit / Observation | — (read-only) |
| Storage / Asset | — (asset bytes/URIs) |
| Configuration | rules/policy, `config_version` |
