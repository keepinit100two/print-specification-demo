# Productionization Gap Analysis

This document analyzes what would be required to evolve the completed
print-specification MVP into a production-ready orchestration platform. It does
not propose new product capabilities. It answers: **what production
responsibilities are missing today?**

References: `docs/PRINT_WORKFLOW_CONTRACT_MAP.md`,
`docs/PRINT_ORCHESTRATION_CONTRACT.md`, `docs/IMPLEMENTATION_SEQUENCE.md`,
`docs/DEPLOYMENT.md`.

---

## 1. Executive Summary

### Current MVP status

| Item | Status |
| --- | --- |
| Workflow completeness | **Complete** — all stages from `SUBMITTED` through `COMPLETED` are implemented and orchestrated in `print_orchestrator.py` |
| Test coverage | **233 tests passing** — unit tests per subsystem plus orchestration wiring and end-to-end smoke |
| End-to-end path | **Operational in-process** — `test_end_to_end_happy_path_smoke` exercises the full AI remediation branch with monkeypatched generation (no OpenAI) |

The MVP demonstrates **workflow correctness** and **subsystem architecture**:
typed contracts, a legal state machine, separation of concerns across eleven stage
services, and an orchestration spine that sequences work without embedding
domain logic.

### What production requires beyond the MVP

Production operation is not primarily about adding workflow stages. It requires
integrating the completed workflow into durable, secure, observable, and
scalable operational layers:

| Layer | MVP gap |
| --- | --- |
| **Persistence** | Runs exist only in memory between caller-supplied `advance_workflow()` invocations |
| **Delivery mechanisms** | Human review stops exist as states; nothing notifies or routes owners to act |
| **Operational resiliency** | No crash recovery, checkpointing, or enforced idempotent step replay for print runs |
| **Security** | No print-workflow authn/authz; approval identity is caller-supplied metadata |
| **Scalability** | Single-process, synchronous, caller-driven execution |
| **Observability** | Rich per-step records exist in return values but are not exported to production telemetry |

### Bottom line

**The workflow architecture is largely production-ready.** Contracts, state
machine rules, subsystem boundaries, and orchestration sequencing are sound and
heavily tested.

**The integrations and operational layers are not.** The MVP is a correct
in-process orchestration engine without the surrounding platform services a real
deployment would need.

---

## 2. MVP vs Production

| Area | MVP | Production |
| --- | --- | --- |
| **Persistence** | Caller holds `PrintWorkflowRunResult` and passes it back on each `advance_workflow()` call. No `save_run()` / `load_run()`. | Durable run store (database or event log). Automatic load/save per step. Versioned run history. Crash-safe checkpointing. |
| **Human review delivery** | `OWNER_REVIEW_PENDING` stops the workflow. Tests pass `metadata["approval_decision"]` manually. | Email, review portal, or ticketing integration with signed approval links. Notification routing by role/tenant. SLA tracking on review latency. |
| **Authentication** | Print workflow has no API surface. Separate Tier-1 ingest uses `OPS_API_KEY` on protected `/ops` routes only. | Authenticated callers for every workflow operation (service accounts, user sessions, mTLS). Identity propagated into `ApprovalDecision.approver`. |
| **Authorization** | Any in-process caller can advance any run and supply any approval metadata. | Role-based access: who may submit, advance, approve, retry, or cancel. Tenant/job isolation. Approval actions bound to authenticated identity. |
| **Storage** | `generated_artifact_store` writes to local `artifacts/generated/` (OpenAI mode). URIs are logical (`artifact://...`). No object store. | Durable object storage (S3/GCS/Azure Blob). Content-addressable keys. Retention policies. Production package artifacts stored and retrievable by print shop. |
| **Retry behavior** | State machine defines `RETRYABLE_STATES`; orchestrator documents retry policy but does not enforce attempt budgets or automatic retries. | Configurable per-stage retry with backoff, max attempts, dead-letter on exhaustion. Idempotent subsystem replay. |
| **Monitoring** | `SubsystemExecutionRecord`, `TransitionCheckResult`, and structured JSONL logging exist for the Tier-1 control plane ingest path. Print workflow steps return records to the caller only. | Metrics (latency, error rate, queue depth), dashboards, alerting on failure rates and review SLA breaches. |
| **Audit** | Per-step `SubsystemExecutionRecord` and `ModelInvocationRecord` are returned in `WorkflowAdvanceResult`. Not persisted. `ContractProvenance` fields exist on schemas but are largely unpopulated. | Immutable audit log of every transition and human decision. Tamper-evident storage. Reconstructable lineage for compliance. |
| **Configuration** | `PrintSpecification` resolved from in-code rules + `configs/routing.json`. Env vars for generation mode. | Versioned, environment-specific config with promotion workflow. Runtime config reload or pinned versions per run. Secrets separated from config. |
| **Deployment** | Local `uvicorn` + `pytest`. CI runs tests on push. No print-workflow service deployment. | Containerized services, staged environments (dev/staging/prod), health checks, rolling deploys, migration strategy for run store schema. |
| **Scalability** | Single Python process; synchronous subsystem calls; caller loops `advance_workflow()`. | Queue-backed workers, horizontal scaling, concurrency limits, backpressure on AI generation. |
| **Disaster recovery** | None. Process death loses in-flight run state unless caller persisted it externally. | Run store replication, object storage versioning/backups, RPO/RTO targets, replay procedures. |

