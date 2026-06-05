"""
Approval Workflow Service.

Assembles ApprovalPackage requests after validation passes and records human
ApprovalDecision outcomes. This stage routes validated outputs for review — it
must NOT call AI, perform file I/O, create ProductionPackage, or orchestrate
workflow transitions.
"""

from typing import List

from app.domain.print_schemas import (
    ApprovalDecision,
    ApprovalPackage,
    ApprovalStatus,
    DesignJob,
    PrintSpecification,
    ResultStatus,
    ValidationResult,
)


def _approval_next_steps(status: ApprovalStatus) -> str:
    """Return operator guidance for a recorded human approval outcome."""
    if status == ApprovalStatus.APPROVED:
        return "Proceed to production packaging."
    if status == ApprovalStatus.REJECTED:
        return "Do not proceed; the selected output was rejected."
    if status == ApprovalStatus.CHANGES_REQUESTED:
        return "Route back for revision before re-submitting for approval."
    return "Awaiting human approval decision."


def create_approval_package(
    validation_result: ValidationResult,
    outputs: List[object],
    specification: PrintSpecification,
    design_job: DesignJob,
) -> ApprovalPackage:
    """Assemble an approval package from a passed validation result."""
    if validation_result is None:
        raise ValueError("validation_result is required to create an approval package")

    if validation_result.status != ResultStatus.PASSED:
        raise ValueError(
            "approval package can only be created when validation status is PASSED"
        )

    candidate_ids = list(validation_result.passed_candidate_ids)

    return ApprovalPackage(
        package_id=f"approval-{specification.spec_id}",
        spec_id=specification.spec_id,
        job_id=design_job.job_id,
        candidate_ids=candidate_ids,
        validation_reference=validation_result.spec_id,
        status=ResultStatus.PENDING,
        next_steps="Validated outputs are ready for owner review.",
    )


def record_approval_decision(
    approval_package: ApprovalPackage,
    candidate_id: str | None,
    status: ApprovalStatus,
    approver: str,
) -> ApprovalDecision:
    """Record a human approval decision against an approval package."""
    if approval_package is None:
        raise ValueError("approval_package is required to record an approval decision")

    return ApprovalDecision(
        decision_id=f"decision-{approval_package.package_id}",
        spec_id=approval_package.spec_id,
        candidate_id=candidate_id,
        status=status,
        approver=approver,
        next_steps=_approval_next_steps(status),
    )
