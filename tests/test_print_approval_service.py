"""
Failing tests for the future Approval Workflow Service.

Expected future module:   app/services/print_approval.py
Expected future functions:
    create_approval_package(
        validation_result: ValidationResult,
        outputs: list,
        specification: PrintSpecification,
        design_job: DesignJob,
    ) -> ApprovalPackage

    record_approval_decision(
        approval_package: ApprovalPackage,
        candidate_id: str | None,
        status: ApprovalStatus,
        approver: str,
    ) -> ApprovalDecision

These tests are expected to FAIL initially because the module does not exist
yet. They are not skipped or xfailed — they define the contract the service
must satisfy.

Approval routes validated outputs for human review. It must NOT call AI,
perform file I/O, orchestrate workflow transitions, or create production packages.
"""

import math
import uuid

import pytest

from app.domain.print_schemas import (
    ApprovalDecision,
    ApprovalPackage,
    ApprovalStatus,
    DesignJob,
    GeneratedCandidate,
    ImageProperties,
    ProductType,
    ResultStatus,
    ValidationResult,
)
from app.services.print_specification import resolve_specification
from app.services.print_approval import (
    create_approval_package,
    record_approval_decision,
)


def _design_job(product_type: ProductType = ProductType.BANNER) -> DesignJob:
    submission_id = str(uuid.uuid4())
    return DesignJob(
        job_id=f"job-{submission_id}",
        submission_id=submission_id,
        product_type=product_type,
        title="Summer sale",
        normalized_brief="A bold outdoor banner advertising a summer sale.",
        requested_quantity=1,
    )


def _required_px(spec) -> tuple[int, int]:
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


def _generated_candidate(spec, *, candidate_id=None) -> GeneratedCandidate:
    candidate_id = candidate_id or f"candidate-{uuid.uuid4()}"
    return GeneratedCandidate(
        candidate_id=candidate_id,
        request_id=f"genreq-{candidate_id}",
        uri=f"artifact://generated/{candidate_id}.png",
        properties=_compliant_image(spec),
    )


def _passed_validation(spec, candidate_id: str) -> ValidationResult:
    return ValidationResult(
        status=ResultStatus.PASSED,
        spec_id=spec.spec_id,
        validated_candidate_ids=[candidate_id],
        passed_candidate_ids=[candidate_id],
        next_steps="Proceed to owner review.",
    )


def _failed_validation(spec, candidate_id: str) -> ValidationResult:
    return ValidationResult(
        status=ResultStatus.FAILED,
        spec_id=spec.spec_id,
        validated_candidate_ids=[candidate_id],
        passed_candidate_ids=[],
        next_steps="Regenerate or remediate before approval.",
    )


def _approval_package_for_decision():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    candidate = _generated_candidate(spec)
    validation = _passed_validation(spec, candidate.candidate_id)
    package = create_approval_package(
        validation,
        [candidate],
        spec,
        job,
    )
    return package, candidate.candidate_id


# ---------------------------------------------------------------------------
# 1. Validation success creates approval package
# ---------------------------------------------------------------------------


def test_validation_success_creates_approval_package():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    candidate = _generated_candidate(spec)
    validation = _passed_validation(spec, candidate.candidate_id)

    package = create_approval_package(validation, [candidate], spec, job)

    assert isinstance(package, ApprovalPackage)
    assert package.spec_id == spec.spec_id
    assert package.job_id == job.job_id
    assert candidate.candidate_id in package.candidate_ids
    assert package.status == ResultStatus.PENDING


# ---------------------------------------------------------------------------
# 2. Validation failure cannot create package
# ---------------------------------------------------------------------------


def test_validation_failure_cannot_create_package():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    candidate = _generated_candidate(spec)
    validation = _failed_validation(spec, candidate.candidate_id)

    with pytest.raises(ValueError):
        create_approval_package(validation, [candidate], spec, job)


# ---------------------------------------------------------------------------
# 3. Approval decision approved
# ---------------------------------------------------------------------------


def test_approval_decision_approved():
    package, candidate_id = _approval_package_for_decision()
    approver = "owner@example.com"

    decision = record_approval_decision(
        package,
        candidate_id,
        ApprovalStatus.APPROVED,
        approver,
    )

    assert isinstance(decision, ApprovalDecision)
    assert decision.status == ApprovalStatus.APPROVED
    assert decision.candidate_id == candidate_id
    assert decision.approver == approver


# ---------------------------------------------------------------------------
# 4. Approval decision rejected
# ---------------------------------------------------------------------------


def test_approval_decision_rejected():
    package, candidate_id = _approval_package_for_decision()

    decision = record_approval_decision(
        package,
        candidate_id,
        ApprovalStatus.REJECTED,
        "owner@example.com",
    )

    assert decision.status == ApprovalStatus.REJECTED


# ---------------------------------------------------------------------------
# 5. Approval decision changes requested
# ---------------------------------------------------------------------------


def test_approval_decision_changes_requested():
    package, candidate_id = _approval_package_for_decision()

    decision = record_approval_decision(
        package,
        candidate_id,
        ApprovalStatus.CHANGES_REQUESTED,
        "owner@example.com",
    )

    assert decision.status == ApprovalStatus.CHANGES_REQUESTED


# ---------------------------------------------------------------------------
# 6. Missing approval package raises ValueError
# ---------------------------------------------------------------------------


def test_missing_approval_package_raises_value_error():
    with pytest.raises(ValueError):
        record_approval_decision(
            None,
            "candidate-001",
            ApprovalStatus.APPROVED,
            "owner@example.com",
        )


# ---------------------------------------------------------------------------
# 7. Approval does not create production package
# ---------------------------------------------------------------------------


def test_approval_does_not_create_production_package():
    package, candidate_id = _approval_package_for_decision()
    decision = record_approval_decision(
        package,
        candidate_id,
        ApprovalStatus.APPROVED,
        "owner@example.com",
    )

    package_fields = set(ApprovalPackage.model_fields.keys())
    decision_fields = set(ApprovalDecision.model_fields.keys())

    assert "production_package" not in package_fields
    assert "production" not in package_fields
    assert "production_package" not in decision_fields
    assert "production" not in decision_fields
    assert not hasattr(decision, "production_package")
    assert not hasattr(package, "production_package")