---

## 3. Schema Gaps

The domain contracts in `app/domain/print_schemas.py` and orchestration contracts
in `app/domain/print_orchestration_schemas.py` are sufficient for MVP
correctness. Several fields reflect interim design that would create production
friction if left unchanged.

### ValidationResult — `validated_candidate_ids` naming

| | |
| --- | --- |
| **Current implementation** | `validated_candidate_ids` and `passed_candidate_ids` store output ids for both `GeneratedCandidate.candidate_id` and `TransformedAsset.transformed_asset_id`. |
| **Production concern** | Operators and integrators cannot distinguish asset type from field name. Downstream systems may mis-route deterministic outputs. Analytics and audit queries become ambiguous. |
| **Recommended fix** | Add neutral fields (e.g. `validated_output_ids`, `passed_output_ids`) with optional `output_kind` metadata, or a small `ValidatedOutputRef` type. Deprecate candidate-specific naming with a migration period. |

### Approval decisions — metadata payload

| | |
| --- | --- |
| **Current implementation** | `WorkflowAdvanceRequest.metadata["approval_decision"]` carries `{ status, candidate_id, approver }`. `WorkflowOperation` defines `APPROVE`, `REJECT`, etc., but the orchestrator keys off metadata, not typed fields. |
| **Production concern** | Untyped metadata is not validated at the API boundary. Any caller can spoof `approver`. No schema versioning. Hard to generate OpenAPI docs or enforce authorization at the field level. |
| **Recommended fix** | Add `ApprovalDecisionInput` (or use `operation` + typed fields) on `WorkflowAdvanceRequest`. Validate at API layer. Bind `approver` to authenticated principal, not client-supplied string. |

### PrintWorkflowRunResult — deterministic transform not first-class

| | |
| --- | --- |
| **Current implementation** | `DeterministicTransformResult` exists in schemas. `PrintWorkflowRunResult` has `candidates` but no `deterministic_transform` field. Orchestrator uses `getattr(run, "deterministic_transform", None)` with a candidate placeholder fallback in tests. |
| **Production concern** | Deterministic branch runs cannot be fully reconstructed from the run bundle. Packaging and validation must guess output location. Audit trail incomplete for non-AI remediation. |
| **Recommended fix** | Add `deterministic_transform: Optional[DeterministicTransformResult]` to `PrintWorkflowRunResult`. Persist and populate on deterministic branch completion. Remove placeholder fallback in orchestrator. |

### WorkflowAdvanceRequest — caller-supplied run state

| | |
| --- | --- |
| **Current implementation** | Caller passes both `current_state` and `existing_run_result`. Spine trusts `current_state` as the transition source; run bundle state is updated on output. |
| **Production concern** | State drift if caller passes mismatched `current_state` and `existing_run_result.state`. No server-side authority over canonical state. |
| **Recommended fix** | Run store owns canonical `state`. API loads run by `run_id` and derives `current_state` from persisted record. Reject requests where caller state disagrees with stored state. |

### Idempotency key — defined but not enforced

| | |
| --- | --- |
| **Current implementation** | `WorkflowAdvanceRequest.idempotency_key` is required and echoed in `WorkflowAdvanceResult`. Orchestrator does not deduplicate or replay prior step results. (Separate Tier-1 ingest uses `SQLiteIdempotencyStore`.) |
| **Production concern** | Network retries can double-execute subsystems (e.g. duplicate OpenAI calls, duplicate packaging). |
| **Recommended fix** | Persist `(run_id, idempotency_key) -> WorkflowAdvanceResult` in run store. Return cached result on replay. |

