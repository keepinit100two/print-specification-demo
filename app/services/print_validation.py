"""
Print Validation Service.

Validates generated or deterministically transformed outputs against a
PrintSpecification (Option A: print-readiness only).

This stage measures width_px, height_px, DPI, file format, and color profile
(when both sides provide one). It must NOT judge marketing quality, brand fit,
aesthetics, copywriting, or human approval.

It must NOT call AI, perform file I/O, or create ApprovalDecision.
Everything here is deterministic and pure.
"""

import math
from typing import List, Union

from app.domain.print_schemas import (
    ComplianceFinding,
    FailureSeverity,
    GeneratedCandidate,
    ImageProperties,
    PrintSpecification,
    ResultStatus,
    StageIssue,
    TransformedAsset,
    ValidationResult,
)

AssetInput = Union[GeneratedCandidate, TransformedAsset]


def _required_px(length_mm: float, min_dpi: int) -> int:
    """Minimum pixels needed to print `length_mm` at `min_dpi` (1 in = 25.4 mm)."""
    return math.ceil(length_mm / 25.4 * min_dpi)


def _output_id(asset: AssetInput) -> str:
    """Return the stable output id for a generated or transformed asset."""
    if isinstance(asset, GeneratedCandidate):
        return asset.candidate_id
    return asset.transformed_asset_id


def _image_properties(asset: AssetInput) -> ImageProperties | None:
    """Return observed image properties for the asset, if any."""
    if isinstance(asset, GeneratedCandidate):
        return asset.properties
    return asset.properties


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


def _color_profile_ok(
    image_properties: ImageProperties,
    specification: PrintSpecification,
) -> bool | None:
    """
    Return True/False when both spec and asset color profiles are present.

    Return None when color profile validation is skipped for MVP.
    """
    spec_profile = specification.color.color_profile
    asset_profile = image_properties.color_profile
    if not spec_profile or not asset_profile:
        return None
    return spec_profile.lower() == asset_profile.lower()


def validate_print_asset(
    specification: PrintSpecification,
    asset: AssetInput | None,
) -> ValidationResult:
    """Validate a generated or transformed output against the specification."""
    if asset is None:
        raise ValueError("asset is required for print validation")

    output_id = _output_id(asset)
    request_id = asset.request_id if isinstance(asset, GeneratedCandidate) else None

    # validated_candidate_ids / passed_candidate_ids are legacy field names;
    # they currently hold validated *output* ids (AI candidates or transforms).
    validated_ids = [output_id]
    passed_ids: List[str] = []

    image_properties = _image_properties(asset)
    missing_metadata = (
        image_properties is None
        or image_properties.width_px is None
        or image_properties.height_px is None
        or image_properties.dpi is None
    )
    if missing_metadata:
        return ValidationResult(
            status=ResultStatus.NEEDS_REVIEW,
            spec_id=specification.spec_id,
            request_id=request_id,
            validated_candidate_ids=validated_ids,
            passed_candidate_ids=passed_ids,
            findings=[],
            reasons=[
                StageIssue(
                    code="MISSING_VALIDATION_METADATA",
                    message=(
                        "Output is missing required metadata (width_px, height_px, "
                        "and/or dpi); cannot validate print readiness."
                    ),
                    severity=FailureSeverity.WARNING,
                    field="properties",
                )
            ],
            next_steps=(
                "Provide complete output metadata (dimensions and DPI), then "
                "re-run validation."
            ),
        )

    min_dpi = specification.dimensions.min_dpi
    required_width_px = _required_px(specification.dimensions.width_mm, min_dpi)
    required_height_px = _required_px(specification.dimensions.height_mm, min_dpi)

    findings: List[ComplianceFinding] = [
        ComplianceFinding(
            asset_id=output_id,
            requirement="width_px",
            expected=required_width_px,
            actual=image_properties.width_px,
            compliant=image_properties.width_px >= required_width_px,
            severity=FailureSeverity.ERROR,
        ),
        ComplianceFinding(
            asset_id=output_id,
            requirement="height_px",
            expected=required_height_px,
            actual=image_properties.height_px,
            compliant=image_properties.height_px >= required_height_px,
            severity=FailureSeverity.ERROR,
        ),
        ComplianceFinding(
            asset_id=output_id,
            requirement="min_dpi",
            expected=min_dpi,
            actual=image_properties.dpi,
            compliant=image_properties.dpi >= min_dpi,
            severity=FailureSeverity.ERROR,
        ),
        ComplianceFinding(
            asset_id=output_id,
            requirement="file_format",
            expected=specification.accepted_formats,
            actual=image_properties.file_format,
            compliant=_format_ok(image_properties, specification),
            severity=FailureSeverity.ERROR,
        ),
    ]

    color_profile_result = _color_profile_ok(image_properties, specification)
    if color_profile_result is not None:
        findings.append(
            ComplianceFinding(
                asset_id=output_id,
                requirement="color_profile",
                expected=specification.color.color_profile,
                actual=image_properties.color_profile,
                compliant=color_profile_result,
                severity=FailureSeverity.ERROR,
            )
        )

    all_compliant = all(f.compliant for f in findings)
    if all_compliant:
        passed_ids = [output_id]
        return ValidationResult(
            status=ResultStatus.PASSED,
            spec_id=specification.spec_id,
            request_id=request_id,
            validated_candidate_ids=validated_ids,
            passed_candidate_ids=passed_ids,
            findings=findings,
            next_steps="Output meets the specification; proceed to approval routing.",
        )

    return ValidationResult(
        status=ResultStatus.FAILED,
        spec_id=specification.spec_id,
        request_id=request_id,
        validated_candidate_ids=validated_ids,
        passed_candidate_ids=passed_ids,
        findings=findings,
        next_steps="Output does not meet the specification; regenerate or remediate.",
    )
