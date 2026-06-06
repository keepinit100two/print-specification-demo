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

### End-to-end workflow (implemented)

Every run follows the same spine through compliance and adaptation planning.
After `AdaptationPlan` is produced, the workflow **forks** into one of two
remediation paths, then **rejoins** at validation before approval and packaging.

```
SUBMITTED
    ↓
NORMALIZATION          (NormalizationResult / DesignJob)
    ↓
SPECIFICATION          (PrintSpecification)
    ↓
COMPLIANCE             (ComplianceResult)
    ↓
ADAPTATION             (AdaptationPlan)
    ├── DETERMINISTIC_TRANSFORM   → TransformedAsset / DeterministicTransformResult
    └── AI_GENERATION             → GenerationRequest → GeneratedCandidate + ModelInvocationRecord
    ↓
VALIDATION             (ValidationResult)     ← single gate for both branches
    ↓
APPROVAL_PACKAGE       (ApprovalPackage)
    ↓
APPROVAL_DECISION      (ApprovalDecision)     ← human gate; spine stops at owner review
    ↓
PRODUCTION_PACKAGE     (ProductionPackage)
    ↓
COMPLETED
```

Fine-grained `PrintWorkflowState` paths (orchestrated in `print_orchestrator.py`):

| Phase | State path |
| --- | --- |
| Intake / normalization | `SUBMITTED → NORMALIZATION_PENDING → NORMALIZED` |
| Specification | `NORMALIZED → SPECIFICATION_PENDING → SPECIFICATION_RESOLVED` |
| Compliance | `SPECIFICATION_RESOLVED → COMPLIANCE_PENDING → COMPLIANCE_COMPLETE` |
| Adaptation planning | `COMPLIANCE_COMPLETE → ADAPTATION_PLANNED` |
| **Deterministic branch** | `ADAPTATION_PLANNED → DETERMINISTIC_TRANSFORM_PENDING → DETERMINISTIC_TRANSFORM_COMPLETE → VALIDATION_PENDING` |
| **AI branch** | `ADAPTATION_PLANNED → GENERATION_PENDING → GENERATION_RUNNING → GENERATION_COMPLETE → VALIDATION_PENDING` |
| Validation | `VALIDATION_PENDING → VALIDATION_COMPLETE` (or `VALIDATION_FAILED`) |
| Approval package | `VALIDATION_COMPLETE → OWNER_REVIEW_PENDING` |
| Approval decision | `OWNER_REVIEW_PENDING → APPROVED` (or `REJECTED` / `REVISION_REQUESTED`) |
| Production packaging | `APPROVED → PRODUCTION_PACKAGING_PENDING → PRODUCTION_PACKAGE_CREATED` |
| Completion | `PRODUCTION_PACKAGE_CREATED → COMPLETED` |

### Deterministic Transform path vs AI Generation path

**Deterministic Transform path** — used when `AdaptationPlan.requires_generation`
is `False` and the plan contains supported deterministic steps (`RESIZE`,
`DPI_ADJUSTMENT`, `PAD`, `CROP`, `COLOR_PROFILE_CONVERSION`). The
`DeterministicTransformService` records intent and placeholder
`TransformedAsset` outputs without calling a model or reading files from disk.

**AI Generation path** — used when `AdaptationPlan.requires_generation` is
`True` (e.g. the plan includes `UPSCALE`, `BACKGROUND_REMOVAL`, or other steps
that cannot be satisfied deterministically in the MVP). The spine calls
`PromptConstructionService` to build a strict `GenerationRequest`, then
`AIGenerationService` executes it and returns `GeneratedCandidate` outputs plus
`ModelInvocationRecord` audit data.

**Why AI is not the default path**

- Adaptation planning prefers **deterministic remediation** when compliance gaps
  can be closed without synthesis. AI is selected only when the plan explicitly
  requires generation.
- When submitted assets are already print-ready at compliance, adaptation may be
  skipped entirely (no transform, no generation).
- AI is an **actuator**, not a controller: it never decides routing, validates
  its own output, or approves candidates.
- Default deployment uses **`PRINT_GENERATION_MODE=fake`** — deterministic local
  generation for tests and offline demos with no network or API key.

**Why validation is downstream of both branches**

- Both branches produce **print outputs** (`GeneratedCandidate` or
  `TransformedAsset`) that must be measured against the same `PrintSpecification`
  before human approval.
- A single `PrintValidationService` gate (Option A: print-readiness only) keeps
  reviewers from seeing unmeasured assets regardless of how they were produced.
- Validation does not judge creative quality, brand fit, or approval — it only
  checks dimensions, DPI, format, and color profile when metadata is present.

