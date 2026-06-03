"""
Print Specification Resolution Service.

Resolves the production requirements for a DesignJob into a PrintSpecification.
This is the SPECIFICATION stage: it sits strictly downstream of normalization and
derives requirements from the DesignJob's product_type plus internal preset rules.

It must NOT:
  - normalize customer intent,
  - inspect image files or dimensions,
  - evaluate compliance,
  - create an AdaptationPlan,
  - read external config files, or
  - perform file I/O.

For the MVP, rules come from an internal deterministic preset table
(`_PRODUCT_RULES`). When config is externalized later, only this table / lookup
changes — the public contract stays the same. Everything here is deterministic.
"""

from typing import Any, Dict, List

from app.domain.print_schemas import (
    AssetRole,
    ColorRequirement,
    DesignJob,
    DimensionRequirement,
    PrintSpecification,
    ProductType,
)

CONFIG_SOURCE = "mvp-print-presets-v1"

# Deterministic MVP preset rules, keyed by ProductType. Each entry fully
# describes the production requirements for that product. This is the seam that
# will later be replaced by externalized configuration.
_PRODUCT_RULES: Dict[ProductType, Dict[str, Any]] = {
    ProductType.BANNER: {
        "dimensions": {
            "width_mm": 2000.0,
            "height_mm": 1000.0,
            "bleed_mm": 10.0,
            "safe_margin_mm": 25.0,
            "min_dpi": 300,
        },
        "color": {"color_mode": "CMYK", "color_profile": "FOGRA39"},
        "accepted_formats": ["pdf", "tiff"],
        "required_asset_roles": [AssetRole.PRIMARY],
    },
    ProductType.POSTER: {
        "dimensions": {
            "width_mm": 594.0,
            "height_mm": 841.0,
            "bleed_mm": 3.0,
            "safe_margin_mm": 5.0,
            "min_dpi": 300,
        },
        "color": {"color_mode": "CMYK", "color_profile": "FOGRA39"},
        "accepted_formats": ["pdf", "png", "tiff"],
        "required_asset_roles": [AssetRole.PRIMARY],
    },
    ProductType.BUSINESS_CARD: {
        "dimensions": {
            "width_mm": 85.0,
            "height_mm": 55.0,
            "bleed_mm": 3.0,
            "safe_margin_mm": 4.0,
            "min_dpi": 300,
        },
        "color": {"color_mode": "CMYK", "color_profile": "FOGRA39"},
        "accepted_formats": ["pdf"],
        "required_asset_roles": [AssetRole.PRIMARY],
    },
    ProductType.FLYER: {
        "dimensions": {
            "width_mm": 210.0,
            "height_mm": 297.0,
            "bleed_mm": 3.0,
            "safe_margin_mm": 5.0,
            "min_dpi": 300,
        },
        "color": {"color_mode": "CMYK", "color_profile": "FOGRA39"},
        "accepted_formats": ["pdf", "png"],
        "required_asset_roles": [AssetRole.PRIMARY],
    },
}


def _get_product_rules(product_type: ProductType) -> Dict[str, Any]:
    """Look up preset rules for a product type, or raise if unavailable."""
    rules = _PRODUCT_RULES.get(product_type)
    if rules is None:
        raise ValueError(
            f"No print specification rules available for product type "
            f"'{getattr(product_type, 'value', product_type)}'"
        )
    return rules


def resolve_specification(design_job: DesignJob) -> PrintSpecification:
    """
    Resolve a DesignJob into a PrintSpecification using internal preset rules.

    Deterministic: the same DesignJob always yields an equivalent specification
    with a stable `spec_id`. Depends only on the DesignJob (no RawSubmission or
    NormalizationResult required).
    """
    rules = _get_product_rules(design_job.product_type)

    dimensions = DimensionRequirement(**rules["dimensions"])
    color = ColorRequirement(**rules["color"])
    accepted_formats: List[str] = list(rules["accepted_formats"])
    required_asset_roles: List[AssetRole] = list(rules["required_asset_roles"])

    return PrintSpecification(
        spec_id=f"spec-{design_job.job_id}",
        job_id=design_job.job_id,
        product_type=design_job.product_type,
        dimensions=dimensions,
        color=color,
        accepted_formats=accepted_formats,
        required_asset_roles=required_asset_roles,
        config_source=CONFIG_SOURCE,
    )
