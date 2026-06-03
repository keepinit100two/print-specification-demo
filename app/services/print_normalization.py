"""
Print Normalization Service.

Turns a loose, untrusted RawSubmission into a typed DesignJob, wrapped in a
NormalizationResult. This is the NORMALIZATION stage: it captures intent and
content only.

It must NOT:
  - produce a PrintSpecification,
  - create any production requirement (dimensions, bleed, DPI, color, formats),
  - inspect image dimensions,
  - read configuration, or
  - perform file I/O.

Everything here is deterministic and pure.
"""

import re
from typing import List, Optional

from app.domain.print_schemas import (
    AssetRole,
    DesignJob,
    NormalizationResult,
    ProductType,
    RawSubmission,
    ResultStatus,
    StageIssue,
    SubmittedAsset,
)

# Ordered alias table. Each entry maps a substring (matched case-insensitively
# against the requested product) to a ProductType. Order is fixed so resolution
# is deterministic. More specific phrases come before generic ones.
_PRODUCT_ALIASES = [
    ("business card", ProductType.BUSINESS_CARD),
    ("businesscard", ProductType.BUSINESS_CARD),
    ("flyer", ProductType.FLYER),
    ("poster", ProductType.POSTER),
    ("brochure", ProductType.BROCHURE),
    ("sticker", ProductType.STICKER),
    ("banner", ProductType.BANNER),
    ("apparel", ProductType.APPAREL),
    ("t-shirt", ProductType.APPAREL),
    ("tshirt", ProductType.APPAREL),
    ("shirt", ProductType.APPAREL),
    ("packaging", ProductType.PACKAGING),
    ("package", ProductType.PACKAGING),
    ("box", ProductType.PACKAGING),
]


def _normalize_product(requested_product: Optional[str]) -> Optional[ProductType]:
    """
    Resolve a free-text product into a ProductType.

    Returns None when no known alias matches (caller distinguishes
    missing vs. unknown by inspecting the raw value separately).
    """
    if not requested_product or not requested_product.strip():
        return None

    text = requested_product.strip().lower()
    for alias, product_type in _PRODUCT_ALIASES:
        if alias in text:
            return product_type
    return None


def _normalize_brief(brief: Optional[str]) -> str:
    """Collapse whitespace and trim the free-text brief into a stable string."""
    if not brief:
        return ""
    return re.sub(r"\s+", " ", brief).strip()


def _has_primary_asset(assets: List[SubmittedAsset]) -> bool:
    """True if at least one asset plays the PRIMARY role."""
    return any(asset.role == AssetRole.PRIMARY for asset in assets)


def _needs_review(reasons: List[StageIssue]) -> NormalizationResult:
    """Build a NEEDS_REVIEW result (no DesignJob) from collected issues."""
    return NormalizationResult(
        status=ResultStatus.NEEDS_REVIEW,
        design_job=None,
        reasons=reasons,
        next_steps=(
            "Resolve the listed issues with the requester, then resubmit for "
            "normalization."
        ),
    )


def normalize_submission(raw_submission: RawSubmission) -> NormalizationResult:
    """Normalize a RawSubmission into a NormalizationResult carrying a DesignJob."""
    reasons: List[StageIssue] = []

    raw_product = raw_submission.requested_product
    product_type: Optional[ProductType] = None

    if not raw_product or not raw_product.strip():
        reasons.append(
            StageIssue(
                code="MISSING_PRODUCT",
                message="No requested product was provided.",
                field="requested_product",
            )
        )
    else:
        product_type = _normalize_product(raw_product)
        if product_type is None:
            reasons.append(
                StageIssue(
                    code="UNKNOWN_PRODUCT",
                    message=f"Could not map requested product '{raw_product}' to a known product type.",
                    field="requested_product",
                )
            )

    normalized_brief = _normalize_brief(raw_submission.brief)
    if not normalized_brief:
        reasons.append(
            StageIssue(
                code="MISSING_BRIEF",
                message="No creative brief was provided.",
                field="brief",
            )
        )

    if not _has_primary_asset(raw_submission.assets):
        reasons.append(
            StageIssue(
                code="MISSING_PRIMARY_ASSET",
                message="At least one primary asset is required.",
                field="assets",
            )
        )

    if reasons or product_type is None:
        return _needs_review(reasons)

    design_job = DesignJob(
        job_id=f"job-{raw_submission.submission_id}",
        submission_id=raw_submission.submission_id,
        product_type=product_type,
        title=normalized_brief[:80] or None,
        normalized_brief=normalized_brief,
        requested_quantity=1,
        assets=list(raw_submission.assets),
    )

    return NormalizationResult(
        status=ResultStatus.PASSED,
        design_job=design_job,
        reasons=[],
        next_steps="Proceed to specification resolution.",
    )
