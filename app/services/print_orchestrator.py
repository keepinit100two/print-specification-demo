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
    AdaptationPlan,
    ApprovalDecision,
    ApprovalPackage,
    ApprovalStatus,
    AssetRole,
    ComplianceResult,
    GeneratedCandidate,
    GenerationRequest,
    ModelInvocationRecord,
    NormalizationResult,
    PrintSpecification,
    ProductionPackage,
    PrintWorkflowRunResult,
    PrintWorkflowStage,
    PrintWorkflowState,
    ResultStatus,
    StageIssue,
    TransformedAsset,
    ValidationResult,
)
from app.domain.print_state_machine import (
    can_transition,
    get_allowed_transitions,
    is_human_review_state,
    is_terminal_state,
)
from app.services.print_adaptation import create_adaptation_plan
from app.services.print_approval import create_approval_package, record_approval_decision
from app.services.print_compliance import evaluate_compliance
from app.services.print_deterministic_transform import execute_deterministic_transforms
from app.services.print_generation import generate_candidates
from app.services.print_normalization import normalize_submission
from app.services.print_production_packaging import create_production_package
from app.services.print_prompt_construction import build_generation_request
from app.services.print_specification import resolve_specification
from app.services.print_validation import validate_print_asset

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
    S.DETERMINISTIC_TRANSFORM_PENDING: PrintWorkflowStage.ADAPTATION,
    S.DETERMINISTIC_TRANSFORM_COMPLETE: PrintWorkflowStage.ADAPTATION,
    S.DETERMINISTIC_TRANSFORM_FAILED: PrintWorkflowStage.ADAPTATION,
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
    transition_checks: Optional[List[TransitionCheckResult]] = None,
    stop_reason: Optional[str] = None,
    next_steps: Optional[str] = None,
    reasons: Optional[list] = None,
    subsystem_records: Optional[List[SubsystemExecutionRecord]] = None,
    normalization: Optional[NormalizationResult] = None,
    specification: Optional[PrintSpecification] = None,
    compliance: Optional[ComplianceResult] = None,
    adaptation: Optional[AdaptationPlan] = None,
    generation_request: Optional[GenerationRequest] = None,
    candidates: Optional[List[GeneratedCandidate]] = None,
    model_invocations: Optional[List[ModelInvocationRecord]] = None,
    validation: Optional[ValidationResult] = None,
    approval_package: Optional[ApprovalPackage] = None,
    approval: Optional[ApprovalDecision] = None,
    production_package: Optional[ProductionPackage] = None,
) -> WorkflowAdvanceResult:
    """Assemble a WorkflowAdvanceResult with a partial run bundle attached."""
    run_result = _build_partial_run_result(request, current_state)
    if normalization is not None:
        run_result.normalization = normalization
    if specification is not None:
        run_result.specification = specification
    if compliance is not None:
        run_result.compliance = compliance
    if adaptation is not None:
        run_result.adaptation = adaptation
    if generation_request is not None:
        run_result.generation_request = generation_request
    if candidates is not None:
        run_result.candidates = candidates
    if model_invocations is not None:
        run_result.model_invocations = model_invocations
    if validation is not None:
        run_result.validation = validation
    if approval_package is not None:
        run_result.approval_package = approval_package
    if approval is not None:
        run_result.approval = approval
    if production_package is not None:
        run_result.production_package = production_package

    # `transition_checks` is the source of truth for all movements in this step.
    # `transition_check` mirrors the final movement for backward compatibility.
    if transition_checks is None:
        checks = [transition_check] if transition_check is not None else []
    else:
        checks = list(transition_checks)
    final_check = checks[-1] if checks else None

    return WorkflowAdvanceResult(
        run_id=request.run_id,
        idempotency_key=request.idempotency_key,
        operation=request.operation,
        previous_state=previous_state,
        current_state=current_state,
        transition_check=final_check,
        transition_checks=checks,
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
        transition_checks=[
            _check(current, S.SPECIFICATION_PENDING),
            _check(S.SPECIFICATION_PENDING, S.SPECIFICATION_FAILED),
        ],
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
        transition_checks=[
            _check(current, S.SPECIFICATION_PENDING),
            _check(S.SPECIFICATION_PENDING, target),
        ],
        subsystem_records=[record],
        specification=specification,
    )


def _primary_image_properties(run: Optional[PrintWorkflowRunResult]):
    """Return ImageProperties of the primary submitted asset, or None."""
    if run is None or run.raw_submission is None:
        return None
    for asset in run.raw_submission.assets:
        if asset.role == AssetRole.PRIMARY:
            return asset.properties
    return None


