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
    AssetRole,
    DesignJob,
    ImageProperties,
    NormalizationResult,
    PrintWorkflowRunResult,
    PrintWorkflowStage,
    PrintWorkflowState,
    ProductType,
    RawSubmission,
    ResultStatus,
    SubmittedAsset,
)
from app.domain.print_state_machine import get_allowed_transitions
from app.services import print_orchestrator
from app.services.print_normalization import normalize_submission
from app.services.print_specification import resolve_specification
from app.services.print_orchestrator import advance_workflow

S = PrintWorkflowState


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
