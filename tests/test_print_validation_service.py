"""
Failing tests for the future Print Validation Service.

Expected future module:   app/services/print_validation.py
Expected future function:
    validate_print_asset(
        specification: PrintSpecification,
        asset: GeneratedCandidate | TransformedAsset,
    ) -> ValidationResult

Validation scope (Option A only): whether the output satisfies the
PrintSpecification — dimensions / pixel size, DPI, file format, and color
profile when available.

This stage must NOT validate marketing quality, brand quality, copywriting,
visual aesthetics, or human approval.

These tests are expected to FAIL initially because the module does not exist
yet. They are not skipped or xfailed — they define the contract the service
must satisfy.
"""

import math
import uuid

import pytest

from app.domain.print_schemas import (
    ApprovalDecision,
    DesignJob,
    DeterministicTransformStatus,
    GeneratedCandidate,
    ImageProperties,
    ProductType,
    ResultStatus,
    TransformedAsset,
    ValidationResult,
)
from app.services.print_specification import resolve_specification
from app.services.print_validation import validate_print_asset


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
    """Pixel dimensions that comfortably satisfy the spec at min_dpi."""
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


def _generated_candidate(spec, *, properties=None, candidate_id=None) -> GeneratedCandidate:
    properties = properties if properties is not None else _compliant_image(spec)
    candidate_id = candidate_id or f"candidate-{uuid.uuid4()}"
    return GeneratedCandidate(
        candidate_id=candidate_id,
        request_id=f"genreq-{candidate_id}",
        uri=f"artifact://generated/{candidate_id}.png",
        properties=properties,
    )


def _transformed_asset(spec, design_job, *, properties=None, transformed_asset_id=None):
    properties = properties if properties is not None else _compliant_image(spec)
    transformed_asset_id = transformed_asset_id or f"transformed-{uuid.uuid4()}"
    plan_id = f"plan-{design_job.job_id}"
    return TransformedAsset(
        transformed_asset_id=transformed_asset_id,
        job_id=design_job.job_id,
        spec_id=spec.spec_id,
        plan_id=plan_id,
        uri=f"artifact://transformed/{plan_id}-001",
        properties=properties,
        status=DeterministicTransformStatus.SUCCEEDED,
    )


def _findings_by_requirement(result, requirement):
    return [f for f in result.findings if f.requirement == requirement]


# ---------------------------------------------------------------------------
# 1. GeneratedCandidate matching spec passes
# ---------------------------------------------------------------------------


def test_generated_candidate_matching_spec_passes():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    candidate = _generated_candidate(spec)

    result = validate_print_asset(spec, candidate)

    assert isinstance(result, ValidationResult)
    assert result.status == ResultStatus.PASSED
    assert candidate.candidate_id in result.validated_candidate_ids
    assert candidate.candidate_id in result.passed_candidate_ids
    assert not any(f.compliant is False for f in result.findings)
    assert result.next_steps


# ---------------------------------------------------------------------------
# 2. GeneratedCandidate low DPI fails
# ---------------------------------------------------------------------------


def test_generated_candidate_low_dpi_fails():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    image = _compliant_image(spec)
    image.dpi = spec.dimensions.min_dpi - 150
    candidate = _generated_candidate(spec, properties=image)

    result = validate_print_asset(spec, candidate)

    assert result.status == ResultStatus.FAILED
    assert candidate.candidate_id in result.validated_candidate_ids
    assert candidate.candidate_id not in result.passed_candidate_ids

    dpi_findings = _findings_by_requirement(result, "min_dpi")
    assert dpi_findings
    assert dpi_findings[0].compliant is False


# ---------------------------------------------------------------------------
# 3. GeneratedCandidate unsupported format fails
# ---------------------------------------------------------------------------


def test_generated_candidate_unsupported_format_fails():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    image = _compliant_image(spec)
    image.file_format = "gif"
    assert image.file_format not in spec.accepted_formats
    candidate = _generated_candidate(spec, properties=image)

    result = validate_print_asset(spec, candidate)

    assert result.status == ResultStatus.FAILED
    assert _findings_by_requirement(result, "file_format")


# ---------------------------------------------------------------------------
# 4. TransformedAsset matching spec passes
# ---------------------------------------------------------------------------


def test_transformed_asset_matching_spec_passes():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    asset = _transformed_asset(spec, job)

    result = validate_print_asset(spec, asset)

    assert result.status == ResultStatus.PASSED
    # TODO: ValidationResult uses validated_candidate_ids / passed_candidate_ids
    # today; treat them as validated *output* ids (AI candidates or deterministic
    # transforms) until a dedicated validated_asset_ids field exists.
    assert asset.transformed_asset_id in result.validated_candidate_ids
    assert asset.transformed_asset_id in result.passed_candidate_ids
    assert not any(f.compliant is False for f in result.findings)
    assert result.next_steps


# ---------------------------------------------------------------------------
# 5. Missing image metadata needs review
# ---------------------------------------------------------------------------


def test_missing_image_metadata_needs_review():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    image = ImageProperties(
        width_px=None,
        height_px=None,
        dpi=None,
        color_profile=None,
        file_format=spec.accepted_formats[0],
    )
    candidate = _generated_candidate(spec, properties=image)

    result = validate_print_asset(spec, candidate)

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert "MISSING_VALIDATION_METADATA" in [issue.code for issue in result.reasons]
    assert result.next_steps


# ---------------------------------------------------------------------------
# 6. Missing asset raises ValueError
# ---------------------------------------------------------------------------


def test_missing_asset_raises_value_error():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)

    with pytest.raises(ValueError):
        validate_print_asset(spec, None)


# ---------------------------------------------------------------------------
# 7. Validation does not approve
# ---------------------------------------------------------------------------


def test_validation_does_not_approve():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    candidate = _generated_candidate(spec)

    result = validate_print_asset(spec, candidate)

    fields = set(ValidationResult.model_fields.keys())
    assert "approval" not in fields
    assert "approval_decision" not in fields
    assert "approved" not in fields
    assert not isinstance(result, ApprovalDecision)