### ContractProvenance — schema present, population absent

| | |
| --- | --- |
| **Current implementation** | Most stage contracts include optional `provenance` (`source_id`, `derived_from_ids`, `created_by_stage`, `config_version`). Services do not consistently populate it. |
| **Production concern** | Cannot reconstruct lineage for compliance, debugging, or dispute resolution. |
| **Recommended fix** | Spine or a cross-cutting helper populates provenance on every subsystem output using run context and config version. |

### ProductionPackage — manifest-only delivery

| | |
| --- | --- |
| **Current implementation** | `ProductionPackage` carries `output_uris` and a JSON `manifest`. No separate delivery receipt or handoff confirmation. |
| **Production concern** | Print shop integration needs durable artifact locations, checksums, and delivery acknowledgment. |
| **Recommended fix** | Extend manifest with content hashes, storage bucket keys, and optional `handoff_status` / `delivered_at` fields once storage layer exists. |

---

## 4. Persistence Layer

### What is absent today

There is **no Run Store** for the print workflow. Each `advance_workflow()` call
is stateless from the platform's perspective:

1. Caller constructs `WorkflowAdvanceRequest` with `existing_run_result`.
2. Orchestrator returns `WorkflowAdvanceResult` with an updated bundle.
3. Caller is solely responsible for retaining that bundle until the next step.

If the caller crashes, the process restarts, or a user closes a browser session,
**in-flight workflow state is lost** unless something external saved it.

### Production requirements

#### Run Store API

```
save_run(run: PrintWorkflowRunResult) -> None
load_run(run_id: str) -> PrintWorkflowRunResult
```

Additional operations production typically needs:

| Operation | Purpose |
| --- | --- |
| `list_runs(filters)` | Operator dashboards, support tooling |
| `append_step_record(run_id, WorkflowAdvanceResult)` | Full audit history per advance |
| `get_step_by_idempotency_key(run_id, key)` | Idempotent replay |

#### Versioning

Each `save_run` should append a new version (optimistic concurrency via
`version` or `updated_at` check). Concurrent advances on the same run must be
detected and rejected.

#### Audit history

`WorkflowAdvanceResult` (including `transition_checks`, `subsystem_records`,
`reasons`) should be persisted immutably per step — not only the latest
`PrintWorkflowRunResult` snapshot.

#### Checkpoint recovery

On worker crash mid-step:

- If subsystem call completed but save failed → replay from idempotency key must
  return the same result without re-invoking the subsystem.
- If subsystem call did not complete → resume from last committed checkpoint
  state.

#### Run resumption

`WorkflowOperation.RESUME` is defined in schemas but not implemented in the
orchestrator. Production needs explicit resume semantics: load run from store,
validate state is non-terminal, continue auto-advance or wait for human input.

#### State reconstruction

Given a run id, operators must reconstruct:

- Current `PrintWorkflowState` and `PrintWorkflowStage`
- All stage outputs (`normalization` through `production_package`)
- Full transition history
- Subsystem execution timeline

### How workflow state would be persisted

Recommended model:

```
runs
  run_id, submission_id, state, stage, status, version, created_at, updated_at

run_snapshots (or event log)
  run_id, version, print_workflow_run_result_json, recorded_at

advance_steps
  run_id, idempotency_key, workflow_advance_result_json, recorded_at
```

The orchestrator API would **load** before advance and **save** after advance
inside a transaction. Callers would pass `run_id` + `idempotency_key`, not the
full run bundle.

---

## 5. Human Review Delivery Layer

### Current behavior

When validation passes, the orchestrator routes to `OWNER_REVIEW_PENDING` and
**stops** (`stopped=True`, `status=NEEDS_REVIEW`). The workflow is logically
waiting for a human, but the platform delivers nothing to that human.

Approval resumes only when a caller invokes `advance_workflow()` again with:

```python
metadata={
    "approval_decision": {
        "status": "approved",
        "candidate_id": "<id>",
        "approver": "owner@example.com",
    }
}
```

This is adequate for tests. It is not a production review delivery mechanism.

### Missing production responsibilities

