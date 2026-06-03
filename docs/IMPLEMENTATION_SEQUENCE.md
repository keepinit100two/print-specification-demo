# Implementation Sequence (Test-Driven)

Defines the order in which to build the print-specification demo. The guiding
principle is **spine-first, then one subsystem at a time** — never build all
workers and integrate at the end.

## Strategy

- Build a **thin orchestration spine shell early** (Phase 0). It validates state
  transitions and returns partial results before any subsystem exists.
- The shell uses the already-defined orchestration contracts:
  `WorkflowAdvanceRequest`, `WorkflowAdvanceResult`, `TransitionCheckResult`,
  `SubsystemExecutionRecord`.
- Implement and wire **one subsystem per phase**. Each phase adds: (a) unit tests
  for the subsystem's own logic, and (b) orchestration wiring tests proving it
  connects correctly to the spine.
- The spine **grows incrementally and never absorbs subsystem logic**. It only
  sequences, validates transitions (via `app/domain/print_state_machine.py`),
  calls subsystems through `app/services/print_interfaces.py`, and bundles
  `PrintWorkflowRunResult`.
- Every step returns or updates a **partial `PrintWorkflowRunResult`** so failures
  and stops are always representable. No big-bang integration.

Ground rules carried through every phase:

- Test-driven: write the failing test first, then the minimal code.
- Each wiring step preserves **legal transitions** (`can_transition`); illegal
  transitions are rejected and recorded in `TransitionCheckResult`.
- AI Generation is an **actuator only** — it never decides control flow.
- Subsystems are reached only through their `Protocol` interfaces, so the spine
  can be tested with stubs.

---

## Phase 0 — Thin Orchestration Spine Shell

- **Purpose:** Stand up the coordination loop with no subsystem logic: accept a step, validate the transition, record the attempt, return a partial result.
- **Consumes:** `WorkflowAdvanceRequest`.
- **Produces:** `WorkflowAdvanceResult` (wrapping a partial `PrintWorkflowRunResult`), `TransitionCheckResult`.
- **Why now:** Establishes the contract surface and transition guard everything else plugs into; lets subsequent phases be wired and tested incrementally.
- **Unit tests:**
  - Legal transition → `TransitionCheckResult.allowed = True`.
  - Illegal/skipped transition (e.g. `submitted -> approved`) → rejected, no subsystem invoked.
  - Terminal state in → no advance, `stopped = True`.
  - Human-review state in → `stopped = True` with `next_steps`.
  - Same `idempotency_key` replay → same run, no duplicated side effects.
- **Orchestration wiring tests:** With a no-op/stub subsystem registry, the shell calls nothing it shouldn't and returns a partial `PrintWorkflowRunResult` with correct `previous_state`/`current_state`.
- **Failure modes:** Illegal transition; unknown/missing current state; missing both `raw_submission` and `existing_run_result`; replay collisions.
- **Done criteria:** Spine validates transitions, records a `SubsystemExecutionRecord` placeholder when applicable, and always returns a partial `WorkflowAdvanceResult`. No subsystem logic present.

## Phase 1 — Normalization Service

- **Purpose:** Turn a `RawSubmission` into typed intent.
- **Consumes:** `RawSubmission`.
- **Produces:** `NormalizationResult` (carrying `DesignJob`).
- **Why now:** First real stage after intake; smallest contract that proves the spine→subsystem→partial-result loop end to end.
- **Unit tests:** Valid submission → `DesignJob` with resolved `ProductType`; ambiguous brief → `NEEDS_REVIEW` + `reasons`; failure → `design_job is None`. Must **not** populate any production requirement.
- **Orchestration wiring tests:** `normalization_pending -> normalized` advances and stores `NormalizationResult`; `-> normalization_needs_review` sets `stopped = True`; `-> normalization_failed` returns partial result, no spec call.
- **Failure modes:** Unparseable submission; unknown product; review-needed ambiguity.
- **Done criteria:** Normalization unit-tested in isolation and wired so the spine advances/stops on its result; spine still holds no normalization logic.

## Phase 2 — Specification Resolution Service

