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
