"""
Print-specific orchestration / spine contracts.

These models describe the *inputs and outputs of one orchestration step* for the
print-specification workflow. They are contracts only — they contain no control
logic, do not call the state machine, and do not implement the orchestrator.

The orchestration spine (defined elsewhere, later) is responsible for actually
validating transitions (via app/domain/print_state_machine.py), calling
subsystems, and bundling results. These schemas just give that spine a typed
request/response surface and a place to record what happened — including partial
and failure results.

Reuses:
  - app/domain/print_schemas.py (PrintWorkflowState/Stage, RawSubmission,
    PrintWorkflowRunResult, ResultStatus, StageIssue, ContractProvenance)
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.print_schemas import (
    ContractProvenance,
    PrintWorkflowRunResult,
    PrintWorkflowState,
    RawSubmission,
    ResultStatus,
    StageIssue,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WorkflowOperation(str, Enum):
    """The intent of an orchestration step requested by a caller."""

    START = "start"
    ADVANCE = "advance"
    RESUME = "resume"
    RETRY = "retry"
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_REVISION = "request_revision"
    CANCEL = "cancel"


class TransitionDecision(str, Enum):
    """Outcome of evaluating a requested state transition."""

    ALLOWED = "allowed"
    REJECTED = "rejected"
    NOOP = "noop"


class SubsystemExecutionStatus(str, Enum):
    """Outcome of a single subsystem call attempt from the spine."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


# ---------------------------------------------------------------------------
# 1. Advance request
# ---------------------------------------------------------------------------


class WorkflowAdvanceRequest(BaseModel):
    """
    A request to advance or resume a print workflow run by one orchestration step.

    A new run supplies `raw_submission`; an in-flight run supplies
    `existing_run_result`. `requested_target_state` is optional — the spine may
    infer the next state — but lets callers express an explicit intended target
    (which the spine still validates against the state machine).
    """

    run_id: str = Field(..., description="Identity of the workflow run being advanced")
    idempotency_key: str = Field(
        ...,
        description="Key guaranteeing exactly-once progression for this step",
    )
    operation: WorkflowOperation = Field(..., description="Intent of this step")
    current_state: PrintWorkflowState = Field(
        ...,
        description="The run's current state (transition source)",
    )
    raw_submission: Optional[RawSubmission] = Field(
        None,
        description="Starting input for a new run",
    )
    existing_run_result: Optional[PrintWorkflowRunResult] = Field(
        None,
        description="Prior run bundle for an in-flight/resumed run",
    )
    requested_target_state: Optional[PrintWorkflowState] = Field(
        None,
        description="Optional explicit target state (still validated by the spine)",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Caller/debug context for this step",
    )


# ---------------------------------------------------------------------------
# 2. Transition check result
# ---------------------------------------------------------------------------


class TransitionCheckResult(BaseModel):
    """
    The recorded result of a transition legality check.

    This is a data record only. The spine computes legality using the state
    machine and stores the outcome here; nothing in this model evaluates it.
    """

    from_state: PrintWorkflowState = Field(..., description="Transition source state")
    to_state: PrintWorkflowState = Field(..., description="Requested target state")
    allowed: bool = Field(..., description="Whether the transition is legal")
    decision: TransitionDecision = Field(
        TransitionDecision.REJECTED,
        description="Categorized transition outcome",
    )
    reason: Optional[str] = Field(
        None,
        description="Human-readable explanation of the decision",
    )
    allowed_transitions: List[PrintWorkflowState] = Field(
        default_factory=list,
        description="Legal next states from `from_state` (for diagnostics)",
    )


# ---------------------------------------------------------------------------
# 3. Subsystem execution record
# ---------------------------------------------------------------------------


class SubsystemExecutionRecord(BaseModel):
    """
    One subsystem call attempt made by the spine.

    Captures inputs/outputs (by contract id), status, timing, and any error so a
    run can be audited and partial/failed steps remain fully representable. The
    spine records these; subsystems do not own this contract.
    """

    subsystem_name: str = Field(..., description="Subsystem/interface invoked")
    input_contract_ids: List[str] = Field(
        default_factory=list,
        description="Ids of contracts passed into the subsystem",
    )
    output_contract_ids: List[str] = Field(
        default_factory=list,
        description="Ids of contracts produced by the subsystem",
    )
    status: SubsystemExecutionStatus = Field(..., description="Outcome of the call")
    started_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the call started",
    )
    completed_at: Optional[datetime] = Field(
        None,
        description="When the call finished, if it has",
    )
    latency_ms: Optional[int] = Field(
        None,
        ge=0,
        description="End-to-end call latency in milliseconds",
    )
    error_code: Optional[str] = Field(
        None,
        description="Machine-readable error code if the call failed",
    )
    error_message: Optional[str] = Field(
        None,
        description="Human-readable error detail if the call failed",
    )
    retry_count: int = Field(0, ge=0, description="Number of retries performed")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional execution/debug context",
    )


# ---------------------------------------------------------------------------
# 4. Advance result
# ---------------------------------------------------------------------------


class WorkflowAdvanceResult(BaseModel):
    """
    The result of one orchestration step.

    Always returns the (possibly partial) PrintWorkflowRunResult so callers can
    inspect produced stage outputs even on stop/failure. `stopped`/`stop_reason`
    communicate human-review and terminal halts.
    """

    run_id: str = Field(..., description="Workflow run this result belongs to")
    idempotency_key: str = Field(..., description="Idempotency key of the step")
    operation: WorkflowOperation = Field(..., description="Operation that was attempted")
    previous_state: PrintWorkflowState = Field(..., description="State before the step")
    current_state: PrintWorkflowState = Field(..., description="State after the step")
    transition_check: Optional[TransitionCheckResult] = Field(
        None,
        description="Legality check recorded for this step's transition",
    )
    run_result: PrintWorkflowRunResult = Field(
        ...,
        description="The bundled (possibly partial) run record",
    )
    subsystem_records: List[SubsystemExecutionRecord] = Field(
        default_factory=list,
        description="Subsystem call attempts made during this step",
    )
    stopped: bool = Field(
        False,
        description="True if the spine halted (human-review or terminal state)",
    )
    stop_reason: Optional[str] = Field(
        None,
        description="Why the run stopped, if it did",
    )
    status: ResultStatus = Field(..., description="Overall outcome of this step")
    reasons: List[StageIssue] = Field(
        default_factory=list,
        description="Why the step reached this state",
    )
    warnings: List[StageIssue] = Field(
        default_factory=list,
        description="Non-blocking concerns from this step",
    )
    next_steps: Optional[str] = Field(
        None,
        description="What the caller/operator should do next",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Caller/debug context for this result",
    )
    provenance: Optional[ContractProvenance] = Field(
        None,
        description="Lineage/provenance for this contract instance",
    )
