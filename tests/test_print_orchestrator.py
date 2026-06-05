import math
import uuid

import pytest

from app.domain.print_orchestration_schemas import (
    SubsystemExecutionStatus,
    WorkflowAdvanceRequest,
    WorkflowAdvanceResult,
    WorkflowOperation,
)
from app.domain.print_schemas import (
    AdaptationPlan,
    AssetRole,
    DesignJob,
    GeneratedCandidate,
    ImageProperties,
    InvocationStatus,
    ModelInvocationRecord,
    NormalizationResult,
    PrintWorkflowRunResult,
    PrintWorkflowStage,
    PrintWorkflowState,
    ProductType,
    RawSubmission,
    ResultStatus,
    SubmittedAsset,
    TransformationStep,
    TransformationType,
    ValidationResult,
)
from app.domain.print_state_machine import get_allowed_transitions
from app.services import print_orchestrator
from app.services.print_normalization import normalize_submission
from app.services.print_specification import resolve_specification
from app.services.print_compliance import evaluate_compliance
from app.services.print_prompt_construction import build_generation_request
from app.services.print_orchestrator import advance_workflow

S = PrintWorkflowState

# The state machine has no dedicated ADAPTATION_FAILED state. The only legal
# failure transition out of COMPLIANCE_COMPLETE is to the terminal FAILED state,
# so adaptation failures route there.
ADAPTATION_FAILURE_STATE = S.FAILED


def _request(
    current_state,
    target=None,
    operation=WorkflowOperation.ADVANCE,
    **kwargs,
):
    return WorkflowAdvanceRequest(
        run_id="run-1",
        idempotency_key="key-1",
        operation=operation,
        current_state=current_state,
        requested_target_state=target,
        **kwargs,
    )


def _transition_pairs(result):
    return [(c.from_state, c.to_state) for c in result.transition_checks]


TERMINAL_STATES = [S.COMPLETED, S.FAILED, S.CANCELLED]
HUMAN_REVIEW_STATES = [
    S.NORMALIZATION_NEEDS_REVIEW,
    S.OWNER_REVIEW_PENDING,
    S.REVISION_REQUESTED,
    S.REJECTED,
]


@pytest.mark.parametrize("state", TERMINAL_STATES)
def test_terminal_states_stop(state):
    result = advance_workflow(_request(state, target=S.COMPLETED))

    assert result.stopped is True
    assert result.previous_state == state
    assert result.current_state == state
    assert result.stop_reason is not None
    assert result.next_steps is not None
    # Terminal stop does not even evaluate a transition.
    assert result.transition_check is None


@pytest.mark.parametrize("state", HUMAN_REVIEW_STATES)
def test_human_review_states_stop(state):
    result = advance_workflow(_request(state, target=S.APPROVED))

    assert result.stopped is True
    assert result.previous_state == state
    assert result.current_state == state
    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.stop_reason is not None
    assert result.next_steps is not None


def test_illegal_transition_rejected():
    result = advance_workflow(_request(S.SUBMITTED, target=S.APPROVED))

    assert result.transition_check is not None
    assert result.transition_check.allowed is False
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert result.subsystem_records == []
    # No advance happened.
    assert result.previous_state == S.SUBMITTED
    assert result.current_state == S.SUBMITTED
    assert isinstance(result.run_result, PrintWorkflowRunResult)


def test_legal_transition_accepted():
    result = advance_workflow(_request(S.SUBMITTED, target=S.INGESTED))

    assert result.transition_check is not None
    assert result.transition_check.allowed is True
    assert result.previous_state == S.SUBMITTED
    assert result.current_state == S.INGESTED
    assert result.run_result.state == S.INGESTED
    assert result.stopped is False


def test_legal_transition_into_human_review_stops():
    result = advance_workflow(
        _request(S.VALIDATION_COMPLETE, target=S.OWNER_REVIEW_PENDING)
    )

    assert result.transition_check.allowed is True
    assert result.current_state == S.OWNER_REVIEW_PENDING
    # Advancing *into* a human-review state should stop.
    assert result.stopped is True


def test_no_target_returns_noop_stopped():
    result = advance_workflow(_request(S.SUBMITTED, target=None))

    assert result.transition_check is None
    assert result.stopped is True
    assert result.status == ResultStatus.PENDING
    assert result.previous_state == S.SUBMITTED
    assert result.current_state == S.SUBMITTED
    assert result.stop_reason is not None
    assert result.next_steps is not None


@pytest.mark.parametrize(
    "current,target",
    [
        (S.SUBMITTED, S.INGESTED),
        (S.SUBMITTED, S.APPROVED),
        (S.COMPLETED, S.COMPLETED),
        (S.SUBMITTED, None),
        (S.OWNER_REVIEW_PENDING, S.APPROVED),
    ],
)
def test_partial_run_result_always_returned(current, target):
    result = advance_workflow(_request(current, target=target))

    assert isinstance(result, WorkflowAdvanceResult)
    assert isinstance(result.run_result, PrintWorkflowRunResult)
    assert result.run_result.run_id == "run-1"


@pytest.mark.parametrize(
    "current,target",
    [
        (S.SUBMITTED, S.INGESTED),
        (S.SUBMITTED, S.APPROVED),
        (S.COMPLETED, S.COMPLETED),
        (S.SUBMITTED, None),
        (S.OWNER_REVIEW_PENDING, S.APPROVED),
    ],
)
def test_no_subsystem_records_in_phase0(current, target):
    result = advance_workflow(_request(current, target=target))
    assert result.subsystem_records == []


