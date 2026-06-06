# Architecture Review

A retrospective evaluation of the print-specification workflow system **as it
exists today** — after full workflow implementation, orchestration wiring, and
end-to-end smoke testing.

**Audience:** future engineers, technical stakeholders, and future-you revisiting
this repo in six months.

**Companion docs:** `docs/PRINT_WORKFLOW_CONTRACT_MAP.md`,
`docs/PRINT_ORCHESTRATION_CONTRACT.md`, `docs/IMPLEMENTATION_SEQUENCE.md`,
`docs/PRODUCTIONIZATION_GAP_ANALYSIS.md`.

This document does not walk through code line-by-line. It answers: *How good is
this architecture? What tradeoffs were made? What debt remains? What is sacred?
What must change before production?*

---

## 1. Executive Assessment

### Current state

| Dimension | Status |
| --- | --- |
| Workflow completeness | **Done** — `SUBMITTED` through `COMPLETED`, including dual remediation branches, human approval, production packaging, and completion routing |
| Test suite | **233 tests passing** — per-subsystem unit tests, orchestration wiring tests per phase, state-machine tests, end-to-end smoke |
| End-to-end path | **Implemented** — AI remediation branch proven in `test_end_to_end_happy_path_smoke` with monkeypatched generation (no live OpenAI) |
| Architecture maturity | **High** for workflow design — contracts, state machine, subsystem boundaries, orchestration spine |
| Operational maturity | **Low** — no durable run store, no print-workflow API, no review delivery, no production telemetry export |

### Honest assessment

This repo is a **successful workflow MVP**, not a **deployed production service**.

The hard problem — defining a correct, testable, multi-stage print pipeline with
controlled AI usage — has been solved in architecture and code. The easier-sounding
but operationally critical problem — running that pipeline reliably for real users
at scale — has been deliberately deferred.

That deferral was the right tradeoff for this phase. The system proves the
workflow is sound before investing in databases, queues, and portals. A senior
reviewer would likely approve the **workflow architecture** while flagging the
**platform gap** as the next major investment.

**Grade (workflow logic):** A  
**Grade (production operations):** Incomplete by design  
**Grade (overall as a learning/reference system):** A-

---

## 2. Architectural Strengths

### Contract-first design

Every stage has explicit input and output types in `app/domain/print_schemas.py`.
Subsystems speak in contracts, not loose dictionaries. This makes failures
representable (`PrintWorkflowRunResult` with optional fields), enables partial
runs, and gives future integrators a stable vocabulary.

**Why it matters:** Production systems fail at boundaries. Typed contracts force
those boundaries to be visible and testable early.

### Deterministic orchestration spine

`print_orchestrator.py` sequences subsystems and validates transitions. It does
not embed normalization rules, compliance math, or generation logic. Each advance
step is a pure function of request inputs plus subsystem outputs.

**Why it matters:** The spine can be wrapped by an API, queue worker, or test
harness without rewriting business logic.

### AI as actuator, not controller

`AIGenerationService` executes a `GenerationRequest` and returns
`GeneratedCandidate` + `ModelInvocationRecord`. It never chooses the next state,
never approves, never validates. The orchestrator routes to generation only when
`AdaptationPlan.requires_generation` is true.

**Why it matters:** AI unpredictability is isolated. The workflow remains
governed by the state machine even when the model misbehaves.

### Clear subsystem ownership

Eleven stage services map cleanly to pipeline phases. `print_interfaces.py` defines
Protocols that document what each subsystem must and must not do. Orchestration
records which subsystem ran via `SubsystemExecutionRecord.subsystem_name`.

**Why it matters:** Teams can own services independently. Changes stay localized.

### Strong auditability (structural)

Each orchestration step can return:

- `transition_checks` — every state movement, including macro `*_PENDING` legs
- `subsystem_records` — latency, inputs/outputs by contract id, error codes
- `ModelInvocationRecord` — provider, model, timing for AI calls

The *shape* of audit data is production-grade even though persistence is not yet
wired.

**Why it matters:** When something goes wrong in production, you need to know
*what step ran, with what inputs, for how long*. The MVP already produces that
data — it just does not store it yet.

### Test-driven development process

Each phase followed: failing unit tests → minimal service → failing orchestration
tests → spine wiring → green suite. The end-to-end smoke test was added last as
confirmation, not as the first integration attempt.