def _compliance_failed(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
) -> WorkflowAdvanceResult:
    """Build a COMPLIANCE_FAILED result for a missing-input or exception failure."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.COMPLIANCE_FAILED,
        status=ResultStatus.FAILED,
        stopped=True,
        transition_checks=[
            _check(current, S.COMPLIANCE_PENDING),
            _check(S.COMPLIANCE_PENDING, S.COMPLIANCE_FAILED),
        ],
        stop_reason="Compliance evaluation failed",
        next_steps="Investigate the compliance failure and retry.",
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_compliance(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 3 wiring: call the compliance service and route on its result.

    The spine only invokes the subsystem and routes on its outcome — it does not
    measure compliance, compute DPI/dimensions/formats, or build findings.
    """
    current = request.current_state
    run = request.existing_run_result
    design_job = (
        run.normalization.design_job
        if run is not None and run.normalization is not None
        else None
    )
    specification = run.specification if run is not None else None
    image_properties = _primary_image_properties(run)

    input_ids = [
        cid
        for cid in (
            getattr(design_job, "job_id", None),
            getattr(specification, "spec_id", None),
        )
        if cid
    ]

    started_at = datetime.utcnow()

    # Guard: required inputs must be present.
    if design_job is None or specification is None or image_properties is None:
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="TechnicalComplianceService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_COMPLIANCE_INPUTS",
            error_message=(
                "Missing required inputs for compliance (design_job, specification, "
                "or primary asset image properties)"
            ),
        )
        return _compliance_failed(
            request,
            record=record,
            error_code="MISSING_COMPLIANCE_INPUTS",
            error_message=record.error_message,
        )

    try:
        compliance = evaluate_compliance(design_job, specification, image_properties)
    except Exception as exc:  # subsystem failure -> COMPLIANCE_FAILED
        completed_at = datetime.utcnow()
        message = str(exc) or "Compliance evaluation raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="TechnicalComplianceService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="COMPLIANCE_EXCEPTION",
            error_message=message,
        )
        return _compliance_failed(
            request,
            record=record,
            error_code="COMPLIANCE_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    record = SubsystemExecutionRecord(
        subsystem_name="TechnicalComplianceService",
        input_contract_ids=input_ids,
        output_contract_ids=[],
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    # NEEDS_REVIEW (e.g. missing image metadata) -> stop for human review.
    if compliance.status == ResultStatus.NEEDS_REVIEW:
        target = S.COMPLIANCE_FAILED
        return _result(
            request,
            previous_state=current,
            current_state=target,
            status=ResultStatus.NEEDS_REVIEW,
            stopped=True,
            transition_checks=[
                _check(current, S.COMPLIANCE_PENDING),
                _check(S.COMPLIANCE_PENDING, target),
            ],
            stop_reason="Compliance requires human review",
            next_steps=(
                compliance.next_steps
                or "Compliance could not be measured; human review required."
            ),
            subsystem_records=[record],
            compliance=compliance,
        )

    # PASSED or FAILED: the measurement succeeded. FAILED simply means the asset
    # is not print-ready — it is not a workflow failure.
    target = S.COMPLIANCE_COMPLETE
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=_run_status_for_state(target),
        stopped=False,
        transition_checks=[
            _check(current, S.COMPLIANCE_PENDING),
            _check(S.COMPLIANCE_PENDING, target),
        ],
        subsystem_records=[record],
        compliance=compliance,
    )