| Responsibility | Gap |
| --- | --- |
| **Email delivery** | No notification when a run enters `OWNER_REVIEW_PENDING` |
| **Review portal** | No UI to inspect candidates, spec summary, validation findings |
| **Approval links** | No signed, time-limited URLs that map to a specific run + candidate |
| **Notification routing** | No mapping from job/product/tenant to reviewer group |
| **Escalation** | No reminder or escalation on review SLA breach |
| **Decision capture** | Approver identity is a free-text string, not authenticated action |

### Production architecture (delivery layer)

The delivery layer sits **outside** the orchestration spine but **calls into** it:

```
OWNER_REVIEW_PENDING
    → Review Delivery Service emits notification (email/webhook)
    → Review Portal loads run from Run Store (read-only)
    → Approver authenticates and submits decision
    → API Layer writes ApprovalDecisionInput
    → Orchestrator advances run
```

Ownership boundaries:

- **Orchestrator** — records decision, transitions state (already implemented).
- **Review Delivery Service** — notifies, routes, tracks SLA (not implemented).
- **Review Portal / API** — authenticated decision submission (not implemented).
- **Run Store** — supplies run context to portal (not implemented).

The MVP correctly models the **stop**. Production must add the **delivery and
capture** infrastructure around that stop.

---

## 6. External API Layer

### Current behavior

`advance_workflow()` in `app/services/print_orchestrator.py` is a **pure
function**. Tests and the end-to-end smoke test call it directly in-process.

The existing FastAPI app (`app/main.py`) serves the **Tier-1 control plane**
ingest/decide/act pipeline. It does **not** expose print workflow endpoints.

### Missing production responsibilities

| Surface | Purpose | Owner |
| --- | --- | --- |
| **REST API** | `POST /runs` (start), `POST /runs/{id}/advance`, `GET /runs/{id}` | API layer |
| **Review endpoints** | `GET /runs/{id}/review-package`, `POST /runs/{id}/approve` | API + auth layer |
| **Webhook callbacks** | Notify external systems on state changes (`validation_complete`, `owner_review_pending`, `completed`) | Delivery/integrations layer |
| **Retry endpoints** | `POST /runs/{id}/retry` from retryable states with auth + audit | API layer |
| **Cancel endpoints** | `POST /runs/{id}/cancel` | API layer |

### Ownership boundaries

```
Client / Portal / Webhook consumer
        ↓
API Layer (authn, authz, request validation, idempotency header)
        ↓
Run Store (load/save)
        ↓
Orchestration Spine (advance_workflow — no HTTP knowledge)
        ↓
Subsystems
```

The spine must remain transport-agnostic. Production adds an API shell that
translates HTTP requests into `WorkflowAdvanceRequest` and persists
`WorkflowAdvanceResult`.

---

## 7. Security Layer

### Authentication

**MVP:** Print workflow has no authenticated entry point. Tier-1 ingest requires
`X-API-Key` matching `OPS_API_KEY` for protected routes.

**Production gap:** Every workflow mutation needs authenticated identity. Service
accounts for automation; user auth for human approvals.

### Authorization

**MVP:** No role model. No check that the caller may approve a given run.

**Production gap:** RBAC or ABAC — e.g. only `print-approver` role on tenant X
may submit approval for runs in `OWNER_REVIEW_PENDING`.

### Approval spoofing prevention

**MVP:** `approver` is client-supplied metadata. Tests set `"owner@example.com"`
directly. Nothing prevents a malicious caller from impersonating an approver.

**Production gap:** Bind `ApprovalDecision.approver` to the authenticated
principal from the security token. Reject mismatched client-supplied approver
fields. Optionally require signed approval tokens for email-link flows.

### API key handling

**MVP:** `OPENAI_API_KEY` and `OPS_API_KEY` read from environment. `.env` for
local dev.

**Production gap:** Secret manager (Vault, AWS Secrets Manager, etc.). Key
rotation. Separate keys per environment. No secrets in logs or `ModelInvocationRecord`.

### Secret management

Generation service reads `OPENAI_API_KEY` at call time. Production needs scoped
credentials, usage quotas, and audit of key access.

### Environment isolation

**MVP:** Single configuration; `PRINT_GENERATION_MODE=fake` in CI.

**Production gap:** Hard separation of dev/staging/prod data and secrets.
Staging may use fake generation; prod uses OpenAI with production storage.

### Audit integrity

