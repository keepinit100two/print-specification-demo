# Print Orchestration Contract

Defines how the **Orchestration Spine** coordinates the print-specification
workflow. The spine sequences subsystems, validates state transitions, enforces
idempotency, and aggregates results — but performs **no subsystem work** itself.

References:
- States/transitions: `app/domain/print_state_machine.py` (`PrintWorkflowState`, `LEGAL_TRANSITIONS`, helpers).
- Stage labels & run bundle: `app/domain/print_schemas.py` (`PrintWorkflowStage`, `PrintWorkflowRunResult`).
- Subsystem interfaces: `app/services/print_interfaces.py`.

---

## 1. Orchestration responsibility

The orchestration spine:

- **Owns workflow progression** — advances a run through `PrintWorkflowStage`/`PrintWorkflowState`.
- **Validates legal state transitions** — every move is checked with `can_transition(from, to)` from `print_state_machine.py`; illegal transitions are rejected, not forced.
- **Enforces idempotency** — a given `idempotency_key` produces/returns one run; replays reuse the existing `PrintWorkflowRunResult` rather than re-executing side effects.
- **Calls subsystem interfaces** — invokes the typed Protocols in `print_interfaces.py` (one subsystem per stage) and stores their outputs.
- **Bundles `PrintWorkflowRunResult`** — accumulates each stage output (plus `model_invocations`, `approval_package`) into the run record.
- **Stops on human review states** — yields control when a human decision is required (see §6).
- **Stops on terminal states** — halts at `completed` / `failed` / `cancelled`.
- **Records stage/audit events** — emits observation events for each transition via the audit subsystem (read-only; see §2).

## 2. Orchestration must not do

The spine coordinates; it never performs subsystem work. It must **not**:

- Normalize raw submissions.
- Resolve specifications.
- Inspect image dimensions / properties.
- Create adaptation plans.
- Build prompts / generation requests.
- Call AI models directly.
- Validate generated outputs.
- Approve candidates.
- Package production files.

Each of these belongs to its owning subsystem (`print_interfaces.py`). The spine
only decides *whether and when* to call them and *whether* a transition is legal.

## 3. Orchestration input contract (conceptual)

A single step into the orchestrator conceptually receives:

| Field | Meaning |
| --- | --- |
| `run_id` | Identity of the workflow run being advanced. |
| `idempotency_key` | Key guaranteeing exactly-once progression for a step/operation. |
| `current_state` | The run's current `PrintWorkflowState` (transition source). |
| `requested_operation` | The intended next step (e.g. "advance", "approve", "cancel", "retry"). |
| `RawSubmission` **or** existing `PrintWorkflowRunResult` | A new run starts from a `RawSubmission`; an in-flight run resumes from its prior `PrintWorkflowRunResult`. |
| `available_subsystem_outputs` | Already-produced contracts (e.g. `DesignJob`, `PrintSpecification`, candidates) needed by the next subsystem call. |

## 4. Orchestration output contract (conceptual)

Every step returns a `PrintWorkflowRunResult`, surfacing:

| Field | Meaning |
| --- | --- |
| `PrintWorkflowRunResult` | The bundled run record (all stage outputs gathered so far). |
| `stage` | Final/current `PrintWorkflowStage`. |
| `state` | Final/current `PrintWorkflowState`. |
| `status` | Overall `ResultStatus` for the run. |
| `reasons` | Why the run reached this state (especially on stop/failure). |
| `warnings` | Non-blocking concerns. |
| `next_steps` | What the caller/operator should do next (e.g. "await owner review", "retry generation"). |

On a stop or failure, the result is **partial but valid**: produced stage outputs
are present; unreached ones remain `None`/empty.

## 5. Subsystem call sequence

### Shared prefix (every run)

```
RawSubmission
  -> NormalizationService.normalize_submission           -> NormalizationResult (DesignJob)
  -> SpecificationResolutionService.resolve_specification -> PrintSpecification
  -> TechnicalComplianceService.evaluate_compliance      -> ComplianceResult
  -> AdaptationPlanningService.create_adaptation_plan    -> AdaptationPlan
```

When assets are already print-ready at compliance, adaptation may be skipped
(print-ready shortcut — future packaging path).

### Deterministic branch (no AI)

When the plan is satisfied by supported deterministic steps only:

```
  -> DeterministicTransformService.execute_deterministic_transforms
        -> DeterministicTransformResult / TransformedAsset
  -> [VALIDATION_PENDING]
  -> PrintValidationService.validate_print_asset         -> ValidationResult
```

**State path:**

`ADAPTATION_PLANNED → DETERMINISTIC_TRANSFORM_PENDING → DETERMINISTIC_TRANSFORM_COMPLETE → VALIDATION_PENDING → VALIDATION_COMPLETE`

### AI branch (generation actuator)

When `AdaptationPlan.requires_generation` is true:

```
  -> PromptConstructionService.build_generation_request  -> GenerationRequest
  -> AIGenerationService.generate_candidates             -> GeneratedCandidate + ModelInvocationRecord
  -> [VALIDATION_PENDING]
  -> PrintValidationService.validate_print_asset          -> ValidationResult
```

**State path:**

`ADAPTATION_PLANNED → GENERATION_PENDING → GENERATION_RUNNING → GENERATION_COMPLETE → VALIDATION_PENDING → VALIDATION_COMPLETE`

Generation runs in **fake** mode by default (`PRINT_GENERATION_MODE=fake`) for
tests and offline work. **OpenAI** mode delegates to `openai_image_client` and may
persist bytes via `generated_artifact_store` under `artifacts/generated/`.

