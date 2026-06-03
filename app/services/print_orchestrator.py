"""
Phase 0: Thin orchestration spine shell for the print-specification workflow.

This module coordinates *one* workflow step: it validates the requested state
transition (using app/domain/print_state_machine.py), stops at terminal and
human-review states, and always returns a partial PrintWorkflowRunResult.

Phase 0 deliberately contains NO subsystem logic:
  - no normalization, specification, compliance, adaptation, prompt, AI,
    validation, approval, or packaging work
  - no subsystem calls (so no SubsystemExecutionRecords are produced)
  - no file I/O, no FastAPI, no model calls

Everything here is a pure function of its inputs, so it is easy to test.
"""

from datetime import datetime
from typing import List, Optional

from app.domain.print_orchestration_schemas import (
    SubsystemExecutionRecord,
    SubsystemExecutionStatus,
    TransitionCheckResult,
    TransitionDecision,
    WorkflowAdvanceRequest,
    WorkflowAdvanceResult,
)
from app.domain.print_schemas import (
    NormalizationResult,
    PrintSpecification,
    PrintWorkflowRunResult,
    PrintWorkflowStage,
    PrintWorkflowState,
    ResultStatus,
    StageIssue,
)
from app.domain.print_state_machine import (
    can_transition,
    get_allowed_transitions,
    is_human_review_state,
    is_terminal_state,
)
from app.services.print_normalization import normalize_submission
from app.services.print_specification import resolve_specification

S = PrintWorkflowState

# Coarse stage label for each fine-grained state. Cancelled has no dedicated
# stage, so it maps to the FAILED coarse phase (terminal, non-success).
_STATE_TO_STAGE = {
    S.SUBMITTED: PrintWorkflowStage.INTAKE,
    S.INGESTED: PrintWorkflowStage.INTAKE,
    S.NORMALIZATION_PENDING: PrintWorkflowStage.NORMALIZATION,
    S.NORMALIZED: PrintWorkflowStage.NORMALIZATION,
    S.NORMALIZATION_NEEDS_REVIEW: PrintWorkflowStage.NORMALIZATION,
    S.NORMALIZATION_FAILED: PrintWorkflowStage.NORMALIZATION,
    S.SPECIFICATION_PENDING: PrintWorkflowStage.SPECIFICATION,
    S.SPECIFICATION_RESOLVED: PrintWorkflowStage.SPECIFICATION,
    S.SPECIFICATION_FAILED: PrintWorkflowStage.SPECIFICATION,
    S.COMPLIANCE_PENDING: PrintWorkflowStage.COMPLIANCE,
    S.COMPLIANCE_COMPLETE: PrintWorkflowStage.COMPLIANCE,
    S.COMPLIANCE_FAILED: PrintWorkflowStage.COMPLIANCE,
    S.ADAPTATION_PLANNED: PrintWorkflowStage.ADAPTATION,
    S.GENERATION_PENDING: PrintWorkflowStage.GENERATION,
    S.GENERATION_RUNNING: PrintWorkflowStage.GENERATION,
    S.GENERATION_COMPLETE: PrintWorkflowStage.GENERATION,
    S.GENERATION_FAILED: PrintWorkflowStage.GENERATION,
    S.VALIDATION_PENDING: PrintWorkflowStage.VALIDATION,
    S.VALIDATION_COMPLETE: PrintWorkflowStage.VALIDATION,
    S.VALIDATION_FAILED: PrintWorkflowStage.VALIDATION,
    S.OWNER_REVIEW_PENDING: PrintWorkflowStage.APPROVAL,
    S.APPROVED: PrintWorkflowStage.APPROVAL,
    S.REJECTED: PrintWorkflowStage.APPROVAL,
    S.REVISION_REQUESTED: PrintWorkflowStage.APPROVAL,
    S.PRODUCTION_PACKAGING_PENDING: PrintWorkflowStage.PRODUCTION,
    S.PRODUCTION_PACKAGE_CREATED: PrintWorkflowStage.PRODUCTION,
    S.COMPLETED: PrintWorkflowStage.COMPLETED,
    S.FAILED: PrintWorkflowStage.FAILED,
    S.CANCELLED: PrintWorkflowStage.FAILED,
}