def _adaptation_failed(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
) -> WorkflowAdvanceResult:
    """Build a FAILED result for a missing-input or exception adaptation failure.

    The state machine has no dedicated ADAPTATION_FAILED state; the only legal
    failure target from COMPLIANCE_COMPLETE is the terminal FAILED state.
    """
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.FAILED,
        status=ResultStatus.FAILED,
        stopped=True,
        transition_checks=[_check(current, S.FAILED)],
        stop_reason="Adaptation planning failed",
        next_steps="Investigate the adaptation failure and retry.",
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_adaptation(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 4 wiring: decide whether adaptation is required and, if so, call the
    adaptation planning service.

    The spine only checks compliance.is_print_ready as a routing flag and invokes
    the subsystem — it does not inspect findings or plan transformations itself.
    """
    current = request.current_state
    run = request.existing_run_result
    design_job = (
        run.normalization.design_job
        if run is not None and run.normalization is not None
        else None
    )
    specification = run.specification if run is not None else None
    compliance = run.compliance if run is not None else None

    input_ids = [
        cid
        for cid in (
            getattr(design_job, "job_id", None),
            getattr(specification, "spec_id", None),
        )
        if cid
    ]

    started_at = datetime.utcnow()

    # Guard: required inputs must be present.
    if design_job is None or specification is None or compliance is None:
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="AdaptationPlanningService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_ADAPTATION_INPUTS",
            error_message=(
                "Missing required inputs for adaptation planning (design_job, "
                "specification, or compliance result)"
            ),
        )
        return _adaptation_failed(
            request,
            record=record,
            error_code="MISSING_ADAPTATION_INPUTS",
            error_message=record.error_message,
        )

    # Routing flag only: a print-ready asset needs no adaptation. Do not call the
    # subsystem and do not create an AdaptationPlan.
    if compliance.is_print_ready:
        return _result(
            request,
            previous_state=current,
            current_state=current,
            status=ResultStatus.PENDING,
            stopped=True,
            stop_reason="Asset is print-ready; no adaptation required",
            next_steps="No adaptation required; proceed to production packaging.",
        )

    try:
        adaptation = create_adaptation_plan(design_job, specification, compliance)
    except Exception as exc:  # subsystem failure -> FAILED
        completed_at = datetime.utcnow()
        message = str(exc) or "Adaptation planning raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="AdaptationPlanningService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="ADAPTATION_EXCEPTION",
            error_message=message,
        )
        return _adaptation_failed(
            request,
            record=record,
            error_code="ADAPTATION_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = (
        [adaptation.plan_id] if getattr(adaptation, "plan_id", None) else []
    )
    record = SubsystemExecutionRecord(
        subsystem_name="AdaptationPlanningService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    target = S.ADAPTATION_PLANNED
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=_run_status_for_state(target),
        stopped=False,
        transition_checks=[_check(current, target)],
        subsystem_records=[record],
        adaptation=adaptation,
    )


def _prompt_failed(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
) -> WorkflowAdvanceResult:
    """Build a FAILED result for a missing-input or exception prompt failure."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.FAILED,
        status=ResultStatus.FAILED,
        stopped=True,
        transition_checks=[_check(current, S.FAILED)],
        stop_reason="Prompt construction failed",
        next_steps="Investigate the prompt construction failure and retry.",
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_prompt(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 5 wiring: build a GenerationRequest when adaptation requires generation.

    The spine only checks adaptation.requires_generation as a routing flag and
    invokes the subsystem — it does not construct prompt text itself.
    """
    current = request.current_state
    run = request.existing_run_result
    design_job = (
        run.normalization.design_job
        if run is not None and run.normalization is not None
        else None
    )
    specification = run.specification if run is not None else None
    adaptation = run.adaptation if run is not None else None

    input_ids = [
        cid
        for cid in (
            getattr(design_job, "job_id", None),
            getattr(specification, "spec_id", None),
            getattr(adaptation, "plan_id", None),
        )
        if cid
    ]

    started_at = datetime.utcnow()

    # Guard: required inputs must be present.
    if design_job is None or specification is None or adaptation is None:
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="PromptConstructionService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_PROMPT_INPUTS",
            error_message=(
                "Missing required inputs for prompt construction (design_job, "
                "specification, or adaptation plan)"
            ),
        )
        return _prompt_failed(
            request,
            record=record,
            error_code="MISSING_PROMPT_INPUTS",
            error_message=record.error_message,
        )

    # Routing flag only: no generation needed. Do not call the subsystem.
    if not adaptation.requires_generation:
        return _result(
            request,
            previous_state=current,
            current_state=current,
            status=ResultStatus.PENDING,
            stopped=True,
            stop_reason="Adaptation does not require generation",
            next_steps="No generation request is needed; proceed without prompt construction.",
        )

    try:
        generation_request = build_generation_request(
            design_job, specification, adaptation
        )
    except Exception as exc:  # subsystem failure -> FAILED
        completed_at = datetime.utcnow()
        message = str(exc) or "Prompt construction raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="PromptConstructionService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="PROMPT_EXCEPTION",
            error_message=message,
        )
        return _prompt_failed(
            request,
            record=record,
            error_code="PROMPT_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = (
        [generation_request.request_id]
        if getattr(generation_request, "request_id", None)
        else []
    )
    record = SubsystemExecutionRecord(
        subsystem_name="PromptConstructionService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    target = S.GENERATION_PENDING
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=_run_status_for_state(target),
        stopped=False,
        transition_checks=[_check(current, target)],
        subsystem_records=[record],
        generation_request=generation_request,
    )


def _generation_macro_checks(
    current: PrintWorkflowState,
    target: PrintWorkflowState,
) -> List[TransitionCheckResult]:
    """Record GENERATION_PENDING -> GENERATION_RUNNING -> target."""
    return [
        _check(current, S.GENERATION_RUNNING),
        _check(S.GENERATION_RUNNING, target),
    ]


def _generation_failed(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
    candidates: Optional[List[GeneratedCandidate]] = None,
    model_invocations: Optional[List[ModelInvocationRecord]] = None,
    next_steps: Optional[str] = None,
) -> WorkflowAdvanceResult:
    """Build a GENERATION_FAILED result for missing inputs, empty output, or exceptions."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.GENERATION_FAILED,
        status=ResultStatus.FAILED,
        stopped=True,
        transition_checks=_generation_macro_checks(current, S.GENERATION_FAILED),
        stop_reason="AI generation failed",
        next_steps=next_steps or "Investigate the generation failure and retry.",
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
        candidates=candidates if candidates is not None else [],
        model_invocations=model_invocations if model_invocations is not None else [],
    )


def _advance_generation(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 6 wiring: call the AI generation actuator and route on its outputs.

    The spine only invokes generate_candidates — it does not inspect candidate
    image content or call model providers directly.
    """
    current = request.current_state
    run = request.existing_run_result
    generation_request = (
        run.generation_request if run is not None else None
    )

    input_ids = (
        [generation_request.request_id]
        if generation_request is not None
        and getattr(generation_request, "request_id", None)
        else []
    )

    started_at = datetime.utcnow()

    if run is None or generation_request is None:
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="AIGenerationService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_GENERATION_INPUTS",
            error_message=(
                "Missing required inputs for generation (existing_run_result or "
                "generation_request)"
            ),
        )
        return _generation_failed(
            request,
            record=record,
            error_code="MISSING_GENERATION_INPUTS",
            error_message=record.error_message,
        )

    try:
        candidates, invocations = generate_candidates(generation_request)
    except Exception as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "AI generation raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="AIGenerationService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="GENERATION_EXCEPTION",
            error_message=message,
        )
        return _generation_failed(
            request,
            record=record,
            error_code="GENERATION_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = [c.candidate_id for c in candidates] if candidates else []

    if not candidates:
        record = SubsystemExecutionRecord(
            subsystem_name="AIGenerationService",
            input_contract_ids=input_ids,
            output_contract_ids=output_ids,
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="NO_CANDIDATES",
            error_message="Generation produced no candidates",
        )
        return _generation_failed(
            request,
            record=record,
            error_code="NO_CANDIDATES",
            error_message=record.error_message,
            model_invocations=invocations,
            next_steps="Generation produced no candidates; review the request and retry.",
        )

    record = SubsystemExecutionRecord(
        subsystem_name="AIGenerationService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    target = S.GENERATION_COMPLETE
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=ResultStatus.PENDING,
        stopped=False,
        transition_checks=_generation_macro_checks(current, target),
        subsystem_records=[record],
        candidates=candidates,
        model_invocations=invocations,
    )


def _deterministic_transform_failed(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
    status: ResultStatus = ResultStatus.FAILED,
    reasons: Optional[List[StageIssue]] = None,
    next_steps: Optional[str] = None,
    stop_reason: Optional[str] = None,
) -> WorkflowAdvanceResult:
    """Build a DETERMINISTIC_TRANSFORM_FAILED result for missing inputs, review, or exceptions."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.DETERMINISTIC_TRANSFORM_FAILED,
        status=status,
        stopped=True,
        transition_checks=[_check(current, S.DETERMINISTIC_TRANSFORM_FAILED)],
        stop_reason=stop_reason or "Deterministic transform failed",
        next_steps=next_steps or "Investigate the deterministic transform failure and retry.",
        reasons=reasons or [StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_deterministic_transform(
    request: WorkflowAdvanceRequest,
) -> WorkflowAdvanceResult:
    """
    Phase 7 wiring: call the deterministic transform service and route on its result.

    The spine only invokes execute_deterministic_transforms — it does not perform
    image processing, inspect transformation contents, or call AI.
    """
    current = request.current_state
    run = request.existing_run_result
    design_job = (
        run.normalization.design_job
        if run is not None and run.normalization is not None
        else None
    )
    specification = run.specification if run is not None else None
    adaptation = run.adaptation if run is not None else None

    input_ids = [
        cid
        for cid in (
            getattr(design_job, "job_id", None),
            getattr(specification, "spec_id", None),
            getattr(adaptation, "plan_id", None),
        )
        if cid
    ]

    started_at = datetime.utcnow()

    if (
        run is None
        or design_job is None
        or specification is None
        or adaptation is None
    ):
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="DeterministicTransformService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_DETERMINISTIC_TRANSFORM_INPUTS",
            error_message=(
                "Missing required inputs for deterministic transforms "
                "(existing_run_result, design_job, specification, or adaptation)"
            ),
        )
        return _deterministic_transform_failed(
            request,
            record=record,
            error_code="MISSING_DETERMINISTIC_TRANSFORM_INPUTS",
            error_message=record.error_message,
        )

    try:
        transform_result = execute_deterministic_transforms(
            design_job, specification, adaptation
        )
    except Exception as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "Deterministic transform raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="DeterministicTransformService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="DETERMINISTIC_TRANSFORM_EXCEPTION",
            error_message=message,
        )
        return _deterministic_transform_failed(
            request,
            record=record,
            error_code="DETERMINISTIC_TRANSFORM_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = [transform_result.result_id] if transform_result.result_id else []
    output_ids.extend(
        asset.transformed_asset_id
        for asset in transform_result.transformed_assets
        if getattr(asset, "transformed_asset_id", None)
    )

    record = SubsystemExecutionRecord(
        subsystem_name="DeterministicTransformService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    if transform_result.status == ResultStatus.NEEDS_REVIEW:
        return _deterministic_transform_failed(
            request,
            record=record,
            error_code="DETERMINISTIC_TRANSFORM_NEEDS_REVIEW",
            error_message="Deterministic transform requires human review",
            status=ResultStatus.NEEDS_REVIEW,
            reasons=transform_result.reasons,
            next_steps=transform_result.next_steps,
            stop_reason="Deterministic transform requires human review",
        )

    if transform_result.status not in (ResultStatus.PASSED, ResultStatus.SKIPPED):
        return _deterministic_transform_failed(
            request,
            record=record,
            error_code="DETERMINISTIC_TRANSFORM_FAILED",
            error_message=(
                f"Deterministic transform returned unexpected status: "
                f"{transform_result.status.value}"
            ),
            reasons=transform_result.reasons,
            next_steps=transform_result.next_steps,
        )

    target = S.DETERMINISTIC_TRANSFORM_COMPLETE
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=_run_status_for_state(target),
        stopped=False,
        transition_checks=[_check(current, target)],
        subsystem_records=[record],
        next_steps=transform_result.next_steps,
    )


def _validation_macro_checks(
    source_state: PrintWorkflowState,
    target_state: PrintWorkflowState,
) -> List[TransitionCheckResult]:
    """Record source_state -> VALIDATION_PENDING -> target."""
    return [
        _check(source_state, S.VALIDATION_PENDING),
        _check(S.VALIDATION_PENDING, target_state),
    ]


def _validation_output_id(asset: GeneratedCandidate | TransformedAsset) -> str:
    """Return the stable output id used for validation subsystem records."""
    if isinstance(asset, GeneratedCandidate):
        return asset.candidate_id
    return asset.transformed_asset_id


def _resolve_validation_asset(
    run: PrintWorkflowRunResult,
    current_state: PrintWorkflowState,
) -> GeneratedCandidate | TransformedAsset | None:
    """
    Select the output asset to validate for the current complete state.

    GENERATION_COMPLETE uses the first candidate. DETERMINISTIC_TRANSFORM_COMPLETE
    prefers deterministic_transform.transformed_assets when the run bundle carries
    them; until that field exists, falls back to the first candidate placeholder
    used by orchestration wiring tests.
    """
    if current_state == S.GENERATION_COMPLETE:
        return run.candidates[0] if run.candidates else None

    if current_state == S.DETERMINISTIC_TRANSFORM_COMPLETE:
        deterministic_transform = getattr(run, "deterministic_transform", None)
        if deterministic_transform is not None:
            transformed_assets = getattr(deterministic_transform, "transformed_assets", None)
            if transformed_assets:
                return transformed_assets[0]
        # Placeholder path until PrintWorkflowRunResult exposes deterministic output.
        return run.candidates[0] if run.candidates else None

    return None


def _validation_failed(
    request: WorkflowAdvanceRequest,
    *,
    source_state: PrintWorkflowState,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
    status: ResultStatus = ResultStatus.FAILED,
    validation: Optional[ValidationResult] = None,
    reasons: Optional[List[StageIssue]] = None,
    next_steps: Optional[str] = None,
    stop_reason: Optional[str] = None,
) -> WorkflowAdvanceResult:
    """Build a VALIDATION_FAILED result for missing inputs, review, failure, or exceptions."""
    return _result(
        request,
        previous_state=source_state,
        current_state=S.VALIDATION_FAILED,
        status=status,
        stopped=True,
        transition_checks=_validation_macro_checks(source_state, S.VALIDATION_FAILED),
        stop_reason=stop_reason or "Print validation failed",
        next_steps=next_steps or "Investigate the validation failure and retry.",
        reasons=reasons or [StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
        validation=validation,
    )


def _advance_validation(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 8 wiring: call the print validation service and route on its result.

    The spine only invokes validate_print_asset — it does not inspect image
    content, call AI, or perform file I/O.
    """
    current = request.current_state
    run = request.existing_run_result
    specification = run.specification if run is not None else None
    asset = _resolve_validation_asset(run, current) if run is not None else None

    input_ids = [
        cid
        for cid in (
            getattr(specification, "spec_id", None),
            _validation_output_id(asset) if asset is not None else None,
        )
        if cid
    ]

    started_at = datetime.utcnow()

    if run is None or specification is None or asset is None:
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="PrintValidationService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_VALIDATION_INPUTS",
            error_message=(
                "Missing required inputs for validation (existing_run_result, "
                "specification, or output asset)"
            ),
        )
        return _validation_failed(
            request,
            source_state=current,
            record=record,
            error_code="MISSING_VALIDATION_INPUTS",
            error_message=record.error_message,
        )

    try:
        validation_result = validate_print_asset(specification, asset)
    except Exception as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "Print validation raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="PrintValidationService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="VALIDATION_EXCEPTION",
            error_message=message,
        )
        return _validation_failed(
            request,
            source_state=current,
            record=record,
            error_code="VALIDATION_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = list(validation_result.validated_candidate_ids or [])
    record = SubsystemExecutionRecord(
        subsystem_name="PrintValidationService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    if validation_result.status == ResultStatus.PASSED:
        target = S.VALIDATION_COMPLETE
        return _result(
            request,
            previous_state=current,
            current_state=target,
            status=ResultStatus.PENDING,
            stopped=False,
            transition_checks=_validation_macro_checks(current, target),
            subsystem_records=[record],
            validation=validation_result,
            next_steps=validation_result.next_steps,
        )

    if validation_result.status == ResultStatus.NEEDS_REVIEW:
        return _validation_failed(
            request,
            source_state=current,
            record=record,
            error_code="VALIDATION_NEEDS_REVIEW",
            error_message="Print validation requires human review",
            status=ResultStatus.NEEDS_REVIEW,
            validation=validation_result,
            reasons=validation_result.reasons,
            next_steps=validation_result.next_steps,
            stop_reason="Print validation requires human review",
        )

    return _validation_failed(
        request,
        source_state=current,
        record=record,
        error_code="VALIDATION_FAILED",
        error_message="Print validation did not pass",
        status=ResultStatus.FAILED,
        validation=validation_result,
        reasons=validation_result.reasons,
        next_steps=validation_result.next_steps,
        stop_reason="Print validation did not pass",
    )


def _approval_failed(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
) -> WorkflowAdvanceResult:
    """Build a terminal FAILED result for approval package routing failures."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.FAILED,
        status=ResultStatus.FAILED,
        stopped=True,
        transition_checks=[_check(current, S.FAILED)],
        stop_reason="Approval package routing failed",
        next_steps="Investigate the approval routing failure and retry.",
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_approval(request: WorkflowAdvanceRequest) -> WorkflowAdvanceResult:
    """
    Phase 9 wiring: assemble an ApprovalPackage after validation passes.

    The spine only invokes create_approval_package — it does not record
    ApprovalDecision, create ProductionPackage, call AI, or perform file I/O.
    """
    current = request.current_state
    run = request.existing_run_result
    validation = run.validation if run is not None else None
    specification = run.specification if run is not None else None
    design_job = (
        run.normalization.design_job
        if run is not None and run.normalization is not None
        else None
    )
    outputs = run.candidates if run is not None else []

    input_ids = [
        cid
        for cid in (
            getattr(validation, "spec_id", None),
            getattr(specification, "spec_id", None),
            getattr(design_job, "job_id", None),
        )
        if cid
    ]

    started_at = datetime.utcnow()

    if (
        run is None
        or validation is None
        or specification is None
        or design_job is None
    ):
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="ApprovalWorkflowService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_APPROVAL_INPUTS",
            error_message=(
                "Missing required inputs for approval routing (existing_run_result, "
                "validation, specification, or design_job)"
            ),
        )
        return _approval_failed(
            request,
            record=record,
            error_code="MISSING_APPROVAL_INPUTS",
            error_message=record.error_message,
        )

    try:
        package = create_approval_package(
            validation,
            outputs,
            specification,
            design_job,
        )
    except Exception as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "Approval package routing raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="ApprovalWorkflowService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="APPROVAL_EXCEPTION",
            error_message=message,
        )
        return _approval_failed(
            request,
            record=record,
            error_code="APPROVAL_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = [package.package_id] if package.package_id else []
    record = SubsystemExecutionRecord(
        subsystem_name="ApprovalWorkflowService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    target = S.OWNER_REVIEW_PENDING
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=ResultStatus.NEEDS_REVIEW,
        stopped=True,
        transition_checks=[_check(current, target)],
        stop_reason="Run is awaiting owner review",
        next_steps=package.next_steps or "A human approval decision is required.",
        subsystem_records=[record],
        approval_package=package,
    )


def _approval_decision_metadata(
    request: WorkflowAdvanceRequest,
) -> Optional[dict]:
    """Return approval decision metadata when present on the advance request."""
    metadata = request.metadata or {}
    decision = metadata.get("approval_decision")
    if not isinstance(decision, dict):
        return None
    return decision


def _parse_approval_status(raw_status: object) -> Optional[ApprovalStatus]:
    """Parse an ApprovalStatus from request metadata."""
    if raw_status is None:
        return None
    try:
        return ApprovalStatus(str(raw_status))
    except ValueError:
        return None


def _target_for_approval_status(status: ApprovalStatus) -> PrintWorkflowState:
    """Map a human approval outcome to the next workflow state."""
    if status == ApprovalStatus.APPROVED:
        return S.APPROVED
    if status == ApprovalStatus.REJECTED:
        return S.REJECTED
    if status == ApprovalStatus.CHANGES_REQUESTED:
        return S.REVISION_REQUESTED
    raise ValueError(f"Unsupported approval status: {status.value}")


def _approval_decision_stay_in_review(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
) -> WorkflowAdvanceResult:
    """Halt in OWNER_REVIEW_PENDING when decision handling cannot complete."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=current,
        status=ResultStatus.NEEDS_REVIEW,
        stopped=True,
        stop_reason=f"Run is awaiting human action in state '{current.value}'",
        next_steps=error_message,
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_approval_decision(
    request: WorkflowAdvanceRequest,
) -> WorkflowAdvanceResult:
    """
    Phase 9b wiring: record a human approval decision from request metadata.

    The spine only invokes record_approval_decision — it does not create
    ProductionPackage, call AI, or perform file I/O.
    """
    current = request.current_state
    run = request.existing_run_result
    decision = _approval_decision_metadata(request) or {}
    approval_package = run.approval_package if run is not None else None
    candidate_id = decision.get("candidate_id")
    approver = decision.get("approver")
    parsed_status = _parse_approval_status(decision.get("status"))

    input_ids = [
        cid
        for cid in (
            getattr(approval_package, "package_id", None),
            candidate_id if candidate_id else None,
        )
        if cid
    ]

    started_at = datetime.utcnow()

    if (
        run is None
        or approval_package is None
        or parsed_status is None
        or not approver
    ):
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="ApprovalDecisionService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_APPROVAL_DECISION_INPUTS",
            error_message=(
                "Missing required inputs for approval decision (existing_run_result, "
                "approval_package, decision status, or approver)"
            ),
        )
        return _approval_decision_stay_in_review(
            request,
            record=record,
            error_code="MISSING_APPROVAL_DECISION_INPUTS",
            error_message=record.error_message,
        )

    try:
        target = _target_for_approval_status(parsed_status)
    except ValueError as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "Unsupported approval decision status"
        record = SubsystemExecutionRecord(
            subsystem_name="ApprovalDecisionService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="UNSUPPORTED_APPROVAL_STATUS",
            error_message=message,
        )
        return _approval_decision_stay_in_review(
            request,
            record=record,
            error_code="UNSUPPORTED_APPROVAL_STATUS",
            error_message=message,
        )

    try:
        approval = record_approval_decision(
            approval_package,
            candidate_id,
            parsed_status,
            approver,
        )
    except Exception as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "Approval decision recording raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="ApprovalDecisionService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="APPROVAL_DECISION_EXCEPTION",
            error_message=message,
        )
        return _approval_decision_stay_in_review(
            request,
            record=record,
            error_code="APPROVAL_DECISION_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = [approval.decision_id] if approval.decision_id else []
    record = SubsystemExecutionRecord(
        subsystem_name="ApprovalDecisionService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=_run_status_for_state(target),
        stopped=True,
        transition_checks=[_check(current, target)],
        subsystem_records=[record],
        approval=approval,
        next_steps=approval.next_steps,
    )


def _packaging_output_id(asset: GeneratedCandidate | TransformedAsset) -> str:
    """Return the stable output id for a generated or transformed asset."""
    if isinstance(asset, GeneratedCandidate):
        return asset.candidate_id
    return asset.transformed_asset_id


def _iter_packaging_outputs(run: PrintWorkflowRunResult):
    """Yield generated and deterministically transformed outputs from a run bundle."""
    for candidate in run.candidates or []:
        yield candidate
    deterministic_transform = getattr(run, "deterministic_transform", None)
    if deterministic_transform is not None:
        for asset in getattr(deterministic_transform, "transformed_assets", None) or []:
            yield asset


def _resolve_approved_output_asset(
    run: PrintWorkflowRunResult,
    approved_output_id: str | None,
) -> GeneratedCandidate | TransformedAsset | None:
    """Find the approved output asset by id in candidates or transformed assets."""
    if not approved_output_id:
        return None
    for asset in _iter_packaging_outputs(run):
        if _packaging_output_id(asset) == approved_output_id:
            return asset
    return None


def _packaging_macro_checks(
    target_state: PrintWorkflowState,
) -> List[TransitionCheckResult]:
    """Record APPROVED -> PRODUCTION_PACKAGING_PENDING -> target."""
    return [
        _check(S.APPROVED, S.PRODUCTION_PACKAGING_PENDING),
        _check(S.PRODUCTION_PACKAGING_PENDING, target_state),
    ]


def _packaging_failed(
    request: WorkflowAdvanceRequest,
    *,
    record: SubsystemExecutionRecord,
    error_code: str,
    error_message: str,
) -> WorkflowAdvanceResult:
    """Build a terminal FAILED result for production packaging failures."""
    current = request.current_state
    return _result(
        request,
        previous_state=current,
        current_state=S.FAILED,
        status=ResultStatus.FAILED,
        stopped=True,
        transition_checks=_packaging_macro_checks(S.FAILED),
        stop_reason="Production packaging failed",
        next_steps="Investigate the production packaging failure and retry.",
        reasons=[StageIssue(code=error_code, message=error_message)],
        subsystem_records=[record],
    )


def _advance_production_packaging(
    request: WorkflowAdvanceRequest,
) -> WorkflowAdvanceResult:
    """
    Phase 10 wiring: assemble a ProductionPackage from an approved output.

    The spine only invokes create_production_package — it does not print, ship,
    call AI, or perform file I/O.
    """
    current = request.current_state
    run = request.existing_run_result
    approval = run.approval if run is not None else None
    specification = run.specification if run is not None else None
    validation = run.validation if run is not None else None
    approved_output_id = approval.candidate_id if approval is not None else None
    output_asset = (
        _resolve_approved_output_asset(run, approved_output_id)
        if run is not None
        else None
    )

    input_ids = [
        cid
        for cid in (
            getattr(approval, "decision_id", None),
            getattr(specification, "spec_id", None),
            getattr(validation, "spec_id", None),
            approved_output_id,
        )
        if cid
    ]

    started_at = datetime.utcnow()

    if (
        run is None
        or approval is None
        or specification is None
        or validation is None
        or output_asset is None
    ):
        completed_at = datetime.utcnow()
        record = SubsystemExecutionRecord(
            subsystem_name="ProductionPackagingService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="MISSING_PACKAGING_INPUTS",
            error_message=(
                "Missing required inputs for production packaging (existing_run_result, "
                "approval, specification, validation, or approved output asset)"
            ),
        )
        return _packaging_failed(
            request,
            record=record,
            error_code="MISSING_PACKAGING_INPUTS",
            error_message=record.error_message,
        )

    try:
        package = create_production_package(
            approval,
            specification,
            validation,
            output_asset,
        )
    except Exception as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "Production packaging raised an exception"
        record = SubsystemExecutionRecord(
            subsystem_name="ProductionPackagingService",
            input_contract_ids=input_ids,
            output_contract_ids=[],
            status=SubsystemExecutionStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=_latency_ms(started_at, completed_at),
            error_code="PACKAGING_EXCEPTION",
            error_message=message,
        )
        return _packaging_failed(
            request,
            record=record,
            error_code="PACKAGING_EXCEPTION",
            error_message=message,
        )

    completed_at = datetime.utcnow()
    output_ids = [package.package_id] if package.package_id else []
    record = SubsystemExecutionRecord(
        subsystem_name="ProductionPackagingService",
        input_contract_ids=input_ids,
        output_contract_ids=output_ids,
        status=SubsystemExecutionStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
    )

    target = S.PRODUCTION_PACKAGE_CREATED
    return _result(
        request,
        previous_state=current,
        current_state=target,
        status=ResultStatus.PENDING,
        stopped=False,
        transition_checks=_packaging_macro_checks(target),
        subsystem_records=[record],
        production_package=package,
        next_steps="Production package assembled; advance to completion when ready.",
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

    # Phase 9b: approval decision handling. When owner review supplies decision
    # metadata, record the human outcome before the generic human-review stop.
    if (
        current == S.OWNER_REVIEW_PENDING
        and request.requested_target_state is None
        and request.metadata
        and "approval_decision" in request.metadata
    ):
        return _advance_approval_decision(request)

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

    # Phase 3: compliance wiring. When asked to advance from SPECIFICATION_RESOLVED
    # with no explicit target, measure the submitted asset against the spec.
    if current == S.SPECIFICATION_RESOLVED and request.requested_target_state is None:
        return _advance_compliance(request)

    # Phase 4: adaptation wiring. When asked to advance from COMPLIANCE_COMPLETE
    # with no explicit target, decide whether adaptation is required.
    if current == S.COMPLIANCE_COMPLETE and request.requested_target_state is None:
        return _advance_adaptation(request)

    # Phase 5: prompt construction wiring. When asked to advance from
    # ADAPTATION_PLANNED with no explicit target, build a GenerationRequest if
    # adaptation requires generation.
    if current == S.ADAPTATION_PLANNED and request.requested_target_state is None:
        return _advance_prompt(request)

    # Phase 6: AI generation wiring. When asked to advance from GENERATION_PENDING
    # with no explicit target, call the generation actuator.
    if current == S.GENERATION_PENDING and request.requested_target_state is None:
        return _advance_generation(request)

    # Phase 7: deterministic transform wiring. When asked to advance from
    # DETERMINISTIC_TRANSFORM_PENDING with no explicit target, execute supported
    # adaptation steps without AI.
    if (
        current == S.DETERMINISTIC_TRANSFORM_PENDING
        and request.requested_target_state is None
    ):
        return _advance_deterministic_transform(request)

    # Phase 8: validation wiring. When asked to advance from GENERATION_COMPLETE
    # or DETERMINISTIC_TRANSFORM_COMPLETE with no explicit target, validate the
    # output asset against the specification.
    if (
        current in (S.GENERATION_COMPLETE, S.DETERMINISTIC_TRANSFORM_COMPLETE)
        and request.requested_target_state is None
    ):
        return _advance_validation(request)

    # Phase 9: approval package routing. When asked to advance from
    # VALIDATION_COMPLETE with no explicit target, assemble an ApprovalPackage
    # for owner review.
    if current == S.VALIDATION_COMPLETE and request.requested_target_state is None:
        return _advance_approval(request)

    # Phase 10: production packaging. When asked to advance from APPROVED with
    # no explicit target, assemble a ProductionPackage from the approved output.
    if current == S.APPROVED and request.requested_target_state is None:
        return _advance_production_packaging(request)

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
