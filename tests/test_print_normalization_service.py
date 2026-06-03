import uuid

import pytest

from app.domain.print_schemas import (
    AssetRole,
    DesignJob,
    ProductType,
    RawSubmission,
    ResultStatus,
    SubmittedAsset,
)

# This import is expected to FAIL until app/services/print_normalization.py
# exists. The tests below define the contract that future implementation must
# satisfy. They are intentionally not skipped/xfailed.
from app.services.print_normalization import normalize_submission


def _asset(role: AssetRole = AssetRole.PRIMARY) -> SubmittedAsset:
    return SubmittedAsset(
        asset_id=str(uuid.uuid4()),
        role=role,
        uri="file:///tmp/asset.png",
    )


def _submission(
    requested_product="banner",
    brief="A bold outdoor banner advertising a summer sale.",
    assets=None,
) -> RawSubmission:
    if assets is None:
        assets = [_asset(AssetRole.PRIMARY)]
    return RawSubmission(
        submission_id=str(uuid.uuid4()),
        requester="designer@example.com",
        requested_product=requested_product,
        brief=brief,
        assets=assets,
    )


def _reason_codes(result) -> list:
    return [issue.code for issue in result.reasons]


def test_valid_banner_submission_normalizes():
    submission = _submission(requested_product="banner")

    result = normalize_submission(submission)

    assert result.status == ResultStatus.PASSED
    assert result.design_job is not None

    job = result.design_job
    assert job.product_type == ProductType.BANNER
    assert job.submission_id == submission.submission_id
    assert job.normalized_brief
    assert job.assets == submission.assets


def test_unknown_product_needs_review():
    submission = _submission(requested_product="unknown thing")

    result = normalize_submission(submission)

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.design_job is None
    assert "UNKNOWN_PRODUCT" in _reason_codes(result)
    assert result.next_steps


def test_missing_product_needs_review():
    submission = _submission(requested_product=None)

    result = normalize_submission(submission)

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.design_job is None
    assert "MISSING_PRODUCT" in _reason_codes(result)


def test_missing_brief_needs_review():
    submission = _submission(brief=None)

    result = normalize_submission(submission)

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.design_job is None
    assert "MISSING_BRIEF" in _reason_codes(result)


def test_missing_primary_asset_needs_review():
    submission = _submission(assets=[_asset(AssetRole.BACKGROUND)])

    result = normalize_submission(submission)

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.design_job is None
    assert "MISSING_PRIMARY_ASSET" in _reason_codes(result)


def test_normalization_does_not_create_production_requirements():
    forbidden = {
        "dimensions",
        "min_dpi",
        "bleed_mm",
        "color",
        "accepted_formats",
        "required_asset_roles",
    }
    assert forbidden.isdisjoint(set(DesignJob.model_fields.keys()))


@pytest.mark.parametrize(
    "requested_product,expected",
    [
        ("poster print", ProductType.POSTER),
        ("business card", ProductType.BUSINESS_CARD),
        ("flyer", ProductType.FLYER),
    ],
)
def test_product_aliases_normalize_deterministically(requested_product, expected):
    submission = _submission(requested_product=requested_product)

    result = normalize_submission(submission)

    assert result.status == ResultStatus.PASSED
    assert result.design_job is not None
    assert result.design_job.product_type == expected