def _stage_for_state(state: PrintWorkflowState) -> PrintWorkflowStage:
    """Map a fine-grained state to its coarse PrintWorkflowStage."""
    return _STATE_TO_STAGE.get(state, PrintWorkflowStage.FAILED)


def _run_status_for_state(state: PrintWorkflowState) -> ResultStatus:
    """Best-effort run-level status for a given state (Phase 0 heuristic)."""
    if state == S.COMPLETED:
        return ResultStatus.PASSED
    if state in (S.FAILED, S.CANCELLED):
        return ResultStatus.FAILED
    if is_human_review_state(state):
        return ResultStatus.NEEDS_REVIEW
    return ResultStatus.PENDING


def _build_partial_run_result(
    request: WorkflowAdvanceRequest,
    state: PrintWorkflowState,
) -> PrintWorkflowRunResult:
    """
    Return a partial PrintWorkflowRunResult reflecting `state`.

    Reuses an existing run bundle when provided (so prior stage outputs are
    preserved); otherwise constructs a fresh, minimal bundle. Phase 0 only sets
    run_id / submission_id / stage / state / status — it never fills stage outputs.
    """
    if request.existing_run_result is not None:
        run_result = request.existing_run_result.model_copy(deep=True)
        run_result.state = state
        run_result.stage = _stage_for_state(state)
        run_result.status = _run_status_for_state(state)
        return run_result

    if request.raw_submission is not None:
        submission_id = request.raw_submission.submission_id
    else:
        submission_id = request.run_id

    return PrintWorkflowRunResult(
        run_id=request.run_id,
        submission_id=submission_id,
        stage=_stage_for_state(state),
        state=state,
        status=_run_status_for_state(state),
        raw_submission=request.raw_submission,
    )


def _result(
    request: WorkflowAdvanceRequest,
    *,
    previous_state: PrintWorkflowState,
    current_state: PrintWorkflowState,
    status: ResultStatus,
    stopped: bool,
    transition_check: Optional[TransitionCheckResult] = None,
    stop_reason: Optional[str] = None,
    next_steps: Optional[str] = None,
    reasons: Optional[list] = None,
    subsystem_records: Optional[List[SubsystemExecutionRecord]] = None,
    normalization: Optional[NormalizationResult] = None,
    specification: Optional[PrintSpecification] = None,
) -> WorkflowAdvanceResult:
    """Assemble a WorkflowAdvanceResult with a partial run bundle attached."""
    run_result = _build_partial_run_result(request, current_state)
    if normalization is not None:
        run_result.normalization = normalization
    if specification is not None:
        run_result.specification = specification
    return WorkflowAdvanceResult(
        run_id=request.run_id,
        idempotency_key=request.idempotency_key,
        operation=request.operation,
        previous_state=previous_state,
        current_state=current_state,
        transition_check=transition_check,
        run_result=run_result,
        subsystem_records=subsystem_records or [],
        stopped=stopped,
        stop_reason=stop_reason,
        status=status,
        reasons=reasons or [],
        next_steps=next_steps,
    )


def _check(
    from_state: PrintWorkflowState,
    to_state: PrintWorkflowState,
) -> TransitionCheckResult:
    """Record (do not enforce) the legality of a from->to transition."""
    allowed = can_transition(from_state, to_state)
    return TransitionCheckResult(
        from_state=from_state,
        to_state=to_state,
        allowed=allowed,
        decision=TransitionDecision.ALLOWED if allowed else TransitionDecision.REJECTED,
        reason=(
            f"Transition {from_state.value} -> {to_state.value} is legal"
            if allowed
            else f"Transition {from_state.value} -> {to_state.value} is not a legal transition"
        ),
        allowed_transitions=sorted(
            get_allowed_transitions(from_state), key=lambda s: s.value
        ),
    )