def test_allowed_transitions_included_on_transition_check():
    result = advance_workflow(_request(S.SUBMITTED, target=S.INGESTED))

    expected = get_allowed_transitions(S.SUBMITTED)
    assert set(result.transition_check.allowed_transitions) == expected


def test_allowed_transitions_included_on_illegal_check():
    result = advance_workflow(_request(S.SUBMITTED, target=S.APPROVED))

    expected = get_allowed_transitions(S.SUBMITTED)
    assert set(result.transition_check.allowed_transitions) == expected


# ---------------------------------------------------------------------------
# Normalization wiring (Phase 1) — expected to fail until the spine calls the
# normalization service from NORMALIZATION_PENDING.
# ---------------------------------------------------------------------------


def _asset(role=AssetRole.PRIMARY):
    return SubmittedAsset(
        asset_id=str(uuid.uuid4()),
        role=role,
        uri="file:///tmp/asset.png",
    )


def _raw_submission(
    requested_product="banner",
    brief="A bold outdoor banner advertising a summer sale.",
    assets=None,
):
    if assets is None:
        assets = [_asset(AssetRole.PRIMARY)]
    return RawSubmission(
        submission_id=str(uuid.uuid4()),
        requester="designer@example.com",
        requested_product=requested_product,
        brief=brief,
        assets=assets,
    )


def test_normalization_wiring_valid_submission_advances():
    submission = _raw_submission(requested_product="banner")
    request = _request(
        S.NORMALIZATION_PENDING,
        target=None,
        raw_submission=submission,
    )

    result = advance_workflow(request)

    assert result.current_state == S.NORMALIZED
    assert result.run_result.normalization is not None
    assert result.run_result.normalization.status == ResultStatus.PASSED
    assert result.run_result.normalization.design_job is not None
    assert (
        result.run_result.normalization.design_job.product_type == ProductType.BANNER
    )
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "NormalizationService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED


def test_normalization_wiring_needs_review_stops():
    submission = _raw_submission(brief=None)
    request = _request(
        S.NORMALIZATION_PENDING,
        target=None,
        raw_submission=submission,
    )

    result = advance_workflow(request)

    assert result.current_state == S.NORMALIZATION_NEEDS_REVIEW
    assert result.stopped is True
    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.run_result.normalization is not None
    assert result.run_result.normalization.status == ResultStatus.NEEDS_REVIEW
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED


def test_normalization_wiring_failure_path(monkeypatch):
    def _boom(_raw_submission):
        raise RuntimeError("normalization exploded")

    monkeypatch.setattr(
        print_orchestrator, "normalize_submission", _boom, raising=False
    )

    submission = _raw_submission(requested_product="banner")
    request = _request(
        S.NORMALIZATION_PENDING,
        target=None,
        raw_submission=submission,
    )

    result = advance_workflow(request)

    assert result.current_state == S.NORMALIZATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.specification is None


# ---------------------------------------------------------------------------
# Specification resolution wiring (Phase 2) — expected to fail until the spine
# calls the specification service from NORMALIZED.
# ---------------------------------------------------------------------------


def _normalized_run_result(submission=None) -> PrintWorkflowRunResult:
    """Build a NORMALIZED run bundle carrying a successful NormalizationResult."""
    if submission is None:
        submission = _raw_submission(requested_product="banner")
    normalization = normalize_submission(submission)
    return PrintWorkflowRunResult(
        run_id="run-1",
        submission_id=submission.submission_id,
        stage=PrintWorkflowStage.NORMALIZATION,
        state=S.NORMALIZED,
        status=ResultStatus.PENDING,
        raw_submission=submission,
        normalization=normalization,
    )


def test_specification_wiring_success_advances():
    run_result = _normalized_run_result()
    design_job = run_result.normalization.design_job
    request = _request(
        S.NORMALIZED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.SPECIFICATION_RESOLVED
    assert result.run_result.specification is not None
    assert result.run_result.specification.job_id == design_job.job_id
    assert result.run_result.specification.product_type == design_job.product_type
    assert len(result.subsystem_records) == 1
    assert (
        result.subsystem_records[0].subsystem_name == "SpecificationResolutionService"
    )
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.NORMALIZED, S.SPECIFICATION_PENDING) in pairs
    assert (S.SPECIFICATION_PENDING, S.SPECIFICATION_RESOLVED) in pairs


def test_specification_wiring_missing_design_job_fails():
    run_result = PrintWorkflowRunResult(
        run_id="run-1",
        submission_id="sub-1",
        stage=PrintWorkflowStage.NORMALIZATION,
        state=S.NORMALIZED,
        status=ResultStatus.PENDING,
        normalization=None,
    )
    request = _request(
        S.NORMALIZED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.SPECIFICATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.specification is None

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.NORMALIZED, S.SPECIFICATION_PENDING) in pairs
    assert (S.SPECIFICATION_PENDING, S.SPECIFICATION_FAILED) in pairs