### GeneratedCandidate vs TransformedAsset

| Contract | Produced by | Meaning |
| --- | --- | --- |
| `GeneratedCandidate` | AI Generation Service | A model output from executing a `GenerationRequest`. Carries `candidate_id`, `request_id`, `uri`, and observed `ImageProperties`. |
| `TransformedAsset` | Deterministic Transform Service | A non-AI output from executing supported `AdaptationPlan` steps (resize, DPI adjust, pad, crop, color-profile conversion). Carries `transformed_asset_id`, `plan_id`, `uri`, and optional `ImageProperties`. |

Both are **print outputs** that downstream validation may measure against the
`PrintSpecification`. They are not interchangeable at the schema level: AI
outputs always flow through `GeneratedCandidate`; deterministic outputs flow
through `TransformedAsset` / `DeterministicTransformResult`.

### ModelInvocationRecord

`ModelInvocationRecord` is the audit/observability contract for a single AI
generation call. It records provider, model name, timing, status, errors, and
`generated_candidate_ids` — but it does **not** decide workflow routing. The
orchestrator owns control flow; the generation service is an actuator that
returns candidates plus invocation records.

### AI generation infrastructure (boundary)

| Component | Role |
| --- | --- |
| `app/services/print_generation.py` | Generation actuator. Selects fake vs OpenAI mode, assembles `GeneratedCandidate` + `ModelInvocationRecord`. |
| `app/services/openai_image_client.py` | **Provider boundary only.** Lazy OpenAI import, API key check, `images.generate` call. Returns provider-neutral `OpenAIImageGenerationResult`. No workflow contracts, no validation, no orchestration. |
| `app/services/generated_artifact_store.py` | Optional persistence under `artifacts/generated/`. Decodes base64 API payloads and writes `{candidate_id}.{format}`. Used by the generation service in OpenAI mode when bytes are returned. |

**Generation modes** (`PRINT_GENERATION_MODE`):

- `fake` (default) — deterministic local generator for unit tests and offline
  demos. Emits `artifact://generated/{candidate_id}.{format}` URIs. No network,
  no `OPENAI_API_KEY`.
- `openai` — calls `openai_image_client`, maps print dimensions to supported
  gpt-image-1 sizes, optionally saves returned base64 images via
  `generated_artifact_store`.

**Environment variables** (see also `docs/DEPLOYMENT.md`):

- `PRINT_GENERATION_MODE` — `fake` or `openai` (default `fake`).
- `OPENAI_API_KEY` — required when mode is `openai`.
- `PRINT_OPENAI_IMAGE_MODEL` — image model id (default `gpt-image-1`).

**Manual smoke test:** `python scripts/demo_generate_image.py` builds a sample
`GenerationRequest`, calls `generate_candidates()`, and prints candidate URIs
and invocation metadata. Loads `.env` when `python-dotenv` is installed.

---

## Core contracts

Primary stage outputs defined in `app/domain/print_schemas.py`:

| Contract | Role |
| --- | --- |
| `NormalizationResult` | Wraps normalization outcome and optional `DesignJob` (typed intent — not production requirements). |
| `PrintSpecification` | Resolved production requirements (dimensions, DPI, color, formats) from `DesignJob` + config. |
| `ComplianceResult` | Measurement of submitted-image readiness against the spec (`is_print_ready`, `ComplianceFinding`s). |
| `AdaptationPlan` | Ordered deterministic transformation intent plus `requires_generation` flag; no execution. |
| `GenerationRequest` | Strict, spec-derived model-ready request (pixels, DPI, format, candidate count). |
| `GeneratedCandidate` | AI branch output: one model-produced asset (`candidate_id`, `uri`, `ImageProperties`). |
| `TransformedAsset` | Deterministic branch output: one non-AI transformed asset (`transformed_asset_id`, `uri`, optional properties). |
| `ValidationResult` | Print-readiness verdict for one output asset against `PrintSpecification`. |
| `ApprovalPackage` | Routes passed validation outputs to owner review (`candidate_ids`, validation reference). |
| `ApprovalDecision` | Records human outcome (`APPROVED`, `REJECTED`, `CHANGES_REQUESTED`) with approver and rationale. |
| `ProductionPackage` | Final approved bundle for the print shop (`output_uris`, `manifest`, traceable ids). |

`PrintWorkflowRunResult` bundles all stage outputs for orchestration logs and
debugging. `ModelInvocationRecord` accompanies AI generation for observability
only.

---

## Subsystem responsibilities

