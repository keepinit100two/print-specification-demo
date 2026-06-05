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

### Subsystem sequence (current target architecture)

```
Normalize → Spec → Compliance → Adaptation
  → [Deterministic Transform]  OR  [Prompt → Generation]
  → Validation → Approval → Production
```

After adaptation planning, the run takes **one** remediation path:

| Branch | When | State path |
| --- | --- | --- |
| **Deterministic** | Plan has supported deterministic-only steps (`requires_generation=False`) | `ADAPTATION_PLANNED → DETERMINISTIC_TRANSFORM_PENDING → DETERMINISTIC_TRANSFORM_COMPLETE → VALIDATION_PENDING` |
| **AI** | Plan requires synthesis (`requires_generation=True`) | `ADAPTATION_PLANNED → GENERATION_PENDING → GENERATION_RUNNING → GENERATION_COMPLETE → VALIDATION_PENDING` |

**Implementation status (as of this doc):** Phases 0–7 are implemented and wired
in `print_orchestrator.py`. Phase 8 validation service is unit-tested;
orchestration wiring tests exist and are pending spine integration. Phases 9+
(approval, packaging, end-to-end demo) are not yet built.

---

## Phase 0 — Thin Orchestration Spine Shell ✅

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

## Phase 1 — Normalization Service ✅

- **Purpose:** Turn a `RawSubmission` into typed intent.
- **Consumes:** `RawSubmission`.
- **Produces:** `NormalizationResult` (carrying `DesignJob`).
- **Why now:** First real stage after intake; smallest contract that proves the spine→subsystem→partial-result loop end to end.
- **Unit tests:** Valid submission → `DesignJob` with resolved `ProductType`; ambiguous brief → `NEEDS_REVIEW` + `reasons`; failure → `design_job is None`. Must **not** populate any production requirement.
- **Orchestration wiring tests:** `normalization_pending -> normalized` advances and stores `NormalizationResult`; `-> normalization_needs_review` sets `stopped = True`; `-> normalization_failed` returns partial result, no spec call.
- **Failure modes:** Unparseable submission; unknown product; review-needed ambiguity.
- **Done criteria:** Normalization unit-tested in isolation and wired so the spine advances/stops on its result; spine still holds no normalization logic.

## Phase 2 — Specification Resolution Service ✅

- **Purpose:** Resolve production requirements from a `DesignJob` + config.
- **Consumes:** `DesignJob` + Configuration.
- **Produces:** `PrintSpecification`.
- **Why now:** Spec is the precondition for every downstream stage (compliance, prompt, validation, packaging).
- **Unit tests:** Deterministic spec for a `(product_type, config_version)` pair; `config_source`/`config_version` recorded; unsupported product → `SPECIFICATION_FAILED`. Must be **downstream** of normalization (never produced by it).
- **Orchestration wiring tests:** `normalized -> specification_pending -> specification_resolved` stores `PrintSpecification`; `specification_failed -> failed` or `-> normalization_needs_review` returns partial result.
- **Failure modes:** Missing/invalid config; unsupported `ProductType`; config version drift.
- **Done criteria:** Spec resolution unit-tested and wired; spine carries spec into the run bundle without inspecting its contents.

## Phase 3 — Technical Compliance Service ✅

- **Purpose:** Measure submitted-image readiness against the spec.
- **Consumes:** `DesignJob` + `PrintSpecification` + `ImageProperties`.
- **Produces:** `ComplianceResult`.
- **Why now:** Determines whether the run can shortcut to packaging or must adapt/generate.
- **Unit tests:** DPI below `min_dpi` flagged; color-mode mismatch → `ComplianceFinding`; `is_print_ready = True` only when all findings compliant. Measures only — no transforms.
- **Orchestration wiring tests:** `compliance_complete -> adaptation_planned` when not print-ready; adaptation skipped when print-ready; `compliance_failed` on missing spec or exceptions.
- **Failure modes:** Missing image properties; un-measurable asset; hard failure mid-check.
- **Done criteria:** Compliance unit-tested and wired with both the shortcut and the adaptation branch covered.

## Phase 4 — Adaptation Planning Service ✅

