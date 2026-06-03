import pytest

from app.domain.print_orchestration_schemas import (
    WorkflowAdvanceRequest,
    WorkflowAdvanceResult,
    WorkflowOperation,
)
from app.domain.print_schemas import (
    PrintWorkflowRunResult,
    PrintWorkflowState,
    ResultStatus,
)
from app.domain.print_state_machine import get_allowed_transitions
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