**MVP:** Audit data returned to caller, not stored immutably.

**Production gap:** Append-only audit log. Human decisions and state transitions
tamper-evident. Correlation ids across API → orchestrator → subsystem.

### Approval identity validation

Production must verify:

- Approver is authenticated.
- Approver is authorized for this run/tenant.
- Decision applies to a candidate in the current `ApprovalPackage`.
- Decision is idempotent (duplicate approve does not double-advance).

---

## 8. Storage Architecture

### Generated artifacts

**MVP:**

- Fake mode: logical URIs only (`artifact://generated/...`).
- OpenAI mode: `generated_artifact_store` writes to local `artifacts/generated/{candidate_id}.{format}`.
- Overwrites on duplicate `candidate_id`. No checksum recorded on contract.

**Production gap:** Durable object storage, checksums in `GeneratedCandidate.metadata`
or manifest, multi-AZ durability, access control.

### Production packages

**MVP:** `ProductionPackage.output_uris` and `manifest` are assembled in-memory.
No physical package artifact is written. No handoff to print shop systems.

**Production gap:** Write production bundle to object storage or MFT endpoint.
Record location, hash, and assembly timestamp in the run store.

### Asset retention

**MVP:** No retention policy. Local files persist until manually deleted.

**Production gap:** Configurable retention per asset role (submission, generated
candidate, production package). Legal hold support.

### Asset versioning

**MVP:** Single version per `candidate_id`. Overwrite semantics.

**Production gap:** Immutable versioning — new generation creates new version;
approved version pinned in `ProductionPackage`.

### Object storage

**MVP:** Local filesystem.

**Production gap:** S3-compatible or enterprise object store with IAM policies,
lifecycle rules, and encryption at rest.

### Backup strategy

**MVP:** None.

**Production gap:** Regular backups, cross-region replication for production
packages, restore drills.

### Content-addressable storage

**MVP:** URIs are id-based, not hash-based.

**Production gap:** Dedup by content hash. Integrity verification on read.

### Artifact lifecycle management

Production needs explicit states: `uploaded → validated → approved → packaged →
archived → deleted`, with automated transitions driven by retention config.

---

## 9. Observability

### Existing strengths (MVP)

The orchestration layer already produces structured observability **artifacts**
per step:

| Artifact | Contents |
| --- | --- |
| `SubsystemExecutionRecord` | Subsystem name, input/output contract ids, status, latency, error codes |
| `TransitionCheckResult` | From/to states, allowed flag, decision, legal alternatives |
| `ModelInvocationRecord` | Provider, model, timing, status, `generated_candidate_ids`, errors |

Tier-1 ingest additionally writes JSONL events to `logs/events.jsonl` via
`app/core/logging.py`.

These are suitable **building blocks** for production telemetry.

### Missing production concerns

| Concern | Gap |
| --- | --- |
| **Metrics** | No Prometheus/OpenTelemetry counters for step latency, failure rate, runs by state, OpenAI cost |
| **Dashboards** | No operational view of in-flight runs, queue depth, approval backlog |
| **Alerting** | No alerts on `FAILED` rate spike, stuck `OWNER_REVIEW_PENDING`, OpenAI errors |
| **Tracing** | No distributed trace linking API request → orchestrator step → OpenAI call |
| **Operational reporting** | `ops/weekly_report.py` exists for Tier-1 events; print workflow not included |
| **SLA monitoring** | No tracking of time-in-state (e.g. hours in owner review, generation p95) |

### Production path

Export per-step records from the Run Store / advance log to:

- Metrics backend (aggregated)
- Trace backend (per-request spans)
- SIEM (security audit)

The MVP records are **correct in shape**; production needs **persistent export
and aggregation**, not redesign of the contracts.

---

## 10. Reliability & Recovery

### Retry policies

**MVP:** `print_state_machine.py` defines `RETRYABLE_STATES`. Orchestrator
docstring references retry policy. No automatic retry execution, no attempt
budget, no backoff.

**Production gap:** Implement `WorkflowOperation.RETRY` with config-driven max
attempts per stage. Distinguish transient (provider timeout) vs permanent
(spec validation) failures.

### Dead-letter queues

**MVP:** Terminal `FAILED` is the only dead end. No DLQ for manual inspection.

**Production gap:** Runs exceeding retry budget move to DLQ with full context for
operator intervention.

### Compensating actions