def test_specification_wiring_exception_path(monkeypatch):
    def _boom(_design_job):
        raise RuntimeError("specification exploded")

    monkeypatch.setattr(
        print_orchestrator, "resolve_specification", _boom, raising=False
    )

    run_result = _normalized_run_result()
    request = _request(
        S.NORMALIZED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.SPECIFICATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.specification is None

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.NORMALIZED, S.SPECIFICATION_PENDING) in pairs
    assert (S.SPECIFICATION_PENDING, S.SPECIFICATION_FAILED) in pairs


# ---------------------------------------------------------------------------
# Technical compliance wiring (Phase 3) — expected to fail until the spine
# calls the compliance service from SPECIFICATION_RESOLVED.
# ---------------------------------------------------------------------------


def _required_px(spec):
    w = math.ceil(spec.dimensions.width_mm / 25.4 * spec.dimensions.min_dpi) + 100
    h = math.ceil(spec.dimensions.height_mm / 25.4 * spec.dimensions.min_dpi) + 100
    return w, h


def _compliant_image(spec) -> ImageProperties:
    width_px, height_px = _required_px(spec)
    return ImageProperties(
        width_px=width_px,
        height_px=height_px,
        dpi=spec.dimensions.min_dpi,
        color_profile=spec.color.color_profile or spec.color.color_mode,
        file_format=spec.accepted_formats[0],
    )


def _spec_resolved_run_result(
    image_properties=None,
    product_type=ProductType.BANNER,
    include_specification=True,
):
    """Build a SPECIFICATION_RESOLVED run bundle with a primary asset present."""
    submission_id = str(uuid.uuid4())
    design_job = DesignJob(
        job_id=f"job-{submission_id}",
        submission_id=submission_id,
        product_type=product_type,
        title="Summer sale",
        normalized_brief="A bold outdoor banner advertising a summer sale.",
        requested_quantity=1,
    )
    spec = resolve_specification(design_job)

    if image_properties is None:
        image_properties = _compliant_image(spec)

    asset = SubmittedAsset(
        asset_id=str(uuid.uuid4()),
        role=AssetRole.PRIMARY,
        uri="file:///tmp/asset.png",
        properties=image_properties,
    )
    submission = RawSubmission(
        submission_id=submission_id,
        requester="designer@example.com",
        requested_product="banner",
        brief="A bold outdoor banner advertising a summer sale.",
        assets=[asset],
    )
    normalization = NormalizationResult(
        status=ResultStatus.PASSED,
        design_job=design_job,
    )
    run_result = PrintWorkflowRunResult(
        run_id="run-1",
        submission_id=submission_id,
        stage=PrintWorkflowStage.SPECIFICATION,
        state=S.SPECIFICATION_RESOLVED,
        status=ResultStatus.PENDING,
        raw_submission=submission,
        normalization=normalization,
        specification=spec if include_specification else None,
    )
    return run_result, design_job, spec