- **Purpose:** Define deterministic transformation intent to close compliance gaps.
- **Consumes:** `ComplianceResult` + `PrintSpecification` + `DesignJob`.
- **Produces:** `AdaptationPlan`.
- **Why now:** Bridges compliance gaps into either deterministic transforms or a generation requirement.
- **Unit tests:** Each non-compliant finding maps to a `TransformationStep`; `requires_generation = True` only when deterministic transforms are insufficient; plan is deterministic for identical inputs. No execution/model calls.
- **Orchestration wiring tests:** `compliance_complete -> adaptation_planned` when not print-ready; skipped when print-ready.
- **Failure modes:** Unaddressable gap; conflicting requirements; empty plan when gaps exist.
- **Done criteria:** Planner unit-tested and wired; spine branches on `requires_generation` without reading transform internals.

## Phase 5 — Prompt Construction Service ✅

- **Purpose:** Build a strict, model-ready `GenerationRequest`.
- **Consumes:** `DesignJob` + `PrintSpecification` + `AdaptationPlan`.
- **Produces:** `GenerationRequest`.
- **Why now:** Generation cannot run without a spec-constrained, validated request; isolating it keeps generation a pure actuator.
- **Unit tests:** Pixel dimensions derive from spec mm + DPI; `color_mode`/`output_format` match the spec; required fields present and non-empty (not loose prompt text only).
- **Orchestration wiring tests:** `adaptation_planned -> generation_pending` when `requires_generation`; stops without calling prompt when generation not required.
- **Failure modes:** Spec/plan mismatch; missing references; under-specified request.
- **Done criteria:** Constructor unit-tested and wired; `GenerationRequest` present in the partial run bundle prior to generation.

## Phase 6 — AI Generation Service ✅

- **Purpose:** Execute the `GenerationRequest` and record what happened.
- **Consumes:** `GenerationRequest`.
- **Produces:** `GeneratedCandidate` + `ModelInvocationRecord`.
- **Why now:** Only meaningful once a strict request exists; kept late and isolated to enforce its actuator-only role.
- **Unit tests:** Each candidate links to `request_id`; `ModelInvocationRecord.status` reflects success/failure; `generated_candidate_ids` matches output. Fake mode is default (no API key). **Must not** decide control flow, validate, approve, or package.
- **Orchestration wiring tests:** `generation_pending -> generation_running -> generation_complete` stores candidates + invocation records; `generation_failed` on missing inputs, zero candidates, or exceptions.
- **Supporting modules:**
  - `openai_image_client.py` — provider boundary (API key, `images.generate`, no workflow logic).
  - `generated_artifact_store.py` — optional `artifacts/generated/` persistence for OpenAI base64 payloads.
- **Environment:** `PRINT_GENERATION_MODE` (`fake`|`openai`), `OPENAI_API_KEY`, `PRINT_OPENAI_IMAGE_MODEL`.
- **Smoke test:** `python scripts/demo_generate_image.py` (loads `.env` when `python-dotenv` is available).
- **Failure modes:** Provider error/timeout; zero candidates; missing API key in openai mode.
- **Done criteria:** Generation unit-tested and wired as an actuator; spine owns retry/routing decisions (the service does not).

## Phase 7 — Deterministic Transform Service ✅

- **Purpose:** Execute supported deterministic `AdaptationPlan` steps without AI.
- **Consumes:** `DesignJob` + `PrintSpecification` + `AdaptationPlan`.
- **Produces:** `DeterministicTransformResult` / `TransformedAsset`.
- **Why now:** Closes the non-AI remediation branch before validation; keeps AI generation separate from resize/DPI/pad/crop/profile conversion intent.
- **Unit tests:** Supported steps -> `PASSED` placeholder assets; unsupported steps -> `NEEDS_REVIEW`; empty plan -> `SKIPPED`. No image libraries, no file I/O.
- **Orchestration wiring tests:** `deterministic_transform_pending -> deterministic_transform_complete`; unsupported step or exception -> `deterministic_transform_failed`.
- **Failure modes:** Missing inputs; unsupported transform types; service exception.
- **Done criteria:** Service unit-tested and wired; orchestrator does not perform image processing or inspect transform parameters.

## Phase 8 — Output Validation Service (service ✅, orchestration pending)