**Why it matters:** The 233 tests are evidence of behavioral contracts, not
accidental coverage. Refactoring for production has a safety net.

### State-machine governance

`print_state_machine.py` is the single authority on legal transitions. The
orchestrator calls `can_transition` and records results; it does not invent
shortcuts. Human-review and terminal states have explicit stop semantics.

**Why it matters:** Prevents “helpful” code paths that bypass approval or skip
validation — a common failure mode in workflow systems.

### Separation of orchestration vs business logic

| Layer | Knows about |
| --- | --- |
| Orchestrator | States, legal transitions, which subsystem to call, bundling results |
| Subsystems | Domain rules for one stage only |

**Why it matters:** This separation is the core reason the MVP can evolve into a
platform without rewriting the workflow.

---

## 3. Most Important Architectural Decisions

### 1. PrintSpecification separated from Normalization

**Decision:** `NormalizationResult` / `DesignJob` capture *intent* (product type,
brief, quantity). `PrintSpecification` is resolved later from `DesignJob` +
configuration.

**Why it improved the system:** Normalization stays faithful to user input.
Production requirements (DPI, bleed, formats) come from shop policy, not from
parsing free text. The same design job can be re-resolved if config changes
without re-normalizing.

### 2. Compliance separated from Adaptation

**Decision:** `TechnicalComplianceService` only *measures* submitted assets.
`AdaptationPlanningService` only *plans* remediation. Neither executes fixes.

**Why it improved the system:** Measurement and planning are independently
testable. Compliance can complete with `is_print_ready=False` without silently
mutating assets. Adaptation plans are reviewable artifacts before any work runs.

### 3. Deterministic Transform branch

**Decision:** After adaptation planning, supported pixel/format operations run
through `DeterministicTransformService` → `TransformedAsset` without AI.

**Why it improved the system:** Most print gaps (resize, DPI metadata, format)
should not require generative models. A dedicated branch keeps cost, latency, and
non-determinism out of the common path.

### 4. AI Generation branch

**Decision:** Generation runs only when `requires_generation=True` (e.g.
`UPSCALE` in the MVP planner). Prompt construction builds a strict
`GenerationRequest`; generation is a replaceable actuator.

**Why it improved the system:** AI becomes an escalation path, not the default.
Provider choice (`fake` vs OpenAI) is an implementation detail behind one interface.

### 5. Validation convergence point

**Decision:** Both branches rejoin at `PrintValidationService` before approval.
One validation gate measures print-readiness (Option A) regardless of output type.

**Why it improved the system:** Reviewers and packaging always see spec-measured
outputs. You do not maintain two approval paths for AI vs deterministic artifacts.

### 6. Human approval boundary

**Decision:** `ApprovalWorkflowService` routes to `OWNER_REVIEW_PENDING` and
stops. `ApprovalDecisionService` records the human outcome separately. Packaging
never runs without `ApprovalStatus.APPROVED`.

**Why it improved the system:** Human judgment is explicit in the state machine,
not implied by automation. Rejected and revision-requested paths are first-class.

### 7. Production packaging boundary

**Decision:** `ProductionPackagingService` assembles `ProductionPackage` (URIs +
manifest) but does not print, ship, or re-validate. Completion routing is a
separate no-subsystem step to `COMPLETED`.

**Why it improved the system:** “Ready for print shop” is a distinct lifecycle
stage from “job done.” Physical fulfillment stays outside the workflow core.

---

## 4. Technical Debt

Debt here means **known simplifications** that are acceptable for MVP correctness
but should be addressed before or during productionization. None of them invalidate
the architecture; they add friction or risk at scale.

### `validated_candidate_ids` naming

| | |
| --- | --- |
| **Current state** | `ValidationResult` uses `validated_candidate_ids` / `passed_candidate_ids` for both `GeneratedCandidate` and `TransformedAsset` output ids. |
| **Risk** | Integrators misread logs and APIs; deterministic outputs look like AI candidates. |
| **Recommended fix** | Neutral output id fields or typed `ValidatedOutputRef`. Deprecate old names with migration. |

### Approval metadata payload