def test_compliance_wiring_compliant_advances():
    run_result, design_job, spec = _spec_resolved_run_result()
    request = _request(
        S.SPECIFICATION_RESOLVED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.COMPLIANCE_COMPLETE
    assert result.run_result.compliance is not None
    assert result.run_result.compliance.status == ResultStatus.PASSED
    assert result.run_result.compliance.is_print_ready is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "TechnicalComplianceService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.SPECIFICATION_RESOLVED, S.COMPLIANCE_PENDING) in pairs
    assert (S.COMPLIANCE_PENDING, S.COMPLIANCE_COMPLETE) in pairs


def test_compliance_wiring_non_compliant_completes_not_print_ready():
    run_result, design_job, spec = _spec_resolved_run_result()
    # Knock DPI below the spec minimum on the submitted asset.
    run_result.raw_submission.assets[0].properties.dpi = spec.dimensions.min_dpi - 150
    request = _request(
        S.SPECIFICATION_RESOLVED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.COMPLIANCE_COMPLETE
    assert result.run_result.compliance.status == ResultStatus.FAILED
    assert result.run_result.compliance.is_print_ready is False
    assert any(
        f.requirement == "min_dpi" for f in result.run_result.compliance.findings
    )
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.SPECIFICATION_RESOLVED, S.COMPLIANCE_PENDING) in pairs
    assert (S.COMPLIANCE_PENDING, S.COMPLIANCE_COMPLETE) in pairs


def test_compliance_wiring_missing_metadata_routes_to_failed():
    image = ImageProperties(
        width_px=None,
        height_px=None,
        dpi=None,
        file_format="pdf",
    )
    run_result, design_job, spec = _spec_resolved_run_result(image_properties=image)
    request = _request(
        S.SPECIFICATION_RESOLVED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.COMPLIANCE_FAILED
    assert result.status in (ResultStatus.FAILED, ResultStatus.NEEDS_REVIEW)
    assert result.stopped is True
    assert result.run_result.compliance is not None
    assert result.run_result.compliance.status == ResultStatus.NEEDS_REVIEW
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED


def test_compliance_wiring_missing_spec_fails_safely():
    run_result, design_job, spec = _spec_resolved_run_result(include_specification=False)
    request = _request(
        S.SPECIFICATION_RESOLVED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.COMPLIANCE_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.SPECIFICATION_RESOLVED, S.COMPLIANCE_PENDING) in pairs
    assert (S.COMPLIANCE_PENDING, S.COMPLIANCE_FAILED) in pairs


def test_compliance_wiring_exception_path(monkeypatch):
    def _boom(design_job, specification, image_properties):
        raise RuntimeError("compliance exploded")

    monkeypatch.setattr(
        print_orchestrator, "evaluate_compliance", _boom, raising=False
    )

    run_result, design_job, spec = _spec_resolved_run_result()
    request = _request(
        S.SPECIFICATION_RESOLVED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.COMPLIANCE_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.SPECIFICATION_RESOLVED, S.COMPLIANCE_PENDING) in pairs
    assert (S.COMPLIANCE_PENDING, S.COMPLIANCE_FAILED) in pairs


# ---------------------------------------------------------------------------
# Adaptation planning wiring (Phase 4) — expected to fail until the spine calls
# the adaptation service from COMPLIANCE_COMPLETE.
#
# Note: the state machine has a single ADAPTATION_PLANNED state (no
# ADAPTATION_PENDING), so adaptation records one transition:
#   COMPLIANCE_COMPLETE -> ADAPTATION_PLANNED.
# There is no ADAPTATION_FAILED state; adaptation failures route to the only
# legal failure target from COMPLIANCE_COMPLETE, the terminal FAILED state.
# ---------------------------------------------------------------------------


def _compliance_complete_run_result(print_ready=True):
    """Build a COMPLIANCE_COMPLETE run bundle carrying design_job, spec, compliance."""
    run_result, design_job, spec = _spec_resolved_run_result()
    asset = run_result.raw_submission.assets[0]
    if not print_ready:
        # Knock DPI below spec so the asset is measured as not print-ready.
        asset.properties.dpi = spec.dimensions.min_dpi - 150

    compliance = evaluate_compliance(design_job, spec, asset.properties)
    run_result.compliance = compliance
    run_result.stage = PrintWorkflowStage.COMPLIANCE
    run_result.state = S.COMPLIANCE_COMPLETE
    run_result.status = ResultStatus.PENDING
    return run_result, design_job, spec, compliance


def test_adaptation_wiring_not_print_ready_creates_plan():
    run_result, design_job, spec, compliance = _compliance_complete_run_result(
        print_ready=False
    )
    assert compliance.status == ResultStatus.FAILED
    assert compliance.is_print_ready is False

    request = _request(
        S.COMPLIANCE_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.ADAPTATION_PLANNED
    assert result.run_result.adaptation is not None
    assert result.run_result.adaptation.steps
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "AdaptationPlanningService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.COMPLIANCE_COMPLETE, S.ADAPTATION_PLANNED) in pairs


def test_adaptation_wiring_print_ready_skips_adaptation():
    run_result, design_job, spec, compliance = _compliance_complete_run_result(
        print_ready=True
    )
    assert compliance.status == ResultStatus.PASSED
    assert compliance.is_print_ready is True

    request = _request(
        S.COMPLIANCE_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    # The adaptation service must not be called and no plan should be created.
    assert result.run_result.adaptation is None
    assert result.subsystem_records == []
    assert all(
        r.subsystem_name != "AdaptationPlanningService"
        for r in result.subsystem_records
    )
    assert result.current_state != S.ADAPTATION_PLANNED


def test_adaptation_wiring_missing_compliance_fails_safely():
    run_result, design_job, spec = _spec_resolved_run_result()
    run_result.stage = PrintWorkflowStage.COMPLIANCE
    run_result.state = S.COMPLIANCE_COMPLETE
    run_result.status = ResultStatus.PENDING
    # compliance is intentionally left as None.

    request = _request(
        S.COMPLIANCE_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == ADAPTATION_FAILURE_STATE
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.adaptation is None

    assert all(c.allowed for c in result.transition_checks)


def test_adaptation_wiring_exception_path(monkeypatch):
    def _boom(design_job, specification, compliance_result):
        raise RuntimeError("adaptation exploded")

    monkeypatch.setattr(
        print_orchestrator, "create_adaptation_plan", _boom, raising=False
    )

    run_result, design_job, spec, compliance = _compliance_complete_run_result(
        print_ready=False
    )
    request = _request(
        S.COMPLIANCE_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == ADAPTATION_FAILURE_STATE
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.adaptation is None

    assert all(c.allowed for c in result.transition_checks)


# ---------------------------------------------------------------------------
# Prompt construction wiring (Phase 5) — expected to fail until the spine calls
# the prompt construction service from ADAPTATION_PLANNED.
#
# The spine only calls build_generation_request when adaptation.requires_generation
# is True; otherwise it stops (no generation request needed). The legal success
# transition is ADAPTATION_PLANNED -> GENERATION_PENDING, and the only legal
# failure target used here is the terminal FAILED state.
# ---------------------------------------------------------------------------


def _adaptation_planned_run_result(requires_generation=True):
    """Build an ADAPTATION_PLANNED run bundle carrying design_job, spec, adaptation."""
    run_result, design_job, spec = _spec_resolved_run_result()

    if requires_generation:
        adaptation = AdaptationPlan(
            plan_id=f"plan-{design_job.job_id}",
            spec_id=spec.spec_id,
            job_id=design_job.job_id,
            status=ResultStatus.PASSED,
            requires_generation=True,
            steps=[
                TransformationStep(
                    transformation=TransformationType.UPSCALE,
                    target_asset_role=AssetRole.PRIMARY,
                    parameters={"requirement": "min_dpi"},
                    reason="Resolve min_dpi: upscale to meet required DPI",
                )
            ],
        )
    else:
        adaptation = AdaptationPlan(
            plan_id=f"plan-{design_job.job_id}",
            spec_id=spec.spec_id,
            job_id=design_job.job_id,
            status=ResultStatus.SKIPPED,
            requires_generation=False,
            steps=[],
        )

    run_result.adaptation = adaptation
    run_result.stage = PrintWorkflowStage.ADAPTATION
    run_result.state = S.ADAPTATION_PLANNED
    run_result.status = ResultStatus.PENDING
    return run_result, design_job, spec, adaptation


def test_prompt_wiring_requires_generation_creates_request():
    run_result, design_job, spec, adaptation = _adaptation_planned_run_result(
        requires_generation=True
    )
    request = _request(
        S.ADAPTATION_PLANNED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.GENERATION_PENDING
    assert result.run_result.generation_request is not None
    assert result.run_result.generation_request.job_id == design_job.job_id
    assert result.run_result.generation_request.spec_id == spec.spec_id
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "PromptConstructionService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.ADAPTATION_PLANNED, S.GENERATION_PENDING) in pairs


def test_prompt_wiring_skips_when_no_generation_required():
    run_result, design_job, spec, adaptation = _adaptation_planned_run_result(
        requires_generation=False
    )
    request = _request(
        S.ADAPTATION_PLANNED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    # The prompt construction service must not be called and no request created.
    assert result.run_result.generation_request is None
    assert result.subsystem_records == []
    assert result.stopped is True
    assert result.next_steps is not None
    assert result.current_state != S.GENERATION_PENDING


def test_prompt_wiring_missing_adaptation_fails_safely():
    run_result, design_job, spec = _spec_resolved_run_result()
    run_result.stage = PrintWorkflowStage.ADAPTATION
    run_result.state = S.ADAPTATION_PLANNED
    run_result.status = ResultStatus.PENDING
    # adaptation is intentionally left as None.

    request = _request(
        S.ADAPTATION_PLANNED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.generation_request is None

    assert all(c.allowed for c in result.transition_checks)


def test_prompt_wiring_exception_path(monkeypatch):
    def _boom(design_job, specification, adaptation_plan):
        raise RuntimeError("prompt construction exploded")

    monkeypatch.setattr(
        print_orchestrator, "build_generation_request", _boom, raising=False
    )

    run_result, design_job, spec, adaptation = _adaptation_planned_run_result(
        requires_generation=True
    )
    request = _request(
        S.ADAPTATION_PLANNED,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.generation_request is None

    assert all(c.allowed for c in result.transition_checks)


# ---------------------------------------------------------------------------
# AI generation wiring (Phase 6) — expected to fail until the spine calls
# generate_candidates from GENERATION_PENDING.
#
# Macro step records two transitions:
#   GENERATION_PENDING -> GENERATION_RUNNING -> GENERATION_COMPLETE / FAILED
# ---------------------------------------------------------------------------


def _generation_pending_run_result(include_generation_request=True):
    """Build a GENERATION_PENDING run bundle with an optional GenerationRequest."""
    run_result, design_job, spec, adaptation = _adaptation_planned_run_result(
        requires_generation=True
    )
    generation_request = (
        build_generation_request(design_job, spec, adaptation)
        if include_generation_request
        else None
    )
    run_result.generation_request = generation_request
    run_result.stage = PrintWorkflowStage.GENERATION
    run_result.state = S.GENERATION_PENDING
    run_result.status = ResultStatus.PENDING
    return run_result, generation_request


def _stub_generation_outputs(generation_request):
    """Deterministic fake candidates + invocation (no OpenAI)."""
    candidate = GeneratedCandidate(
        candidate_id=f"candidate-{generation_request.request_id}-001",
        request_id=generation_request.request_id,
        uri=f"artifact://generated/candidate-{generation_request.request_id}-001.png",
    )
    invocation = ModelInvocationRecord(
        invocation_id=f"invocation-{generation_request.request_id}",
        request_id=generation_request.request_id,
        provider="local-fake",
        model_name="deterministic-mvp-generator",
        status=InvocationStatus.SUCCEEDED,
        generated_candidate_ids=[candidate.candidate_id],
    )
    return [candidate], [invocation]


def test_generation_wiring_success_advances(monkeypatch):
    run_result, generation_request = _generation_pending_run_result()

    def _fake_generate(req):
        return _stub_generation_outputs(req)

    monkeypatch.setattr(
        print_orchestrator, "generate_candidates", _fake_generate, raising=False
    )

    request = _request(
        S.GENERATION_PENDING,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.GENERATION_COMPLETE
    assert result.run_result.candidates
    assert result.run_result.model_invocations
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "AIGenerationService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.GENERATION_PENDING, S.GENERATION_RUNNING) in pairs
    assert (S.GENERATION_RUNNING, S.GENERATION_COMPLETE) in pairs


def test_generation_wiring_missing_request_fails_safely():
    run_result, _ = _generation_pending_run_result(include_generation_request=False)

    request = _request(
        S.GENERATION_PENDING,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.GENERATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.candidates == []
    assert result.run_result.model_invocations == []

    assert all(c.allowed for c in result.transition_checks)


def test_generation_wiring_no_candidates_produced(monkeypatch):
    run_result, generation_request = _generation_pending_run_result()

    failed_invocation = ModelInvocationRecord(
        invocation_id=f"invocation-{generation_request.request_id}",
        request_id=generation_request.request_id,
        provider="local-fake",
        model_name="deterministic-mvp-generator",
        status=InvocationStatus.FAILED,
        generated_candidate_ids=[],
        error_code="NO_CANDIDATES",
        error_message="No candidates produced",
    )

    monkeypatch.setattr(
        print_orchestrator,
        "generate_candidates",
        lambda _req: ([], [failed_invocation]),
        raising=False,
    )

    request = _request(
        S.GENERATION_PENDING,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.GENERATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert result.run_result.candidates == []
    assert result.run_result.model_invocations
    assert result.subsystem_records[0].error_message or result.next_steps

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.GENERATION_PENDING, S.GENERATION_RUNNING) in pairs
    assert (S.GENERATION_RUNNING, S.GENERATION_FAILED) in pairs


def test_generation_wiring_exception_path(monkeypatch):
    def _boom(_generation_request):
        raise RuntimeError("generation exploded")

    monkeypatch.setattr(
        print_orchestrator, "generate_candidates", _boom, raising=False
    )

    run_result, _ = _generation_pending_run_result()
    request = _request(
        S.GENERATION_PENDING,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.GENERATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.candidates == []

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.GENERATION_PENDING, S.GENERATION_RUNNING) in pairs
    assert (S.GENERATION_RUNNING, S.GENERATION_FAILED) in pairs


# ---------------------------------------------------------------------------
# Deterministic transform wiring (Phase 7) — expected to fail until the spine
# calls execute_deterministic_transforms from DETERMINISTIC_TRANSFORM_PENDING.
#
# The legal success transition is a single step:
#   DETERMINISTIC_TRANSFORM_PENDING -> DETERMINISTIC_TRANSFORM_COMPLETE
# Unsupported transform outcomes route to:
#   DETERMINISTIC_TRANSFORM_PENDING -> DETERMINISTIC_TRANSFORM_FAILED
#
# TODO: PrintWorkflowRunResult does not yet expose `deterministic_transform`.
# Once that field exists, assert result.run_result.deterministic_transform is
# populated on success and carries the DeterministicTransformResult status.
# ---------------------------------------------------------------------------


def _deterministic_transform_pending_run_result(
    *,
    transformation=TransformationType.RESIZE,
    include_design_job=True,
    include_specification=True,
    include_adaptation=True,
):
    """Build a DETERMINISTIC_TRANSFORM_PENDING run bundle with adaptation inputs."""
    run_result, design_job, spec = _spec_resolved_run_result(
        include_specification=include_specification,
    )

    if not include_design_job:
        run_result.normalization = None

    adaptation = None
    if include_adaptation:
        adaptation = AdaptationPlan(
            plan_id=f"plan-{design_job.job_id}",
            spec_id=spec.spec_id,
            job_id=design_job.job_id,
            status=ResultStatus.PASSED,
            requires_generation=False,
            steps=[
                TransformationStep(
                    transformation=transformation,
                    target_asset_role=AssetRole.PRIMARY,
                    parameters={"requirement": "width_px"},
                    reason="Resolve width_px via deterministic resize",
                )
            ],
        )

    run_result.adaptation = adaptation
    run_result.stage = PrintWorkflowStage.ADAPTATION
    run_result.state = S.DETERMINISTIC_TRANSFORM_PENDING
    run_result.status = ResultStatus.PENDING
    return run_result, design_job, spec, adaptation


def test_deterministic_transform_wiring_success_advances():
    run_result, design_job, spec, adaptation = _deterministic_transform_pending_run_result(
        transformation=TransformationType.RESIZE,
    )
    request = _request(
        S.DETERMINISTIC_TRANSFORM_PENDING,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.DETERMINISTIC_TRANSFORM_COMPLETE
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "DeterministicTransformService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    # TODO: assert result.run_result.deterministic_transform once the run bundle field exists.
    if hasattr(result.run_result, "deterministic_transform"):
        assert result.run_result.deterministic_transform is not None
        assert result.run_result.deterministic_transform.status == ResultStatus.PASSED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.DETERMINISTIC_TRANSFORM_PENDING, S.DETERMINISTIC_TRANSFORM_COMPLETE) in pairs


def test_deterministic_transform_wiring_needs_review_stops():
    run_result, design_job, spec, adaptation = _deterministic_transform_pending_run_result(
        transformation=TransformationType.BACKGROUND_REMOVAL,
    )
    request = _request(
        S.DETERMINISTIC_TRANSFORM_PENDING,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.DETERMINISTIC_TRANSFORM_FAILED
    assert result.status in (ResultStatus.NEEDS_REVIEW, ResultStatus.FAILED)
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "DeterministicTransformService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED
    assert result.reasons or result.next_steps

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.DETERMINISTIC_TRANSFORM_PENDING, S.DETERMINISTIC_TRANSFORM_FAILED) in pairs


@pytest.mark.parametrize(
    "missing_field",
    [
        "existing_run_result",
        "design_job",
        "specification",
        "adaptation",
    ],
)
def test_deterministic_transform_wiring_missing_inputs_fail_safely(missing_field):
    run_result, design_job, spec, adaptation = _deterministic_transform_pending_run_result()

    if missing_field == "existing_run_result":
        request = _request(
            S.DETERMINISTIC_TRANSFORM_PENDING,
            target=None,
        )
    elif missing_field == "design_job":
        run_result.normalization = None
        request = _request(
            S.DETERMINISTIC_TRANSFORM_PENDING,
            target=None,
            existing_run_result=run_result,
        )
    elif missing_field == "specification":
        run_result.specification = None
        request = _request(
            S.DETERMINISTIC_TRANSFORM_PENDING,
            target=None,
            existing_run_result=run_result,
        )
    else:
        run_result.adaptation = None
        request = _request(
            S.DETERMINISTIC_TRANSFORM_PENDING,
            target=None,
            existing_run_result=run_result,
        )

    result = advance_workflow(request)

    assert result.current_state == S.DETERMINISTIC_TRANSFORM_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "DeterministicTransformService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.DETERMINISTIC_TRANSFORM_PENDING, S.DETERMINISTIC_TRANSFORM_FAILED) in pairs


def test_deterministic_transform_wiring_exception_path(monkeypatch):
    def _boom(design_job, specification, adaptation_plan):
        raise RuntimeError("deterministic transform exploded")

    monkeypatch.setattr(
        print_orchestrator,
        "execute_deterministic_transforms",
        _boom,
        raising=False,
    )

    run_result, design_job, spec, adaptation = _deterministic_transform_pending_run_result()
    request = _request(
        S.DETERMINISTIC_TRANSFORM_PENDING,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.DETERMINISTIC_TRANSFORM_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "DeterministicTransformService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.DETERMINISTIC_TRANSFORM_PENDING, S.DETERMINISTIC_TRANSFORM_FAILED) in pairs


# ---------------------------------------------------------------------------
# Validation wiring (Phase 8) — expected to fail until the spine calls
# validate_print_asset from GENERATION_COMPLETE or DETERMINISTIC_TRANSFORM_COMPLETE.
#
# Macro step records two transitions:
#   <complete_state> -> VALIDATION_PENDING -> VALIDATION_COMPLETE / FAILED
# ---------------------------------------------------------------------------


def _valid_generated_candidate(spec, *, candidate_id=None, properties=None):
    """Build a GeneratedCandidate with optional compliant ImageProperties."""
    properties = properties if properties is not None else _compliant_image(spec)
    candidate_id = candidate_id or f"candidate-{uuid.uuid4()}"
    return GeneratedCandidate(
        candidate_id=candidate_id,
        request_id=f"genreq-{candidate_id}",
        uri=f"artifact://generated/{candidate_id}.png",
        properties=properties,
    )


def _generation_complete_run_result(
    *,
    compliant_candidate=True,
    include_specification=True,
    include_candidates=True,
):
    """Build a GENERATION_COMPLETE run bundle with spec and optional candidates."""
    run_result, generation_request = _generation_pending_run_result()
    spec = run_result.specification

    if not include_specification:
        run_result.specification = None

    candidate = None
    if include_candidates:
        if compliant_candidate:
            candidate = _valid_generated_candidate(spec)
        else:
            image = _compliant_image(spec)
            image.dpi = spec.dimensions.min_dpi - 150
            candidate = _valid_generated_candidate(spec, properties=image)
        run_result.candidates = [candidate]
    else:
        run_result.candidates = []

    run_result.state = S.GENERATION_COMPLETE
    run_result.stage = PrintWorkflowStage.GENERATION
    run_result.status = ResultStatus.PENDING
    return run_result, spec, candidate


def _deterministic_transform_complete_run_result():
    """
    Build a DETERMINISTIC_TRANSFORM_COMPLETE run bundle with a validatable output.

    TODO: PrintWorkflowRunResult has no deterministic_transform field yet. Future
    wiring should validate transformed_assets[0] from that result. Until the run
    bundle carries deterministic output, attach a compliant GeneratedCandidate so
    this entry-point test can exercise validation routing from
    DETERMINISTIC_TRANSFORM_COMPLETE.
    """
    run_result, design_job, spec, adaptation = _deterministic_transform_pending_run_result()
    run_result.state = S.DETERMINISTIC_TRANSFORM_COMPLETE
    run_result.stage = PrintWorkflowStage.ADAPTATION
    run_result.status = ResultStatus.PENDING
    run_result.candidates = [_valid_generated_candidate(spec)]
    return run_result, spec


def test_validation_wiring_generation_passes():
    run_result, spec, candidate = _generation_complete_run_result()
    request = _request(
        S.GENERATION_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.VALIDATION_COMPLETE
    assert result.run_result.validation is not None
    assert result.run_result.validation.status == ResultStatus.PASSED
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "PrintValidationService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.GENERATION_COMPLETE, S.VALIDATION_PENDING) in pairs
    assert (S.VALIDATION_PENDING, S.VALIDATION_COMPLETE) in pairs


def test_validation_wiring_deterministic_transform_passes():
    run_result, spec = _deterministic_transform_complete_run_result()
    request = _request(
        S.DETERMINISTIC_TRANSFORM_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.VALIDATION_COMPLETE
    assert result.run_result.validation is not None
    assert result.run_result.validation.status == ResultStatus.PASSED
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "PrintValidationService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.DETERMINISTIC_TRANSFORM_COMPLETE, S.VALIDATION_PENDING) in pairs
    assert (S.VALIDATION_PENDING, S.VALIDATION_COMPLETE) in pairs


def test_validation_wiring_failed_candidate_stops():
    run_result, spec, candidate = _generation_complete_run_result(
        compliant_candidate=False
    )
    request = _request(
        S.GENERATION_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.VALIDATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert result.run_result.validation is not None
    assert result.run_result.validation.status == ResultStatus.FAILED
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "PrintValidationService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.GENERATION_COMPLETE, S.VALIDATION_PENDING) in pairs
    assert (S.VALIDATION_PENDING, S.VALIDATION_FAILED) in pairs


@pytest.mark.parametrize(
    "missing_field",
    [
        "existing_run_result",
        "specification",
        "candidates",
    ],
)
def test_validation_wiring_missing_inputs_fail_safely(missing_field):
    run_result, spec, candidate = _generation_complete_run_result()

    if missing_field == "existing_run_result":
        request = _request(
            S.GENERATION_COMPLETE,
            target=None,
        )
    elif missing_field == "specification":
        run_result.specification = None
        request = _request(
            S.GENERATION_COMPLETE,
            target=None,
            existing_run_result=run_result,
        )
    else:
        run_result.candidates = []
        request = _request(
            S.GENERATION_COMPLETE,
            target=None,
            existing_run_result=run_result,
        )

    result = advance_workflow(request)

    assert result.current_state == S.VALIDATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "PrintValidationService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.GENERATION_COMPLETE, S.VALIDATION_PENDING) in pairs
    assert (S.VALIDATION_PENDING, S.VALIDATION_FAILED) in pairs


def test_validation_wiring_exception_path(monkeypatch):
    def _boom(specification, asset):
        raise RuntimeError("validation exploded")

    monkeypatch.setattr(
        print_orchestrator, "validate_print_asset", _boom, raising=False
    )

    run_result, spec, candidate = _generation_complete_run_result()
    request = _request(
        S.GENERATION_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.VALIDATION_FAILED
    assert result.status == ResultStatus.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "PrintValidationService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.GENERATION_COMPLETE, S.VALIDATION_PENDING) in pairs
    assert (S.VALIDATION_PENDING, S.VALIDATION_FAILED) in pairs


# ---------------------------------------------------------------------------
# Approval package routing (Phase 9) — expected to fail until the spine calls
# create_approval_package from VALIDATION_COMPLETE.
#
# Routes a passed validation into owner review; does not record ApprovalDecision
# or create a production package.
#
# Legal success transition:
#   VALIDATION_COMPLETE -> OWNER_REVIEW_PENDING
# ---------------------------------------------------------------------------


def _validation_complete_run_result(
    *,
    include_validation=True,
    include_specification=True,
    include_design_job=True,
    include_candidates=True,
):
    """Build a VALIDATION_COMPLETE run bundle ready for approval routing."""
    run_result, spec, candidate = _generation_complete_run_result(
        include_specification=include_specification,
        include_candidates=include_candidates,
    )

    if not include_design_job:
        run_result.normalization = None

    if include_validation and candidate is not None:
        run_result.validation = ValidationResult(
            status=ResultStatus.PASSED,
            spec_id=spec.spec_id,
            validated_candidate_ids=[candidate.candidate_id],
            passed_candidate_ids=[candidate.candidate_id],
            next_steps="Proceed to owner review.",
        )
    else:
        run_result.validation = None

    run_result.state = S.VALIDATION_COMPLETE
    run_result.stage = PrintWorkflowStage.VALIDATION
    run_result.status = ResultStatus.PENDING
    return run_result, spec, candidate


def test_approval_wiring_creates_package_and_routes_to_review():
    run_result, spec, candidate = _validation_complete_run_result()
    request = _request(
        S.VALIDATION_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.OWNER_REVIEW_PENDING
    assert result.run_result.approval_package is not None
    assert result.run_result.approval_package.status == ResultStatus.PENDING
    assert candidate.candidate_id in result.run_result.approval_package.candidate_ids
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "ApprovalWorkflowService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.SUCCEEDED

    assert all(c.allowed for c in result.transition_checks)
    pairs = _transition_pairs(result)
    assert (S.VALIDATION_COMPLETE, S.OWNER_REVIEW_PENDING) in pairs


@pytest.mark.parametrize(
    "missing_field",
    [
        "validation",
        "specification",
        "design_job",
    ],
)
def test_approval_wiring_missing_inputs_fail_safely(missing_field):
    run_result, spec, candidate = _validation_complete_run_result()

    if missing_field == "validation":
        run_result.validation = None
    elif missing_field == "specification":
        run_result.specification = None
    else:
        run_result.normalization = None

    request = _request(
        S.VALIDATION_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "ApprovalWorkflowService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.approval_package is None


def test_approval_wiring_exception_path(monkeypatch):
    def _boom(validation_result, outputs, specification, design_job):
        raise RuntimeError("approval package exploded")

    monkeypatch.setattr(
        print_orchestrator,
        "create_approval_package",
        _boom,
        raising=False,
    )

    run_result, spec, candidate = _validation_complete_run_result()
    request = _request(
        S.VALIDATION_COMPLETE,
        target=None,
        existing_run_result=run_result,
    )

    result = advance_workflow(request)

    assert result.current_state == S.FAILED
    assert result.stopped is True
    assert len(result.subsystem_records) == 1
    assert result.subsystem_records[0].subsystem_name == "ApprovalWorkflowService"
    assert result.subsystem_records[0].status == SubsystemExecutionStatus.FAILED
    assert result.subsystem_records[0].error_message
    assert result.run_result.approval_package is None
