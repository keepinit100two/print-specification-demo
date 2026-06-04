import math
import uuid

import pytest

from app.domain.print_schemas import (
    ComplianceResult,
    DesignJob,
    ImageProperties,
    ProductType,
    ResultStatus,
)
from app.services.print_specification import resolve_specification

# Expected to FAIL until app/services/print_compliance.py exists. These tests
# define the contract the future implementation must satisfy. They are
# intentionally not skipped/xfailed.
from app.services.print_compliance import evaluate_compliance


def _design_job(product_type: ProductType = ProductType.BANNER) -> DesignJob:
    return DesignJob(
        job_id=f"job-{uuid.uuid4()}",
        submission_id=str(uuid.uuid4()),
        product_type=product_type,
        title="Summer sale",
        normalized_brief="A bold outdoor banner advertising a summer sale.",
        requested_quantity=1,
    )


def _required_px(spec) -> tuple:
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


def _findings_by_requirement(result, requirement):
    return [f for f in result.findings if f.requirement == requirement]


def test_image_meeting_spec_passes():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    image = _compliant_image(spec)

    result = evaluate_compliance(job, spec, image)

    assert result.status == ResultStatus.PASSED
    assert result.is_print_ready is True
    assert all(f.compliant for f in result.findings)
    assert result.next_steps


def test_low_dpi_fails_compliance():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    image = _compliant_image(spec)
    image.dpi = spec.dimensions.min_dpi - 150

    result = evaluate_compliance(job, spec, image)

    assert result.status == ResultStatus.FAILED
    assert result.is_print_ready is False

    dpi_findings = _findings_by_requirement(result, "min_dpi")
    assert dpi_findings
    finding = dpi_findings[0]
    assert finding.expected == spec.dimensions.min_dpi
    assert finding.actual == image.dpi
    assert finding.compliant is False


def test_unsupported_file_format_fails_compliance():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    image = _compliant_image(spec)
    image.file_format = "gif"  # deliberately not in accepted_formats

    assert image.file_format not in spec.accepted_formats

    result = evaluate_compliance(job, spec, image)

    assert result.status == ResultStatus.FAILED
    assert result.is_print_ready is False
    assert _findings_by_requirement(result, "file_format")


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

    result = evaluate_compliance(job, spec, image)

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.is_print_ready is False
    assert "MISSING_IMAGE_METADATA" in [issue.code for issue in result.reasons]
    assert result.next_steps


def test_compliance_does_not_create_adaptation_or_generation():
    fields = set(ComplianceResult.model_fields.keys())
    assert "adaptation_plan" not in fields
    assert "generation_request" not in fields


def test_compliance_references_correct_job_and_spec():
    job = _design_job(ProductType.BANNER)
    spec = resolve_specification(job)
    image = _compliant_image(spec)

    result = evaluate_compliance(job, spec, image)

    assert result.job_id == job.job_id
    assert result.spec_id == spec.spec_id
