"""
Failing tests for the future Production Packaging Service.

Expected future module:   app/services/print_production_packaging.py
Expected future function:
    create_production_package(
        approval_decision: ApprovalDecision,
        specification: PrintSpecification,
        validation_result: ValidationResult,
        output_asset,
    ) -> ProductionPackage

These tests are expected to FAIL initially because the module does not exist
yet. They are not skipped or xfailed — they define the contract the service
must satisfy.

Production packaging assembles an approved output bundle for the print shop.
It must NOT execute printing, perform file I/O, call AI, or handle shipping.
"""

import math
import uuid

import pytest

from app.domain.print_schemas import (
    ApprovalDecision,
    ApprovalStatus,
    DesignJob,
    GeneratedCandidate,
    ImageProperties,
    ProductionPackage,
    ProductType,
    ResultStatus,
    ValidationResult,
)
from app.services.print_approval import create_approval_package, record_approval_decision
from app.services.print_production_packaging import create_production_package
from app.services.print_specification import resolve_specification


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
        next_steps="Proceed to production packaging.",
    )


def _approved_decision(spec, candidate_id: str) -> ApprovalDecision:
    job = _design_job(spec.product_type)
    job = job.model_copy(update={"job_id": spec.job_id})
    candidate = _generated_candidate(spec, candidate_id=candidate_id)
    validation = _passed_validation(spec, candidate_id)
    approval_package = create_approval_package(
        validation,
        [candidate],
        spec,
        job,
    )
    return record_approval_decision(
        approval_package,
        candidate_id,
        ApprovalStatus.APPROVED,
        "owner@example.com",
    )


def _packaging_inputs(*, status=ApprovalStatus.APPROVED, candidate_id=None):
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    candidate = _generated_candidate(spec, candidate_id=candidate_id)
    validation = _passed_validation(spec, candidate.candidate_id)
    approval_package = create_approval_package(
        validation,
        [candidate],
        spec,
        job,
    )
    decision = record_approval_decision(
        approval_package,
        candidate.candidate_id,
        status,
        "owner@example.com",
    )
    return decision, spec, validation, candidate


def _approved_output_id(package: ProductionPackage, candidate_id: str) -> str:
    """Return the packaged approved output id from schema or manifest."""
    if getattr(package, "approved_output_id", None):
        return package.approved_output_id
    if package.candidate_id:
        return package.candidate_id
    return package.manifest.get("approved_output_id", candidate_id)


# ---------------------------------------------------------------------------
# 1. Approved output creates package
# ---------------------------------------------------------------------------


def test_approved_output_creates_production_package():
    decision, spec, validation, candidate = _packaging_inputs(
        status=ApprovalStatus.APPROVED
    )

    package = create_production_package(
        decision,
        spec,
        validation,
        candidate,
    )

    assert isinstance(package, ProductionPackage)
    assert package.spec_id == spec.spec_id
    assert _approved_output_id(package, candidate.candidate_id) == candidate.candidate_id
    assert package.manifest


# ---------------------------------------------------------------------------
# 2. Rejected output cannot create package
# ---------------------------------------------------------------------------


def test_rejected_output_cannot_create_package():
    decision, spec, validation, candidate = _packaging_inputs(
        status=ApprovalStatus.REJECTED
    )

    with pytest.raises(ValueError):
        create_production_package(decision, spec, validation, candidate)


# ---------------------------------------------------------------------------
# 3. Changes requested cannot create package
# ---------------------------------------------------------------------------


def test_changes_requested_cannot_create_package():
    decision, spec, validation, candidate = _packaging_inputs(
        status=ApprovalStatus.CHANGES_REQUESTED
    )

    with pytest.raises(ValueError):
        create_production_package(decision, spec, validation, candidate)


# ---------------------------------------------------------------------------
# 4. Missing approval decision raises ValueError
# ---------------------------------------------------------------------------


def test_missing_approval_decision_raises_value_error():
    _, spec, validation, candidate = _packaging_inputs()

    with pytest.raises(ValueError):
        create_production_package(None, spec, validation, candidate)


# ---------------------------------------------------------------------------
# 5. Missing output asset raises ValueError
# ---------------------------------------------------------------------------


def test_missing_output_asset_raises_value_error():
    decision, spec, validation, _ = _packaging_inputs()

    with pytest.raises(ValueError):
        create_production_package(decision, spec, validation, None)


# ---------------------------------------------------------------------------
# 6. Manifest contains production metadata
# ---------------------------------------------------------------------------


def test_manifest_contains_production_metadata():
    decision, spec, validation, candidate = _packaging_inputs(
        status=ApprovalStatus.APPROVED
    )

    package = create_production_package(
        decision,
        spec,
        validation,
        candidate,
    )

    manifest = package.manifest
    assert manifest.get("product_type") == spec.product_type.value
    assert manifest.get("dimensions") or (
        manifest.get("width_mm") and manifest.get("height_mm")
    )
    assert manifest.get("dpi") or manifest.get("min_dpi") == spec.dimensions.min_dpi
    assert manifest.get("approved_output_id") == candidate.candidate_id


# ---------------------------------------------------------------------------
# 7. Production package does not perform printing
# ---------------------------------------------------------------------------


def test_production_package_does_not_perform_printing():
    decision, spec, validation, candidate = _packaging_inputs(
        status=ApprovalStatus.APPROVED
    )

    package = create_production_package(
        decision,
        spec,
        validation,
        candidate,
    )

    fields = set(ProductionPackage.model_fields.keys())
    assert "print_execution" not in fields
    assert "printing" not in fields
    assert "shipment" not in fields
    assert "shipping" not in fields
    assert "tracking_number" not in fields
    assert not hasattr(package, "print_job_id")
    assert not hasattr(package, "shipping_label_uri")