**MVP:** No rollback or compensating transactions (e.g. revoke package if
delivery fails).

**Production gap:** Define compensating flows for reversible stages where
business rules require it.

### Workflow recovery

**MVP:** Caller must hold run bundle. Process crash = data loss.

**Production gap:** Load from Run Store, resume from last committed state.

### Partial run restoration

**MVP:** `PrintWorkflowRunResult` supports optional stage outputs — partial runs
serialize correctly in memory.

**Production gap:** Persist partial runs durably so support can inspect failed
runs mid-pipeline.

### Idempotent replay

**MVP:** Tier-1 ingest idempotency via SQLite. Print workflow idempotency key
not enforced.

**Production gap:** Step-level idempotency store keyed by `(run_id,
idempotency_key)`.

### Duplicate event handling

**MVP:** No protection against duplicate advance requests.

**Production gap:** Exactly-once step semantics at the platform boundary.

### Provider outage handling

**MVP:** OpenAI failure surfaces as `GENERATION_FAILED` or exception path. No
circuit breaker, no queued retry, no fallback provider.

**Production gap:** Circuit breaker on `openai_image_client`, queue generation
for later retry, optional fallback to fake/alternate provider with explicit
degraded-mode labeling.

---

## 11. Scalability

### MVP: single-process model

- Caller synchronously loops `advance_workflow()`.
- Subsystems execute in-process (pure functions except optional local file I/O
  for OpenAI artifacts).
- No concurrency model for multiple runs.

This is appropriate for correctness demonstration.

### Production requirements

| Requirement | Why |
| --- | --- |
| **Concurrent workflows** | Many customer submissions in parallel |
| **Queue-backed execution** | Decouple API accept from long-running generation |
| **Distributed workers** | Scale generation and transform workers independently |
| **Horizontal scaling** | Multiple API + worker instances behind load balancer |
| **Rate limiting** | Protect OpenAI budget and prevent abuse |
| **Backpressure** | Shed load when generation queue depth exceeds threshold |

### Architectural shift

```
API (stateless, scales horizontally)
  → Run Store (shared)
  → Work Queue (per-stage topics: generation, validation, ...)
  → Workers (pull jobs, call advance_workflow for one step, save result)
```

The orchestrator function can remain largely unchanged. Production adds
**async execution** and **shared state** around it.

---

## 12. OpenAI Production Readiness

### Current integration

| Component | Role |
| --- | --- |
| `print_generation.py` | Mode switch (`fake` / `openai`), assembles candidates + invocation record |
| `openai_image_client.py` | Provider boundary — lazy import, API key check, `images.generate` |
| `generated_artifact_store.py` | Optional local persistence of base64 payloads |

Default mode is `fake`. OpenAI is opt-in via environment. The orchestrator never
calls OpenAI directly. Tests monkeypatch `generate_candidates`.

This is a sound **boundary** for MVP.

### Missing production responsibilities

| Concern | Gap |
| --- | --- |
| **Cost controls** | No per-run or per-tenant budget cap on generation calls |
| **Rate limiting** | No throttle on OpenAI requests; risk of quota exhaustion |
| **Fallback providers** | Single provider path; no alternate model/provider on failure |
| **Prompt versioning** | `GenerationRequest` has no `prompt_version`; changes are not tracked |
| **Model version pinning** | `PRINT_OPENAI_IMAGE_MODEL` is env-defaulted, not pinned per run |
| **Safety monitoring** | No content policy evaluation on generated outputs |
| **Generation quality monitoring** | No systematic tracking of validation pass rate post-generation |

### Production recommendations

- Record `model_name`, `prompt_version`, and estimated cost on every
  `ModelInvocationRecord`.
- Enforce rate limits and budgets in the generation service or a wrapper.
- Pin model version in run metadata at generation time (reproducibility).
- Alert when validation failure rate after generation exceeds baseline.

---

## 13. Deployment Architecture

### Current local execution

```
Developer machine / CI runner
  pytest → advance_workflow() in-process
  uvicorn app.main:app → Tier-1 ingest API only
  optional: scripts/demo_generate_image.py
```

CI (`.github/workflows/ci.yml`) runs `pytest` on Python 3.11. No container
build, no staged deploy, no print-workflow service.

### Production deployment layers

