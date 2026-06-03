"""
Pure, deterministic state-machine definition for the print-specification workflow.

This module defines the *legal* transitions between PrintWorkflowState values and
small, side-effect-free helpers to query them. It contains no workflow logic, calls
no services, imports no web framework, and does not modify any schema.

Everything here is a pure function of its inputs, so it is trivially testable.
"""

from typing import Dict, Set

from app.domain.print_schemas import PrintWorkflowState

S = PrintWorkflowState


# ---------------------------------------------------------------------------
# Legal transition table
# ---------------------------------------------------------------------------

LEGAL_TRANSITIONS: Dict[PrintWorkflowState, Set[PrintWorkflowState]] = {
    S.SUBMITTED: {S.INGESTED, S.FAILED, S.CANCELLED},
    S.INGESTED: {S.NORMALIZATION_PENDING, S.FAILED},

    S.NORMALIZATION_PENDING: {
        S.NORMALIZED,
        S.NORMALIZATION_NEEDS_REVIEW,
        S.NORMALIZATION_FAILED,
    },
    S.NORMALIZATION_NEEDS_REVIEW: {S.NORMALIZATION_PENDING, S.CANCELLED},
    S.NORMALIZATION_FAILED: {S.FAILED},
    S.NORMALIZED: {S.SPECIFICATION_PENDING, S.FAILED},

    S.SPECIFICATION_PENDING: {S.SPECIFICATION_RESOLVED, S.SPECIFICATION_FAILED},
    S.SPECIFICATION_FAILED: {S.FAILED, S.NORMALIZATION_NEEDS_REVIEW},
    S.SPECIFICATION_RESOLVED: {S.COMPLIANCE_PENDING, S.FAILED},

    S.COMPLIANCE_PENDING: {S.COMPLIANCE_COMPLETE, S.COMPLIANCE_FAILED},
    S.COMPLIANCE_FAILED: {S.REVISION_REQUESTED, S.FAILED},
    S.COMPLIANCE_COMPLETE: {
        S.ADAPTATION_PLANNED,
        S.PRODUCTION_PACKAGING_PENDING,
        S.FAILED,
    },

    S.ADAPTATION_PLANNED: {S.GENERATION_PENDING, S.VALIDATION_PENDING, S.FAILED},

    S.GENERATION_PENDING: {S.GENERATION_RUNNING, S.FAILED},
    S.GENERATION_RUNNING: {S.GENERATION_COMPLETE, S.GENERATION_FAILED},
    S.GENERATION_FAILED: {S.GENERATION_PENDING, S.FAILED},
    S.GENERATION_COMPLETE: {S.VALIDATION_PENDING, S.FAILED},

    S.VALIDATION_PENDING: {S.VALIDATION_COMPLETE, S.VALIDATION_FAILED},
    S.VALIDATION_FAILED: {S.GENERATION_PENDING, S.FAILED},
    S.VALIDATION_COMPLETE: {S.OWNER_REVIEW_PENDING, S.FAILED},

    S.OWNER_REVIEW_PENDING: {
        S.APPROVED,
        S.REJECTED,
        S.REVISION_REQUESTED,
        S.CANCELLED,
    },
    S.REJECTED: {S.REVISION_REQUESTED, S.CANCELLED},
    S.REVISION_REQUESTED: {S.ADAPTATION_PLANNED, S.CANCELLED},
    S.APPROVED: {S.PRODUCTION_PACKAGING_PENDING, S.FAILED},

    S.PRODUCTION_PACKAGING_PENDING: {S.PRODUCTION_PACKAGE_CREATED, S.FAILED},
    S.PRODUCTION_PACKAGE_CREATED: {S.COMPLETED},

    # Terminal states: no outgoing transitions.
    S.COMPLETED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
}


# ---------------------------------------------------------------------------
# State classifications
# ---------------------------------------------------------------------------

TERMINAL_STATES: Set[PrintWorkflowState] = {S.COMPLETED, S.FAILED, S.CANCELLED}

FAILURE_STATES: Set[PrintWorkflowState] = {
    S.NORMALIZATION_FAILED,
    S.SPECIFICATION_FAILED,
    S.COMPLIANCE_FAILED,
    S.GENERATION_FAILED,
    S.VALIDATION_FAILED,
    S.FAILED,
}

HUMAN_REVIEW_STATES: Set[PrintWorkflowState] = {
    S.NORMALIZATION_NEEDS_REVIEW,
    S.OWNER_REVIEW_PENDING,
    S.REVISION_REQUESTED,
    S.REJECTED,
}

# Non-terminal failure states that can re-enter the pipeline (retry/recover).
RETRYABLE_STATES: Set[PrintWorkflowState] = {
    S.NORMALIZATION_FAILED,
    S.SPECIFICATION_FAILED,
    S.COMPLIANCE_FAILED,
    S.GENERATION_FAILED,
    S.VALIDATION_FAILED,
}


# ---------------------------------------------------------------------------
# Helper functions (pure)
# ---------------------------------------------------------------------------


def can_transition(
    from_state: PrintWorkflowState,
    to_state: PrintWorkflowState,
) -> bool:
    """Return True if moving from `from_state` to `to_state` is a legal transition."""
    return to_state in LEGAL_TRANSITIONS.get(from_state, set())


def get_allowed_transitions(state: PrintWorkflowState) -> Set[PrintWorkflowState]:
    """Return the set of states reachable in one legal step from `state`."""
    return set(LEGAL_TRANSITIONS.get(state, set()))


def is_terminal_state(state: PrintWorkflowState) -> bool:
    """Return True if `state` has no outgoing transitions."""
    return state in TERMINAL_STATES


def is_failure_state(state: PrintWorkflowState) -> bool:
    """Return True if `state` represents a failure (stage-level or terminal)."""
    return state in FAILURE_STATES


def is_human_review_state(state: PrintWorkflowState) -> bool:
    """Return True if `state` is awaiting a human decision/action."""
    return state in HUMAN_REVIEW_STATES


def is_retryable_state(state: PrintWorkflowState) -> bool:
    """Return True if `state` is a non-terminal failure that can re-enter the pipeline."""
    return state in RETRYABLE_STATES