- **Purpose:** Resolve production requirements from a `DesignJob` + config.
- **Consumes:** `DesignJob` + Configuration.
- **Produces:** `PrintSpecification`.
- **Why now:** Spec is the precondition for every downstream stage (compliance, prompt, validation, packaging).
- **Unit tests:** Deterministic spec for a `(product_type, config_version)` pair; `config_source`/`config_version` recorded; unsupported product → `SPECIFICATION_FAILED`. Must be **downstream** of normalization (never produced by it).
- **Orchestration wiring tests:** `normalized -> specification_pending -> specification_resolved` stores `PrintSpecification`; `specification_failed -> failed` or `-> normalization_needs_review` returns partial result.
- **Failure modes:** Missing/invalid config; unsupported `ProductType`; config version drift.
- **Done criteria:** Spec resolution unit-tested and wired; spine carries spec into the run bundle without inspecting its contents.

## Phase 3 — Technical Compliance Service

- **Purpose:** Measure submitted-image readiness against the spec.
- **Consumes:** `DesignJob` + `PrintSpecification` + `ImageProperties`.
- **Produces:** `ComplianceResult`.
- **Why now:** Determines whether the run can shortcut to packaging or must adapt/generate.
- **Unit tests:** DPI below `min_dpi` flagged; color-mode mismatch → `ComplianceFinding`; `is_print_ready = True` only when all findings compliant. Measures only — no transforms.
- **Orchestration wiring tests:** `compliance_complete -> production_packaging_pending` when print-ready; `compliance_complete -> adaptation_planned` otherwise; `compliance_failed -> revision_requested`/`failed`.
- **Failure modes:** Missing image properties; un-measurable asset; hard failure mid-check.
- **Done criteria:** Compliance unit-tested and wired with both the shortcut and the adaptation branch covered.

## Phase 4 — Adaptation Planning Service

- **Purpose:** Define deterministic transformation intent to close compliance gaps.
- **Consumes:** `ComplianceResult` + `PrintSpecification` + `DesignJob`.
- **Produces:** `AdaptationPlan`.
- **Why now:** Bridges compliance gaps into either deterministic transforms or a generation requirement.
- **Unit tests:** Each non-compliant finding maps to a `TransformationStep`; `requires_generation = True` only when deterministic transforms are insufficient; plan is deterministic for identical inputs. No execution/model calls.
- **Orchestration wiring tests:** `adaptation_planned -> generation_pending` when `requires_generation`; `adaptation_planned -> validation_pending` when transforms suffice.
- **Failure modes:** Unaddressable gap; conflicting requirements; empty plan when gaps exist.
- **Done criteria:** Planner unit-tested and wired; spine branches on `requires_generation` without reading transform internals.

## Phase 5 — Prompt Construction Service

- **Purpose:** Build a strict, model-ready `GenerationRequest`.
- **Consumes:** `DesignJob` + `PrintSpecification` + `AdaptationPlan`.
- **Produces:** `GenerationRequest`.
- **Why now:** Generation cannot run without a spec-constrained, validated request; isolating it keeps generation a pure actuator.
- **Unit tests:** Pixel dimensions derive from spec mm + DPI; `color_mode`/`output_format` match the spec; required fields present and non-empty (not loose prompt text only).
- **Orchestration wiring tests:** Request assembled and attached to the run before the generation transition; spine does not mutate the request.
- **Failure modes:** Spec/plan mismatch; missing references; under-specified request.
- **Done criteria:** Constructor unit-tested and wired; `GenerationRequest` present in the partial run bundle prior to generation.

## Phase 6 — AI Generation Service

- **Purpose:** Execute the `GenerationRequest` and record what happened.
- **Consumes:** `GenerationRequest`.
- **Produces:** `GeneratedCandidate` + `ModelInvocationRecord`.
- **Why now:** Only meaningful once a strict request exists; kept late and isolated to enforce its actuator-only role.
- **Unit tests:** Each candidate links to `request_id`; `ModelInvocationRecord.status` reflects success/failure; `generated_candidate_ids` matches output; `retry_count`/`cost_estimate` recorded. **Must not** decide control flow, validate, approve, or package.
- **Orchestration wiring tests:** `generation_pending -> generation_running -> generation_complete` stores candidates + invocation records; `generation_failed -> generation_pending` (bounded retry) or `-> failed`.
- **Failure modes:** Provider error/timeout; zero candidates; partial batch; retry exhaustion.
- **Done criteria:** Generation unit-tested and wired as an actuator; spine owns the retry decision (the service does not).