| Subsystem | Module | Produces | Must not |
| --- | --- | --- | --- |
| `NormalizationService` | `print_normalization.py` | `NormalizationResult` / `DesignJob` | Resolve `PrintSpecification` or production requirements |
| `SpecificationResolutionService` | `print_specification.py` | `PrintSpecification` | Measure assets or re-derive design intent |
| `TechnicalComplianceService` | `print_compliance.py` | `ComplianceResult` | Transform assets, plan adaptation, or call AI |
| `AdaptationPlanningService` | `print_adaptation.py` | `AdaptationPlan` | Execute transforms or call models |
| `PromptConstructionService` | `print_prompt_construction.py` | `GenerationRequest` | Call the model (AI branch only) |
| `AIGenerationService` | `print_generation.py` | `GeneratedCandidate`, `ModelInvocationRecord` | Decide routing, validate, approve, or package |
| `DeterministicTransformService` | `print_deterministic_transform.py` | `DeterministicTransformResult` / `TransformedAsset` | Call AI, perform real image I/O, or route workflow |
| `PrintValidationService` | `print_validation.py` | `ValidationResult` | Approve, call AI, or perform file I/O |
| `ApprovalWorkflowService` | `print_approval.py` (`create_approval_package`) | `ApprovalPackage` | Record decisions or create production packages |
| `ApprovalDecisionService` | `print_approval.py` (`record_approval_decision`) | `ApprovalDecision` | Auto-approve or package for production |
| `ProductionPackagingService` | `print_production_packaging.py` | `ProductionPackage` | Re-validate, print, ship, or call AI |
| **Orchestration Spine** | `print_orchestrator.py` | `PrintWorkflowRunResult`, transitions | Perform any subsystem work directly |

Typed interfaces: `app/services/print_interfaces.py`.

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

## 5b. Deterministic Transform Execution

- **Owns:** `DeterministicTransformResult`, `TransformedAsset`.
- **Consumes:** `DesignJob` + `PrintSpecification` + `AdaptationPlan`.
- **Produces:** `DeterministicTransformResult` (bundling one or more `TransformedAsset` outputs).
- **Allowed state transitions:** `DETERMINISTIC_TRANSFORM_PENDING -> DETERMINISTIC_TRANSFORM_COMPLETE`; `-> DETERMINISTIC_TRANSFORM_FAILED`.
- **Must not do:** Call AI, create `GeneratedCandidate`, perform real image processing, read files from disk, or decide orchestration routing. MVP returns placeholder `artifact://transformed/{plan_id}-{index}` URIs and records which steps were applied.
- **Supported steps (MVP):** `RESIZE`, `DPI_ADJUSTMENT`, `PAD`, `CROP`, `COLOR_PROFILE_CONVERSION`.
- **Unsupported steps (MVP):** `UPSCALE`, `BACKGROUND_REMOVAL`, `RECOLOR`, `BLEED_EXTENSION` route to `NEEDS_REVIEW` for human follow-up.
- **Future test candidates:** Empty plan -> `SKIPPED`; supported step -> `SUCCEEDED` asset; unsupported step -> `NEEDS_REVIEW` with `reasons`.

## 6. Prompt Construction

- **Owns:** `GenerationRequest` (assembly of).
- **Consumes:** `AdaptationPlan` + `PrintSpecification`.
- **Produces:** `GenerationRequest`.
- **Allowed state transitions:** `ADAPTATION_PLANNED -> GENERATION_PENDING` (AI branch only, when `requires_generation=True`).
- **Must not do:** Call the model, emit loose prompt text only, or relax spec constraints. It builds a **strict, model-ready** request (resolved `output_width_px`/`output_height_px`, `target_dpi`, `color_mode`, `output_format`, `candidate_count`, references) derived from the spec. Not used on the deterministic branch.
- **Future test candidates:** Pixel dimensions derive from spec mm + DPI; `color_mode`/`output_format` match `PrintSpecification`; required fields present and non-empty.

## 7. AI Generation

- **Owns:** `GeneratedCandidate`, `ModelInvocationRecord` (and `InvocationStatus`).
- **Consumes:** `GenerationRequest`.
- **Produces:** `GeneratedCandidate` **plus** `ModelInvocationRecord`.
- **Allowed state transitions:** `GENERATION_PENDING -> GENERATION_RUNNING -> GENERATION_COMPLETE`; `-> GENERATION_FAILED`.
- **Must not do:** Decide control flow, validate its own output, approve, or package. It is an **actuator**: execute the request, emit candidates, and record provider/model/timing/cost/errors in `ModelInvocationRecord`.
- **Modes:** `fake` (default, deterministic) and `openai` (via `openai_image_client`). OpenAI mode may persist returned bytes through `generated_artifact_store` under `artifacts/generated/`.
- **Future test candidates:** Each candidate links back to `request_id`; `ModelInvocationRecord.status` reflects success/failure; `generated_candidate_ids` matches produced candidates; fake mode requires no API key.