| | |
| --- | --- |
| **Current state** | Human decisions arrive via `WorkflowAdvanceRequest.metadata["approval_decision"]`. `approver` is caller-supplied text. |
| **Risk** | Spoofed approvals; weak API contracts; hard to enforce authorization at the boundary. |
| **Recommended fix** | Typed `ApprovalDecisionInput` on the request; bind approver to authenticated identity. |

### `deterministic_transform` missing on run bundle

| | |
| --- | --- |
| **Current state** | `DeterministicTransformResult` exists in schemas but is not a field on `PrintWorkflowRunResult`. Orchestrator uses `getattr` fallback; tests use placeholder candidates. |
| **Risk** | Deterministic branch runs are not fully reconstructable from the bundle; packaging/validation must guess output location. |
| **Recommended fix** | Add `deterministic_transform` to `PrintWorkflowRunResult`; populate on branch completion; remove placeholder path. |

### `ContractProvenance` underused

| | |
| --- | --- |
| **Current state** | Schemas include `provenance` on most contracts; services rarely populate `source_id`, `derived_from_ids`, `config_version`. |
| **Risk** | Weak lineage for compliance disputes and debugging (“which config version produced this spec?”). |
| **Recommended fix** | Spine or shared helper sets provenance on every subsystem output. |

### Idempotency key not enforced (print workflow)

| | |
| --- | --- |
| **Current state** | `WorkflowAdvanceRequest.idempotency_key` is required and echoed but not deduplicated. Tier-1 ingest has SQLite idempotency; print workflow does not. |
| **Risk** | Retried HTTP advances could double-call OpenAI or double-package. |
| **Recommended fix** | Persist `(run_id, idempotency_key) → WorkflowAdvanceResult` in Run Store. |

### Caller-supplied canonical state

| | |
| --- | --- |
| **Current state** | Caller passes both `current_state` and `existing_run_result`. Spine trusts `current_state` as transition source. |
| **Risk** | State drift if caller bundle and declared state disagree. |
| **Recommended fix** | Run Store owns state; API derives `current_state` from persisted record. |

### Deterministic transform is placeholder execution

| | |
| --- | --- |
| **Current state** | `DeterministicTransformService` records intent and placeholder URIs; no real image processing. |
| **Risk** | Production would ship metadata-only transforms unless replaced with real execution or external tool handoff. |
| **Recommended fix** | Integrate real transform worker or document handoff to prepress system — a product decision, not an orchestration flaw. |

### In-process-only execution model

| | |
| --- | --- |
| **Current state** | Caller loops `advance_workflow()` and holds the run bundle. |
| **Risk** | Not debt in the workflow sense — it is an explicit MVP boundary — but it blocks multi-user production until Run Store exists. |
| **Recommended fix** | See `docs/PRODUCTIONIZATION_GAP_ANALYSIS.md` Priority 1. |

---

## 5. What Should Never Change

These principles are the architectural “constitution.” Production should add
platform layers **around** them, not erode them.

### Orchestrator never performs subsystem work

The spine sequences, validates transitions, calls services, and bundles results.
It must not normalize briefs, measure DPI, build prompts, call OpenAI, or assemble
manifests inline. If that rule breaks, tests become meaningless and ownership
blurs.

### AI never controls workflow

Models execute requests and return candidates. They do not choose branches, skip
validation, approve outputs, or complete runs. `ModelInvocationRecord` is
observability, not authority.

### Validation remains separate from approval

`PrintValidationService` measures print-readiness only. It does not judge
marketing quality or replace human sign-off. `ApprovalDecisionService` records
human judgment. Merging these stages would automate trust in ways the business
has not agreed to.

### Subsystems own business logic

Each stage service owns its rules and contracts. The orchestrator passes inputs
and routes on outputs — it does not inspect image bytes or reinterpret compliance
findings.

### Contracts remain explicit

Stage inputs and outputs stay typed Pydantic models (or successor schemas). Avoid
replacing contracts with untyped JSON blobs at subsystem boundaries.

### State machine remains the transition authority

All advances must be legal per `LEGAL_TRANSITIONS`. No admin “force state”
shortcuts without equally explicit, audited escape hatches.

### Human review stops are real stops

`OWNER_REVIEW_PENDING`, `NORMALIZATION_NEEDS_REVIEW`, and `REVISION_REQUESTED`
mean the automation boundary has been reached. Production adds delivery mechanisms;
it should not auto-approve to avoid building a portal.

