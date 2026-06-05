"""
Production Packaging Service.

Assembles ProductionPackage bundles from approved outputs after human approval.
This stage packages validated, approved assets for the print shop — it must NOT
execute printing, perform file I/O, call AI, or handle shipping.
"""

from typing import Union

from app.domain.print_schemas import (
    ApprovalDecision,
    ApprovalStatus,
    GeneratedCandidate,
    PrintSpecification,
    ProductionPackage,
    ResultStatus,
    TransformedAsset,
    ValidationResult,
)

AssetInput = Union[GeneratedCandidate, TransformedAsset]


def _approved_output_id(output_asset: AssetInput) -> str:
    """Return the stable approved output id for a generated or transformed asset."""
    if isinstance(output_asset, GeneratedCandidate):
        return output_asset.candidate_id
    return output_asset.transformed_asset_id


def _output_uri(output_asset: AssetInput) -> str:
    """Return the asset location reference without performing file I/O."""
    return output_asset.uri


def create_production_package(
    approval_decision: ApprovalDecision | None,
    specification: PrintSpecification,
    validation_result: ValidationResult,
    output_asset: AssetInput | None,
) -> ProductionPackage:
    """Assemble a production package from an approved output and its context."""
    if approval_decision is None:
        raise ValueError("approval_decision is required to create a production package")

    if output_asset is None:
        raise ValueError("output_asset is required to create a production package")

    if approval_decision.status != ApprovalStatus.APPROVED:
        raise ValueError(
            "production package can only be created when approval status is APPROVED"
        )

    if validation_result.status != ResultStatus.PASSED:
        raise ValueError(
            "production package can only be created when validation status is PASSED"
        )

    approved_id = _approved_output_id(output_asset)
    output_uri = _output_uri(output_asset)

    manifest = {
        "product_type": specification.product_type.value,
        "width_mm": specification.dimensions.width_mm,
        "height_mm": specification.dimensions.height_mm,
        "dimensions": {
            "width_mm": specification.dimensions.width_mm,
            "height_mm": specification.dimensions.height_mm,
            "bleed_mm": specification.dimensions.bleed_mm,
            "safe_margin_mm": specification.dimensions.safe_margin_mm,
        },
        "min_dpi": specification.dimensions.min_dpi,
        "dpi": specification.dimensions.min_dpi,
        "accepted_formats": list(specification.accepted_formats),
        "approved_output_id": approved_id,
        "output_uri": output_uri,
        "approved_by": approval_decision.approver,
        "approver": approval_decision.approver,
        "approval_decision_id": approval_decision.decision_id,
        "validation_spec_id": validation_result.spec_id,
    }

    return ProductionPackage(
        package_id=f"production-{specification.spec_id}-{approved_id}",
        spec_id=specification.spec_id,
        job_id=specification.job_id,
        candidate_id=approved_id,
        decision_id=approval_decision.decision_id,
        status=ResultStatus.PASSED,
        output_uris=[output_uri],
        manifest=manifest,
    )