- **Purpose:** Automated **print-readiness** gate before human approval (Option A only).
- **Consumes:** `PrintSpecification` + `GeneratedCandidate` or `TransformedAsset`.
- **Produces:** `ValidationResult`.
- **Why now:** Must sit after either remediation branch so reviewers only see spec-measured outputs.
- **Scope:** Validates `width_px`, `height_px`, `min_dpi`, `file_format`, and `color_profile` when available. **Does not** judge creative quality, brand, copy, aesthetics, or approval.
- **Unit tests:** Compliant output -> `PASSED`; low DPI / bad format -> `FAILED`; missing metadata -> `NEEDS_REVIEW` (`MISSING_VALIDATION_METADATA`). No `ApprovalDecision`.
- **Orchestration wiring tests (written, spine integration pending):** From `generation_complete` or `deterministic_transform_complete`, macro `-> validation_pending -> validation_complete` on pass; `-> validation_failed` on fail, missing inputs, or exception. Subsystem name `PrintValidationService`.
- **Failure modes:** All outputs fail spec; un-measurable metadata; missing spec or output on run bundle.
- **Done criteria:** Validation unit-tested; orchestrator wires `validate_print_asset` and attaches `ValidationResult` to the run bundle.

## Phase 9 — Approval Workflow Service

- **Purpose:** Route validated candidates to a human and record the decision.
- **Consumes:** `ValidationResult` + passed outputs (`GeneratedCandidate` and/or `TransformedAsset` ids from validation pass set).
- **Produces:** `ApprovalPackage`, then `ApprovalDecision`.
- **Why now:** The human gate before production; the spine must **stop** here.
- **Unit tests:** `ApprovalPackage.candidate_ids ⊆` validation pass set; `record_approval_decision` captures `approver`/`decided_at`; `REVISION_REQUESTED` carries actionable `reasons`. No auto-approval.
- **Orchestration wiring tests:** `validation_complete -> owner_review_pending` sets `stopped = True`; resume with `approve` → `approved`; `reject` → `rejected`; `request_revision` → `revision_requested -> adaptation_planned` on resume.
- **Failure modes:** No reviewer routed; decision on a non-existent candidate; revision loop without changes.
- **Done criteria:** Approval unit-tested and wired; spine halts for the human and resumes correctly per decision.

## Phase 10 — Production Packaging Service

- **Purpose:** Assemble the final production-ready bundle.
- **Consumes:** `ApprovalDecision` + approved `GeneratedCandidate` + `PrintSpecification`.
- **Produces:** `ProductionPackage`.
- **Why now:** Final value-producing stage; only reachable after approval.
- **Unit tests:** Package assembled only when `ApprovalStatus.APPROVED`; `manifest` matches `PrintSpecification`; `candidate_id`/`decision_id` traceable. No re-validate/re-generate.
- **Orchestration wiring tests:** `approved -> production_packaging_pending -> production_package_created -> completed`; packaging failure → partial result with `reasons`.
- **Failure modes:** Packaging error; spec mismatch at assembly; missing approved candidate.
- **Done criteria:** Packaging unit-tested and wired; reaching `completed` produces a full `PrintWorkflowRunResult`.

## Phase 11 — End-to-End Demo Path

- **Purpose:** Prove the full happy path and representative stop/failure paths through the assembled spine.
- **Consumes:** A seed `RawSubmission`.
- **Produces:** A `completed` `PrintWorkflowRunResult` (and partial results for stop/failure scenarios).
- **Why now:** Prior phases were each wired and tested as added, so this is a confirmation pass — not a first integration.
- **Unit tests:** N/A (covered per phase).
- **Orchestration wiring tests:** Full happy path `submitted → completed`; print-ready shortcut (`compliance_complete -> production_packaging_pending`); human-review stop + resume; a failure path returning a partial run; idempotent replay of the whole sequence.
- **Failure modes:** Cross-stage state drift; partial-result gaps; non-idempotent replay.
- **Done criteria:** Happy path and key stop/failure paths pass; every transition is legal per `print_state_machine.py`; spine still contains no subsystem logic.

---

## Invariants to assert in every phase

- Spine only sequences/validates/bundles — it never normalizes, resolves specs, inspects images, plans, prompts, calls models, executes transforms, validates, approves, or packages.
- Deterministic transform and AI generation are separate branches after adaptation; the spine routes on plan flags, not on image bytes.
- Every transition is checked with `can_transition`; rejections are recorded, not forced.
- Each step returns or updates a **partial `PrintWorkflowRunResult`**.
- AI Generation remains an actuator; control authority stays in the spine.
- Subsystems are invoked only via their `Protocol` interfaces (stub-friendly).