### Production packaging does not imply physical print

`ProductionPackage` means “bundle ready for the print shop,” not “printed and
shipped.” Fulfillment stays downstream.

---

## 6. What Will Change In Production

These are **platform concerns** — infrastructure wrapped around the workflow —
not corrections to workflow logic.

| Change | Why it is platform, not workflow |
| --- | --- |
| **Run Store** | Durability, crash recovery, and multi-caller access do not alter stage rules. Same `advance_workflow()` inside a load/save shell. |
| **API layer** | HTTP/auth validation translates to `WorkflowAdvanceRequest`. Spine stays transport-agnostic. |
| **Review delivery** | Email, portal, and signed links notify humans at existing stop states. Approval recording already exists. |
| **Object storage** | Artifacts need durable bytes; contracts already use URI references (`artifact://...`). |
| **Metrics / tracing** | Export existing `SubsystemExecutionRecord` and transitions to Prometheus/OTel. Shape is already there. |
| **Queue workers** | Long-running generation runs async; worker calls one orchestration step and saves. Branch logic unchanged. |

**Key insight:** Production changes **how runs are invoked and stored**, not
**what the pipeline means**. That separation is intentional and should be preserved.

See `docs/PRODUCTIONIZATION_GAP_ANALYSIS.md` for prioritized productionization order.

---

## 7. Scalability Assessment

Current architecture: single process, synchronous subsystem calls, caller-driven
loop, in-memory run bundle.

### ~10 concurrent runs

**Verdict: Fine for MVP/demo.**

Pure Python orchestration and fake generation handle this in one process. No
shared state conflicts if each run is independent in memory.

### ~100 concurrent runs

**Verdict: Stressful without changes.**

Bottlenecks emerge:

- **Caller-held state** — no central visibility; support cannot inspect runs.
- **Synchronous OpenAI calls** — generation latency blocks the caller thread.
- **Local artifact store** — disk I/O and filename collisions under parallel writes.
- **No backpressure** — unlimited concurrent generation could exhaust API quota.

Architecture still *correct*; platform layers become necessary.

### ~1,000 concurrent runs

**Verdict: Requires queue + Run Store + horizontal workers.**

Bottlenecks:

- **Single-process ceiling** — CPU and memory for Python workers.
- **OpenAI rate limits** — need queue, throttling, and retry discipline.
- **Approval backlog** — human review stops accumulate; need SLA monitoring and routing.

Workflow logic does not need redesign; **execution model** must change.

### ~10,000 concurrent runs

**Verdict: Full platform architecture required.**

Additional bottlenecks:

- **Run Store write throughput** — append-heavy step log needs sharding or event streaming.
- **Object storage** — local filesystem is eliminated.
- **Multi-tenant isolation** — auth, quotas, and per-tenant rate limits.
- **Observability at scale** — sampling, aggregation, alert budgets.

The **state machine and subsystem decomposition** still scale conceptually — you
scale **workers and storage**, not **workflow rules per machine**.

---

## 8. Maintainability Assessment

### Replace OpenAI

**Ease: High.**

`openai_image_client.py` is an isolated provider boundary. `print_generation.py`
switches on `PRINT_GENERATION_MODE`. Tests monkeypatch `generate_candidates`.
Adding another provider means a new client module and a branch in the generation
service — orchestrator unchanged.

### Add new print products

**Ease: Medium.**

`ProductType` enum and `_PRODUCT_RULES` in `print_specification.py` drive
dimensions, formats, and color rules. A new product requires: enum entry, rule
block, normalization mapping for free-text product names, and tests. No
orchestrator change if the pipeline shape is unchanged.

### Add new transform types

**Ease: Medium–High for planning; Medium for execution.**

`TransformationType` and adaptation planner map findings to steps. Deterministic
service has supported/unsupported sets. New deterministic types: extend planner +
transform executor. Generation-required types: add to `_GENERATION_TRANSFORMS`.
Orchestrator routes on existing `requires_generation` flag.

### Add new validation rules

**Ease: High within Option A scope.**

`print_validation.py` is self-contained. New print-readiness checks (e.g. bleed
metadata) go here with new tests. Expanding into creative/brand validation would
be a **product/architecture decision** — approval stage exists for that class of
judgment.