### Post-validation (not yet wired)

```
  -> ApprovalWorkflowService.create_approval_package     -> ApprovalPackage
  -> [STOP: owner_review_pending — human decides]
  -> ApprovalWorkflowService.record_approval_decision  -> ApprovalDecision
  -> ProductionPackagingService.create_production_package -> ProductionPackage
  -> completed
```

The spine performs the corresponding legal transition before/after each call.
Macro steps (generation, validation) record intermediate `*_PENDING` /
`*_RUNNING` transitions in `transition_checks`.

### Output contracts: GeneratedCandidate vs TransformedAsset

| Output | Subsystem | Used on branch |
| --- | --- | --- |
| `GeneratedCandidate` | AI Generation | AI branch only |
| `TransformedAsset` | Deterministic Transform | Deterministic branch only |

`ModelInvocationRecord` accompanies AI generation for observability (provider,
model, latency, errors). It does not authorize workflow transitions.

### Validation boundary

`PrintValidationService.validate_print_asset` measures **print-readiness only**
(Option A): pixel dimensions, DPI, file format, and color profile when both
sides provide one. It does not approve, score creative quality, or replace human
review. Orchestration wiring from `GENERATION_COMPLETE` and
`DETERMINISTIC_TRANSFORM_COMPLETE` is defined by tests and pending spine
integration.

### Current spine wiring status

| Phase | Entry state | Service | Status |
| --- | --- | --- | --- |
| 1 | `NORMALIZATION_PENDING` | Normalization | Wired |
| 2 | `NORMALIZED` | Specification | Wired |
| 3 | `SPECIFICATION_RESOLVED` | Compliance | Wired |
| 4 | `COMPLIANCE_COMPLETE` | Adaptation | Wired |
| 5 | `ADAPTATION_PLANNED` | Prompt construction | Wired (AI branch) |
| 6 | `GENERATION_PENDING` | AI generation | Wired |
| 7 | `DETERMINISTIC_TRANSFORM_PENDING` | Deterministic transform | Wired |
| 8 | `GENERATION_COMPLETE` / `DETERMINISTIC_TRANSFORM_COMPLETE` | Validation | Service implemented; spine wiring pending |

## 6. Stop boundaries

Orchestration must stop advancing and return control when the run reaches:

| State | Why orchestration stops |
| --- | --- |
| `normalization_needs_review` | Human must clarify/repair the submission before normalization can proceed. |
| `owner_review_pending` | Human approval is required before production. |
| `revision_requested` | Human asked for changes; a human/external trigger must re-enter the loop. |
| `completed` | Terminal success — nothing further to do. |
| `failed` | Terminal failure — nothing further to do. |
| `cancelled` | Terminal cancellation — nothing further to do. |

Human-review stops are identified via `is_human_review_state(...)`; terminal
stops via `is_terminal_state(...)`. At a stop, the spine returns the current
`PrintWorkflowRunResult` with `next_steps` describing the awaited action.

## 7. Failure boundaries

| Failure kind | Meaning | Orchestrator behavior |
| --- | --- | --- |
| **Hard failure** | A subsystem raises/returns a non-recoverable error mid-stage. | Transition to the appropriate failure state; do not call the next subsystem; return partial result. |
| **Review-needed failure** | Output is incomplete/ambiguous and needs a human (`normalization_needs_review`, `revision_requested`). | Stop at the human-review state (see §6); not a terminal failure. |
| **Retryable failure** | A stage-level `*_failed` state that may re-enter the pipeline (`is_retryable_state(...)`): `generation_failed`, `validation_failed`, `normalization_failed`, `specification_failed`, `compliance_failed`. | Eligible for a bounded retry (see §8) per the legal transition back into the pipeline. |
| **Terminal failure** | `failed` (or `cancelled`). | Halt; no further subsystem calls; return final result. |

`is_failure_state(...)` covers both stage-level failures and terminal `failed`;
`is_retryable_state(...)` is the strict subset that can re-enter the pipeline.

## 8. Retry policy boundary

- The orchestrator **may retry only retryable states** as defined by `is_retryable_state(...)` in `print_state_machine.py`, and only along a transition that `can_transition(...)` permits (e.g. `generation_failed -> generation_pending`).
- **Subsystem-specific retry rules** (backoff, max attempts per provider, timeouts) belong in **subsystem configuration**, supplied by the Configuration subsystem — not hard-coded in the spine.
- **No infinite retries.** A bounded attempt budget (from config) must exist; once exhausted, the run transitions to terminal `failed` with `reasons`.

## 9. Future test candidates

- Orchestrator **rejects illegal transitions** (uses `can_transition`; refuses skips like `submitted -> approved`).
- Orchestrator **stops at human review** states (`normalization_needs_review`, `owner_review_pending`, `revision_requested`) without calling the next subsystem.
- Orchestrator **stops at terminal states** (`completed`, `failed`, `cancelled`) and makes no further calls.
- Orchestrator **does not call the next subsystem after a failure**.
- Orchestrator **returns a partial `PrintWorkflowRunResult` on failure** (produced stages present, unreached stages empty).
- Orchestrator **never performs subsystem work directly** (e.g. with stub subsystems, all domain outputs originate from subsystem calls, never from the spine).
- Orchestrator **honors the retry boundary** (retries only retryable states; stops after the configured attempt budget; never loops infinitely).
- Orchestrator **is idempotent** (same `idempotency_key` returns the same run without re-executing side effects).