def _latency_ms(start: datetime, end: datetime) -> int:
    """Whole-millisecond latency between two timestamps (never negative)."""
    return max(0, int((end - start).total_seconds() * 1000))


def _advance_normalization(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 1 wiring: call the normalization service and derive the next state.

    The spine only invokes the subsystem and routes on its returned status — it
    performs no normalization logic itself (no product/brief/asset inspection).
    """
    current = request.current_state
    raw = request.raw_submission
    input_ids = [raw.submission_id] if raw and raw.submission_id else []

    started_at = datetime.utcnow()
    try:
        normalization = normalize_submission(raw)
    except Exception as exc:  # subsystem failure -> NORMALIZATION_FAILED
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="NormalizationService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="NORMALIZATION_EXCEPTION",
            error_message=str(exc) or "Normalization raised an exception",
        )
        return _result(
            request,
            previous_state=current,
            current_state=S.NORMALIZATION_FAILED,
            status=ResultStatus.FAILED,
            stopped=True,
            transition_check=_check(current, S.NORMALIZATION_FAILED),
            stop_reason="Normalization service raised an exception",
            next_steps="Investigate the normalization failure and retry.",
            reasons=[
                StageIssue(
                    code="NORMALIZATION_EXCEPTION",
                    message=str(exc) or "Normalization raised an exception",
                )
            ],
            subsystem_records=[record],
        )

    completed_at = datetime.utcnow()
    output_ids = (
        [normalization.design_job.job_id]
        if normalization.design_job is not None
        else []
    )
    record = SubsystemExecutionRecord(
        subsystem_name="NormalizationService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    if normalization.status == ResultStatus.PASSED:
        target = S.NORMALIZED
        return _result(
            request,
            previous_state=current,
            current_state=target,
            status=_run_status_for_state(target),
            stopped=False,
            transition_check=_check(current, target),
            subsystem_records=[record],
            normalization=normalization,
        )

    # Any non-PASSED outcome routes to human review.
    target = S.NORMALIZATION_NEEDS_REVIEW
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=ResultStatus.NEEDS_REVIEW,
        stopped=True,
        transition_check=_check(current, target),
        stop_reason="Normalization requires human review",
        next_steps=(
            normalization.next_steps
            or "A human must review the submission before it can proceed."
        ),
        subsystem_records=[record],
        normalization=normalization,
    )


def _specification_failure(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_message: str,
    error_code: str,
) -> WorkflowAdvanceResult:
    """Build a SPECIFICATION_FAILED result (specification stays None)."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.SPECIFICATION_FAILED,
        status=ResultStatus.FAILED,
        stopped=True,
        transition_check=_check(current, S.SPECIFICATION_FAILED),
        stop_reason="Specification resolution failed",
        next_steps="Investigate the specification failure and retry.",
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_specification(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 2 wiring: call the specification service and derive the next state.

    The spine only invokes the subsystem and routes on its outcome — it does not
    resolve specs, inspect product rules, or build any requirement objects.
    """
    current = request.current_state
    run = request.existing_run_result
    design_job = (
        run.normalization.design_job
        if run is not None and run.normalization is not None
        else None
    )

    started_at = datetime.utcnow()

    # Guard: a DesignJob from normalization is required to resolve a spec.
    if design_job is None:
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="SpecificationResolutionService",
            input_contract_ids=[],
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_DESIGN_JOB",
            error_message="No DesignJob available from normalization to resolve a specification",
        )
        return _specification_failure(
            request,
            record=record,
            error_message=record.error_message,
            error_code="MISSING_DESIGN_JOB",
        )

    input_ids = [design_job.job_id]
    try:
        specification = resolve_specification(design_job)
    except Exception as exc:  # subsystem failure -> SPECIFICATION_FAILED
        completed_at = datetime.utcnow()
        message = str(exc) or "Specification resolution raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="SpecificationResolutionService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="SPECIFICATION_EXCEPTION",
            error_message=message,
        )
        return _specification_failure(
            request,
            record=record,
            error_message=message,
            error_code="SPECIFICATION_EXCEPTION",
        )

    completed_at = datetime.utcnow()
    record = SubsystemExecutionRecord(
        subsystem_name="SpecificationResolutionService",
        input_contract_ids=input_ids,
        output_contract_ids=[specification.spec_id],
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    target = S.SPECIFICATION_RESOLVED
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=_run_status_for_state(target),
        stopped=False,
        transition_check=_check(current, target),
        subsystem_records=[record],
        specification=specification,
    )


def advance_workflow(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Advance a print workflow by one step (Phase 0 shell).

    Behavior:
      1. Terminal current_state  -> stop, no advance.
      2. Human-review state      -> stop, no advance.
      3. requested_target_state given:
           - illegal -> reject (allowed=False, status=FAILED, stopped=True)
           - legal   -> advance to target (allowed=True)
      4. No requested_target_state -> no-op; target is required in Phase 0.

    Never calls a subsystem and always returns a partial PrintWorkflowRunResult.
    """
    current = request.current_state

    # 1. Terminal states: nothing to do.
    if is_terminal_state(current):
        return _result(
            request,
            previous_state=current,
            current_state=current,
            status=_run_status_for_state(current),
            stopped=True,
            stop_reason=f"Run is in terminal state '{current.value}'",
            next_steps="Run has ended; start a new run to do more work.",
        )

    # 2. Human-review states: hand control back to a human.
    if is_human_review_state(current):
        return _result(
            request,
            previous_state=current,
            current_state=current,
            status=ResultStatus.NEEDS_REVIEW,
            stopped=True,
            stop_reason=f"Run is awaiting human action in state '{current.value}'",
            next_steps="A human decision/action is required before the run can advance.",
        )

    # Phase 1: normalization wiring. When asked to advance from
    # NORMALIZATION_PENDING with a submission and no explicit target, call the
    # normalization subsystem and route on its result.
    if (
        current == S.NORMALIZATION_PENDING
        and request.requested_target_state is None
        and request.raw_submission is not None
    ):
        return _advance_normalization(request)

    # Phase 2: specification wiring. When asked to advance from NORMALIZED with
    # no explicit target, resolve the specification from the run's DesignJob.
    if current == S.NORMALIZED and request.requested_target_state is None:
        return _advance_specification(request)

    allowed_transitions = sorted(
        get_allowed_transitions(current), key=lambda s: s.value
    )

    # 5. No explicit target: Phase 0 does not infer the next state.
    if request.requested_target_state is None:
        return _result(
            request,
            previous_state=current,
            current_state=current,
            status=ResultStatus.PENDING,
            stopped=True,
            stop_reason="No requested_target_state provided",
            next_steps=(
                "Phase 0 requires an explicit requested_target_state. "
                f"Legal targets: {[s.value for s in allowed_transitions]}"
            ),
        )

    target = request.requested_target_state
    allowed = can_transition(current, target)

    transition_check = TransitionCheckResult(
        from_state=current,
        to_state=target,
        allowed=allowed,
        decision=TransitionDecision.ALLOWED if allowed else TransitionDecision.REJECTED,
        reason=(
            f"Transition {current.value} -> {target.value} is legal"
            if allowed
            else f"Transition {current.value} -> {target.value} is not a legal transition"
        ),
        allowed_transitions=allowed_transitions,
    )

    # 3. Illegal transition: reject without advancing.
    if not allowed:
        return _result(
            request,
            previous_state=current,
            current_state=current,
            status=ResultStatus.FAILED,
            stopped=True,
            transition_check=transition_check,
            stop_reason="Requested transition is illegal",
            next_steps=(
                f"Choose a legal target. Legal targets: "
                f"{[s.value for s in allowed_transitions]}"
            ),
            reasons=[
                StageIssue(
                    code="ILLEGAL_TRANSITION",
                    message=transition_check.reason,
                    field="requested_target_state",
                )
            ],
        )

    # 4. Legal transition: advance to the requested target.
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=_run_status_for_state(target),
        stopped=is_terminal_state(target) or is_human_review_state(target),
        transition_check=transition_check,
        next_steps=None,
    )