### Add new delivery channels (review notifications, webhooks)

**Ease: High for workflow; Medium for platform.**

New channels attach at **stop states** and **API boundaries**, not inside
subsystems. Webhook on `OWNER_REVIEW_PENDING` is an integration layer concern.
Core approval recording stays the same.

### Overall maintainability

**Good.** Small files, one service per stage, heavy tests, clear docs. A new
engineer can locate behavior by stage name. Main cognitive load is the
orchestrator’s phase ordering — mitigated by phase comments and wiring tests.

---

## 9. Stakeholder Value Assessment

*For non-engineering readers: what this system demonstrates in business terms.*

### Auditability

Every step can answer: *What happened? Who approved it? Which output was
validated?* The structure exists today; production adds long-term storage. This
supports dispute resolution and regulatory questions about automated decisions.

### Repeatability

The same submission type and shop configuration produce the same specification
and the same adaptation plan. AI can be run in deterministic fake mode for
training and demos. The pipeline is explainable stage by stage — not a black box.

### Visibility

Operators can see where a job is stuck (normalization review, owner review,
validation failed) because state names map to business milestones. Production
dashboards would translate `PrintWorkflowState` into human language.

### Controlled AI usage

AI runs only when the plan says generation is required, and only with a
pre-built request derived from print specifications — not from ad-hoc prompts.
Default mode avoids API cost entirely. This demonstrates **governed AI** rather
than **AI-driven process**.

### Production readiness trajectory

The MVP is not deployable as-is to customers, but it is **not a prototype that
must be thrown away**. The next investments (database, API, notifications) attach
to a verified core. Stakeholders can fund productionization with confidence that
workflow risk has been retired.

**Business bottom line:** The repo proves you can run a print job from submission
to “ready for production” with explicit human approval and measurable quality
gates — a credible foundation for a real product, not just a demo script.

---

## 10. Final Verdict

### If a senior engineer reviewed this repo today

#### What would be praised

- **Disciplined workflow modeling** — state machine, typed contracts, dual branches
  converging at validation, human gate before packaging.
- **Orchestration hygiene** — spine does not absorb subsystem logic; AI is an
  actuator; extensive wiring tests per phase.
- **Test culture** — 233 tests, TDD sequence documented, end-to-end smoke as
  capstone.
- **Documentation** — contract map, orchestration contract, implementation
  sequence, gap analysis; rare in MVPs.
- **Pragmatic MVP boundaries** — fake generation default, placeholder deterministic
  transforms, metadata-based approval acknowledged as interim.

#### What would be criticized

- **No persistence** — “Where is the run after the process crashes?”
- **No API for the print workflow** — core value is library-only.
- **Approval trust model** — client-supplied `approver` would not pass security review.
- **Schema loose ends** — `validated_candidate_ids`, missing `deterministic_transform`
  on bundle.
- **Operational blind spot** — metrics and alerts not wired despite good record shapes.
- **Tier-1 control plane vs print workflow** — two systems in one repo; newcomers
  must learn which entry point matters.

#### Would the architecture be approved?

**Yes — as a workflow foundation.**

A senior engineer would likely **approve merging and building production platform
layers on top**, with conditions:

1. Run Store and API before customer-facing launch.
2. Typed approval input and authenticated approver before real human review.
3. Close schema debt (`deterministic_transform` on bundle, output id naming) before
   external integrators depend on JSON shape.
4. Do **not** refactor the state machine or collapse subsystems for “simplicity.”

### Honest conclusion

Six months from now, you should open this repo and think:

> *The workflow was done right. The platform was intentionally not done yet.*

That is a **good** place to be — better than a production deployment with an
untested workflow, or a beautiful architecture that never completed the pipeline.

**Architecture quality:** Strong.  
**Production readiness:** Platform work remains.  
**Recommendation:** Preserve the core; productionize in the order documented in
`PRODUCTIONIZATION_GAP_ANALYSIS.md` — persistence first, then API, then human
delivery, then observability and scale.

The system is worth keeping, worth extending, and worth showing to stakeholders
as evidence that the hard workflow design is solved.

---

*Review date reflects: 233 tests passing, Phases 1–11 orchestration complete,
end-to-end happy path smoke test green.*