## 8. Output Validation

- **Owns:** `ValidationResult`.
- **Consumes:** `PrintSpecification` + one output asset (`GeneratedCandidate` **or** `TransformedAsset`).
- **Produces:** `ValidationResult`.
- **Allowed state transitions:** `VALIDATION_PENDING -> VALIDATION_COMPLETE`; `-> VALIDATION_FAILED`. Entered from `GENERATION_COMPLETE` or `DETERMINISTIC_TRANSFORM_COMPLETE`.
- **Scope (Option A — print-readiness only):** Measures `width_px`, `height_px`, `min_dpi`, `file_format`, and `color_profile` when both spec and asset profiles are present. **Does not** judge marketing quality, brand fit, copywriting, aesthetics, or human approval.
- **Must not do:** Approve, create `ApprovalDecision`, call AI, read files from disk, or package. Missing required metadata (`width_px`/`height_px`/`dpi`) -> `NEEDS_REVIEW` with `MISSING_VALIDATION_METADATA`.
- **Id fields:** `validated_candidate_ids` / `passed_candidate_ids` currently hold validated **output** ids (AI `candidate_id` or deterministic `transformed_asset_id`) until a dedicated asset-id field is added.
- **Future test candidates:** Low DPI -> `FAILED` with `min_dpi` finding; unsupported format -> `file_format` finding; compliant output -> `PASSED`.

## 9a. Approval Package Routing (`ApprovalWorkflowService`)

- **Owns:** `ApprovalPackage`.
- **Consumes:** `ValidationResult` + passed outputs + `PrintSpecification` + `DesignJob`.
- **Produces:** `ApprovalPackage` (request-for-decision).
- **Allowed state transitions:** `VALIDATION_COMPLETE -> OWNER_REVIEW_PENDING`.
- **Must not do:** Record `ApprovalDecision`, auto-approve, or create `ProductionPackage`.
- **Orchestration:** Wired — Phase 9 in `print_orchestrator.py`.

## 9b. Approval Decision Recording (`ApprovalDecisionService`)

- **Owns:** `ApprovalDecision` (and `ApprovalStatus`).
- **Consumes:** `ApprovalPackage` + human decision input (candidate, status, approver).
- **Produces:** `ApprovalDecision`.
- **Allowed state transitions:** `OWNER_REVIEW_PENDING -> APPROVED`; `-> REJECTED`; `-> REVISION_REQUESTED`.
- **Must not do:** Auto-approve, generate or transform assets, or build the production package.
- **Orchestration:** Wired — Phase 9b; decision context currently supplied via `WorkflowAdvanceRequest.metadata["approval_decision"]` (see Known MVP Tradeoffs).
- **Future test candidates:** `ApprovalPackage.candidate_ids` ⊆ validation pass set; `CHANGES_REQUESTED` carries actionable `reasons`; `ApprovalDecision` records `approver` and `decided_at`.

## 10. Production Packaging

- **Owns:** `ProductionPackage`.
- **Consumes:** `ApprovalDecision` + approved output (`GeneratedCandidate` or `TransformedAsset`) + `PrintSpecification` + `ValidationResult`.
- **Produces:** `ProductionPackage`.
- **Allowed state transitions:** `APPROVED -> PRODUCTION_PACKAGING_PENDING -> PRODUCTION_PACKAGE_CREATED`; `PRODUCTION_PACKAGE_CREATED -> COMPLETED`.
- **Must not do:** Re-validate, re-generate, print, ship, or alter the approved output. Assembles `output_uris` and `manifest` only.
- **Orchestration:** Wired — Phase 10 in `print_orchestrator.py`.

## 11. Workflow Completion (orchestration routing)

- **Owns:** No new contract — marks an existing run complete.
- **Consumes:** `PrintWorkflowRunResult` with `production_package` already populated.
- **Produces:** Updated run state `COMPLETED`, `status=PASSED`.
- **Allowed state transitions:** `PRODUCTION_PACKAGE_CREATED -> COMPLETED`.
- **Must not do:** Call subsystems, perform file I/O, AI, printing, or shipping. Completion routing simply acknowledges the production package exists.
- **Orchestration:** Wired — Phase 11 in `print_orchestrator.py`.

## 12. Orchestration Spine