| Layer | Responsibility |
| --- | --- |
| **API layer** | FastAPI (or equivalent) exposing print run endpoints, auth, rate limits |
| **Workflow layer** | `print_orchestrator.py` — unchanged core sequencing |
| **Worker layer** | Async consumers for long steps (generation, future real transforms) |
| **Storage layer** | Run store DB + object storage for artifacts |
| **Observability layer** | Metrics, traces, logs, dashboards |
| **Secrets layer** | Managed secrets for OpenAI, DB, API keys |

### CI/CD

Production needs:

- Build and push container images
- Run test gate (current 233 tests)
- Deploy to staging with `PRINT_GENERATION_MODE=fake`
- Smoke test print workflow API
- Promote to production with migration scripts for run store

### Environment promotion

Config and secrets must differ per environment. Run data must not leak between
tenants or between staging and production.

---

## 14. Recommended Productionization Order

| Priority | Layer | Rationale |
| --- | --- | --- |
| **1** | **Persistence Layer** | Without a Run Store, nothing else is production-safe. Crashes lose state. Idempotency cannot be enforced. APIs cannot be stateless. Every other layer depends on durable runs. |
| **2** | **External API Layer** | Persistence enables a real HTTP surface. Clients, portals, and webhooks need load/save boundaries. Auth wraps the API, not the orchestrator. |
| **3** | **Human Review Delivery** | The workflow already stops at `OWNER_REVIEW_PENDING`. Production value requires notifying and capturing authenticated decisions — not reshaping the spine. |
| **4** | **Observability** | Once runs are persistent and API-driven, operators need visibility. Export existing `SubsystemExecutionRecord` / transition data to metrics and alerts. |
| **5** | **Scalability** | Queue-backed workers and horizontal scaling matter after correct single-run behavior is durable and observable. |
| **6** | **Advanced AI Operations** | Cost controls, provider fallbacks, and quality monitoring optimize cost and reliability — they do not unblock basic production operation if earlier priorities are done. |

### Why this order

1. **Persistence** is foundational — it converts an in-memory demo into a system
   that survives restarts and supports concurrent callers.
2. **API** is the integration surface — production systems do not call Python
   functions directly.
3. **Review delivery** closes the largest functional gap in the human path
   without altering workflow logic.
4. **Observability** is required before scaling — otherwise growth creates blind
   spots.
5. **Scalability** follows proven single-run correctness at volume.
6. **AI operations** refine economics and resilience once the platform runs
   reliably.

---

## 15. Final Assessment

### How far is the current MVP from production?

The MVP is **not** far in **workflow design** and **subsystem correctness**. It
is **substantially far** in **platform integration** and **operational maturity**.

| Dimension | Maturity | Assessment |
| --- | --- | --- |
| **Architecture maturity** | **High** | Clear spine + subsystem separation. Legal state machine. Dual remediation branches. AI as actuator. Contracts are well-factored for extension. |
| **Workflow maturity** | **High** | All stages implemented and orchestrated. End-to-end happy path proven. Failure and stop paths tested per phase. |
| **Contract maturity** | **Medium** | Schemas cover the pipeline. Known naming and bundling gaps (`validated_candidate_ids`, missing `deterministic_transform` on run bundle, metadata-based approval). `ContractProvenance` underused. Fixable without architectural rework. |
| **Operational maturity** | **Low** | No run persistence, no print API, no review delivery, no enforced idempotency, no production storage, minimal security for workflow operations, no metrics/alerting for print runs. |

### Honest summary for a future engineer

**What you have:** A correct, testable orchestration engine with eleven stage
services and a complete state machine. You can advance a run from submission to
completion in-process with high confidence in transition legality and subsystem
boundaries.

**What you do not have:** The platform around that engine — the database, API,
notification system, object store, secret management, and telemetry that turn a
workflow library into a service operators can run, monitor, and trust under
failure and load.

**Distance metaphor:** The **engine and transmission are built and tested** (≈
80–90% of workflow logic). The **vehicle** — chassis, fuel system, dashboard,
locks, registration — is largely unbuilt (≈ 10–20% of production operations).

Productionization is therefore **integration and operations work** layered onto
a sound core, not a rewrite of the workflow architecture. The recommended path
starts with persistence and an API, then closes the human delivery gap, then
hardens for scale and AI economics.

---

*Document reflects repository state: 233 tests passing, Phases 1–11 orchestration
complete, `docs/IMPLEMENTATION_SEQUENCE.md` workflow completion status.*
