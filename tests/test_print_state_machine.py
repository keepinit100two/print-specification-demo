import pytest

from app.domain.print_schemas import PrintWorkflowState as S
from app.domain.print_state_machine import (
    LEGAL_TRANSITIONS,
    can_transition,
    get_allowed_transitions,
    is_terminal_state,
    is_failure_state,
    is_human_review_state,
    is_retryable_state,
)


HAPPY_PATH = [
    (S.SUBMITTED, S.INGESTED),
    (S.INGESTED, S.NORMALIZATION_PENDING),
    (S.NORMALIZATION_PENDING, S.NORMALIZED),
    (S.NORMALIZED, S.SPECIFICATION_PENDING),
    (S.SPECIFICATION_PENDING, S.SPECIFICATION_RESOLVED),
    (S.SPECIFICATION_RESOLVED, S.COMPLIANCE_PENDING),
    (S.COMPLIANCE_PENDING, S.COMPLIANCE_COMPLETE),
    (S.COMPLIANCE_COMPLETE, S.ADAPTATION_PLANNED),
    (S.ADAPTATION_PLANNED, S.GENERATION_PENDING),
    (S.GENERATION_PENDING, S.GENERATION_RUNNING),
    (S.GENERATION_RUNNING, S.GENERATION_COMPLETE),
    (S.GENERATION_COMPLETE, S.VALIDATION_PENDING),
    (S.VALIDATION_PENDING, S.VALIDATION_COMPLETE),
    (S.VALIDATION_COMPLETE, S.OWNER_REVIEW_PENDING),
    (S.OWNER_REVIEW_PENDING, S.APPROVED),
    (S.APPROVED, S.PRODUCTION_PACKAGING_PENDING),
    (S.PRODUCTION_PACKAGING_PENDING, S.PRODUCTION_PACKAGE_CREATED),
    (S.PRODUCTION_PACKAGE_CREATED, S.COMPLETED),
]

ILLEGAL_SKIPS = [
    (S.SUBMITTED, S.APPROVED),
    (S.NORMALIZED, S.OWNER_REVIEW_PENDING),
    (S.COMPLIANCE_PENDING, S.GENERATION_PENDING),
    (S.GENERATION_RUNNING, S.VALIDATION_COMPLETE),
    (S.VALIDATION_COMPLETE, S.COMPLETED),
]

TERMINAL = [S.COMPLETED, S.FAILED, S.CANCELLED]

HUMAN_REVIEW = [
    S.NORMALIZATION_NEEDS_REVIEW,
    S.OWNER_REVIEW_PENDING,
    S.REVISION_REQUESTED,
    S.REJECTED,
]


@pytest.mark.parametrize("from_state,to_state", HAPPY_PATH)
def test_happy_path_transitions_allowed(from_state, to_state):
    assert can_transition(from_state, to_state) is True


@pytest.mark.parametrize("from_state,to_state", ILLEGAL_SKIPS)
def test_illegal_skipped_transitions_rejected(from_state, to_state):
    assert can_transition(from_state, to_state) is False


@pytest.mark.parametrize("state", TERMINAL)
def test_terminal_states_have_no_outgoing_transitions(state):
    assert is_terminal_state(state) is True
    assert get_allowed_transitions(state) == set()
    assert LEGAL_TRANSITIONS[state] == set()


def test_terminal_states_cannot_transition_anywhere():
    for terminal in TERMINAL:
        for target in S:
            assert can_transition(terminal, target) is False


def test_generation_failed_is_failure_and_retryable():
    assert is_failure_state(S.GENERATION_FAILED) is True
    assert is_retryable_state(S.GENERATION_FAILED) is True


def test_validation_failed_is_failure_and_retryable():
    assert is_failure_state(S.VALIDATION_FAILED) is True
    assert is_retryable_state(S.VALIDATION_FAILED) is True


def test_terminal_failed_is_failure_but_not_retryable():
    assert is_failure_state(S.FAILED) is True
    assert is_retryable_state(S.FAILED) is False


@pytest.mark.parametrize("state", HUMAN_REVIEW)
def test_human_review_states(state):
    assert is_human_review_state(state) is True


def test_non_human_review_state_is_not_flagged():
    assert is_human_review_state(S.GENERATION_RUNNING) is False


def test_get_allowed_transitions_returns_a_copy():
    state = S.SUBMITTED
    original = set(LEGAL_TRANSITIONS[state])

    allowed = get_allowed_transitions(state)
    allowed.add(S.COMPLETED)

    assert LEGAL_TRANSITIONS[state] == original
    assert S.COMPLETED not in LEGAL_TRANSITIONS[state]


def test_every_state_has_a_transition_entry():
    for state in S:
        assert state in LEGAL_TRANSITIONS
