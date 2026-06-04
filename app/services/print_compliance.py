"""
Print Technical Compliance Service.

Measures whether submitted image properties satisfy a PrintSpecification. This is
the COMPLIANCE stage: it only *measures* readiness and reports findings.

It must NOT:
  - transform or fix images,
  - create an AdaptationPlan,
  - create a GenerationRequest,
  - call any model,
  - inspect files directly,
  - read configuration, or
  - perform file I/O.

Everything here is deterministic and pure.
"""

import math
from typing import List

from app.domain.print_schemas import (
    ComplianceFinding,
    ComplianceResult,
    DesignJob,
    FailureSeverity,
    ImageProperties,
    PrintSpecification,
    ResultStatus,
    StageIssue,
)


def _required_px(length_mm: float, min_dpi: int) -> int:
    """Minimum pixels needed to print `length_mm` at `min_dpi` (1 in = 25.4 mm)."""
    return math.ceil(length_mm / 25.4 * min_dpi)


def evaluate_compliance(
    design_job: DesignJob,
    specification: PrintSpecification,
    image_properties: ImageProperties,
) -> ComplianceResult:
    """Measure submitted image properties against the specification."""
    # Gate: required metadata must be present before pass/fail evaluation.
    missing_metadata = (
        image_properties.width_px is None
        or image_properties.height_px is None
        or image_properties.dpi is None
    )
    if missing_metadata:
        return ComplianceResult(
            status=ResultStatus.NEEDS_REVIEW,
            spec_id=specification.spec_id,
            job_id=design_job.job_id,
            is_print_ready=False,
            findings=[],
            reasons=[
                StageIssue(
                    code="MISSING_IMAGE_METADATA",
                    message=(
                        "Image is missing required metadata (width_px, height_px, "
                        "and/or dpi); cannot measure compliance."
                    ),
                    severity=FailureSeverity.WARNING,
                    field="image_properties",
                )
            ],
            next_steps=(
                "Provide complete image metadata (dimensions and DPI), then "
                "re-run compliance."
            ),
        )

    min_dpi = specification.dimensions.min_dpi
    required_width_px = _required_px(specification.dimensions.width_mm, min_dpi)
    required_height_px = _required_px(specification.dimensions.height_mm, min_dpi)

    findings: List[ComplianceFinding] = [
        ComplianceFinding(
            requirement="width_px",
            expected=required_width_px,
            actual=image_properties.width_px,
            compliant=image_properties.width_px >= required_width_px,
            severity=FailureSeverity.ERROR,
        ),
        ComplianceFinding(
            requirement="height_px",
            expected=required_height_px,
            actual=image_properties.height_px,
            compliant=image_properties.height_px >= required_height_px,
            severity=FailureSeverity.ERROR,
        ),
        ComplianceFinding(
            requirement="min_dpi",
            expected=min_dpi,
            actual=image_properties.dpi,
            compliant=image_properties.dpi >= min_dpi,
            severity=FailureSeverity.ERROR,
        ),
        ComplianceFinding(
            requirement="file_format",
            expected=specification.accepted_formats,
            actual=image_properties.file_format,
            compliant=_format_ok(image_properties, specification),
            severity=FailureSeverity.ERROR,
        ),
    ]

    is_print_ready = all(f.compliant for f in findings)

    if is_print_ready:
        return ComplianceResult(
            status=ResultStatus.PASSED,
            spec_id=specification.spec_id,
            job_id=design_job.job_id,
            is_print_ready=True,
            findings=findings,
            next_steps="Submitted assets meet the specification; proceed.",
        )

    return ComplianceResult(
        status=ResultStatus.FAILED,
        spec_id=specification.spec_id,
        job_id=design_job.job_id,
        is_print_ready=False,
        findings=findings,
        next_steps="Submitted assets do not meet the specification; adaptation required.",
    )


def _format_ok(
    image_properties: ImageProperties,
    specification: PrintSpecification,
) -> bool:
    """True if the image file format is one of the accepted formats."""
    fmt = image_properties.file_format
    if not fmt:
        return False
    accepted = {f.lower() for f in specification.accepted_formats}
    return fmt.lower() in accepted