- **Owns:** `PrintWorkflowRunResult`; the `PrintWorkflowStage`/`PrintWorkflowState` state machine.
- **Consumes:** Stage outputs from every subsystem.
- **Produces:** `PrintWorkflowRunResult` (bundles all stage outputs + `model_invocations` + `approval_package` + `approval` + `production_package`).
- **Allowed state transitions:** All transitions across the run, including terminal `-> CANCELLED` and `-> FAILED` from any stage.
- **Must not do:** Perform subsystem work (no normalization, spec resolution, compliance, generation, validation, packaging logic). It **coordinates** transitions and aggregates results only.
- **Future test candidates:** Illegal transitions rejected; `stage` and `state` stay consistent; failure in any stage drives `FAILED` with run-level `reasons`; partial runs serialize (optional stage outputs).

## 13. Audit / Observation

- **Owns:** Nothing (no contracts).
- **Consumes:** `PrintWorkflowRunResult`, `ModelInvocationRecord`, and `ContractProvenance` across contracts (read-only).
- **Produces:** Logs / reports only (e.g. the existing `logs/events.jsonl` + `ops/weekly_report.py` style observation).
- **Allowed state transitions:** None.
- **Must not do:** Mutate any contract, change state, or influence control flow. **Observe only.**
- **Future test candidates:** Audit reconstructs lineage from `derived_from_ids`; observation never alters state; every state transition emits an event.

## 14. Storage / Asset

- **Owns:** Nothing semantically (no domain contracts).
- **Consumes:** Asset URIs referenced by `SubmittedAsset`, `GeneratedCandidate`, `TransformedAsset`, `ProductionPackage`.
- **Produces:** Stored bytes + resolvable URIs only.
- **Generated images:** `app/services/generated_artifact_store.py` writes OpenAI (and demo) outputs to `artifacts/generated/{candidate_id}.{format}`. Contract URIs remain `artifact://generated/...`; the store is a local persistence helper, not a workflow stage.
- **Allowed state transitions:** None.
- **Must not do:** Interpret, validate, or transform asset content; make routing decisions. **Store assets only.**
- **Future test candidates:** URIs resolve to stored bytes; storage is idempotent per asset id; missing asset surfaces a clear error to the consuming subsystem (not a state change).

## 15. Configuration

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
| Deterministic Transform Execution | `DeterministicTransformResult`, `TransformedAsset` |
| Prompt Construction | `GenerationRequest` |
| AI Generation | `GeneratedCandidate`, `ModelInvocationRecord` |
| OpenAI client (provider boundary) | — (provider DTOs only) |
| Generated artifact store | — (local files under `artifacts/generated/`) |
| Output Validation | `ValidationResult` |
| Approval Workflow (`ApprovalWorkflowService`) | `ApprovalPackage` |
| Approval Decision (`ApprovalDecisionService`) | `ApprovalDecision` |
| Production Packaging | `ProductionPackage` |
| Orchestration Spine | `PrintWorkflowRunResult`, state machine |
| Audit / Observation | — (read-only) |
| Storage / Asset | — (asset bytes/URIs) |
| Configuration | rules/policy, `config_version` |

---

## Known MVP Tradeoffs

Documented gaps that tests and orchestration account for but schemas have not
yet closed:

- **`validated_candidate_ids` naming** — `ValidationResult.validated_candidate_ids`
  and `passed_candidate_ids` currently store validated **output** ids for both
  AI candidates (`candidate_id`) and deterministic transforms
  (`transformed_asset_id`). A dedicated asset-id field may be added later.
- **Deterministic transform on run bundle** — `PrintWorkflowRunResult` does not
  yet expose a `deterministic_transform` field. Orchestration validation and
  production packaging can search `deterministic_transform.transformed_assets`
  when present; until then, wiring tests use a compliant `GeneratedCandidate`
  placeholder when advancing from `DETERMINISTIC_TRANSFORM_COMPLETE`.
- **Approval decision input** — Human approval outcomes are passed via
  `WorkflowAdvanceRequest.metadata["approval_decision"]`
  (`status`, `candidate_id`, `approver`) rather than first-class typed fields
  on `WorkflowAdvanceRequest`.
- **No external email delivery** — Owner review is modeled as a workflow stop
  (`OWNER_REVIEW_PENDING`); there is no email/notification subsystem yet.

---

## Workflow Completion Status

| Item | Status |
| --- | --- |
| All workflow stages implemented | **Yes** — normalization through production packaging services exist and are unit-tested |
| All workflow stages orchestrated | **Yes** — Phases 1–11 wired in `print_orchestrator.py` |
| End-to-end happy path tested | **Yes** — `test_end_to_end_happy_path_smoke` exercises the full AI remediation branch through `COMPLETED` |
| Test suite | **233 tests passing** (`pytest`) |