## Phase 7 — Output Validation Service

- **Purpose:** Automated gate over generated candidates before approval.
- **Consumes:** `GeneratedCandidate` + `PrintSpecification` + `AdaptationPlan`.
- **Produces:** `ValidationResult`.
- **Why now:** Must sit between generation and human approval so reviewers only see spec-conformant candidates.
- **Unit tests:** Candidate violating spec excluded from `passed_candidate_ids`; empty pass set → `VALIDATION_FAILED` with `next_steps = 'regenerate'`; findings reference candidate ids. Does not approve.
- **Orchestration wiring tests:** `validation_pending -> validation_complete -> owner_review_pending`; `validation_failed -> generation_pending` (loop back) or `-> failed`.
- **Failure modes:** All candidates fail; un-measurable candidate; spec/plan unavailable.
- **Done criteria:** Validation unit-tested and wired; spine routes to review only on a non-empty pass set.

## Phase 8 — Approval Workflow Service

- **Purpose:** Route validated candidates to a human and record the decision.
- **Consumes:** `ValidationResult` + passed `GeneratedCandidate`s.
- **Produces:** `ApprovalPackage`, then `ApprovalDecision`.
- **Why now:** The human gate before production; the spine must **stop** here.
- **Unit tests:** `ApprovalPackage.candidate_ids ⊆` validation pass set; `record_approval_decision` captures `approver`/`decided_at`; `REVISION_REQUESTED` carries actionable `reasons`. No auto-approval.
- **Orchestration wiring tests:** `validation_complete -> owner_review_pending` sets `stopped = True`; resume with `approve` → `approved`; `reject` → `rejected`; `request_revision` → `revision_requested -> adaptation_planned` on resume.
- **Failure modes:** No reviewer routed; decision on a non-existent candidate; revision loop without changes.
- **Done criteria:** Approval unit-tested and wired; spine halts for the human and resumes correctly per decision.

## Phase 9 — Production Packaging Service

- **Purpose:** Assemble the final production-ready bundle.
- **Consumes:** `ApprovalDecision` + approved `GeneratedCandidate` + `PrintSpecification`.
- **Produces:** `ProductionPackage`.
- **Why now:** Final value-producing stage; only reachable after approval.
- **Unit tests:** Package assembled only when `ApprovalStatus.APPROVED`; `manifest` matches `PrintSpecification`; `candidate_id`/`decision_id` traceable. No re-validate/re-generate.
- **Orchestration wiring tests:** `approved -> production_packaging_pending -> production_package_created -> completed`; packaging failure → partial result with `reasons`.
- **Failure modes:** Packaging error; spec mismatch at assembly; missing approved candidate.
- **Done criteria:** Packaging unit-tested and wired; reaching `completed` produces a full `PrintWorkflowRunResult`.

## Phase 10 — End-to-End Demo Path

- **Purpose:** Prove the full happy path and representative stop/failure paths through the assembled spine.
- **Consumes:** A seed `RawSubmission`.
- **Produces:** A `completed` `PrintWorkflowRunResult` (and partial results for stop/failure scenarios).
- **Why now:** Phases 0–9 were each wired and tested as added, so this is a confirmation pass — not a first integration.
- **Unit tests:** N/A (covered per phase).
- **Orchestration wiring tests:** Full happy path `submitted → completed`; print-ready shortcut (`compliance_complete -> production_packaging_pending`); human-review stop + resume; a failure path returning a partial run; idempotent replay of the whole sequence.
- **Failure modes:** Cross-stage state drift; partial-result gaps; non-idempotent replay.
- **Done criteria:** Happy path and key stop/failure paths pass; every transition is legal per `print_state_machine.py`; spine still contains no subsystem logic.

---

## Invariants to assert in every phase

- Spine only sequences/validates/bundles — it never normalizes, resolves specs, inspects images, plans, prompts, calls models, validates, approves, or packages.
- Every transition is checked with `can_transition`; rejections are recorded, not forced.
- Each step returns or updates a **partial `PrintWorkflowRunResult`**.
- AI Generation remains an actuator; control authority stays in the spine.
- Subsystems are invoked only via their `Protocol` interfaces (stub-friendly).
