import uuid

import pytest

from app.domain.print_schemas import (
    DesignJob,
    PrintSpecification,
    ProductType,
)

# Expected to FAIL until app/services/print_specification.py exists. These tests
# define the contract the future implementation must satisfy. They are
# intentionally not skipped/xfailed.
from app.services import print_specification
from app.services.print_specification import resolve_specification


def _design_job(product_type: ProductType = ProductType.BANNER) -> DesignJob:
    return DesignJob(
        job_id=f"job-{uuid.uuid4()}",
        submission_id=str(uuid.uuid4()),
        product_type=product_type,
        title="Summer sale",
        normalized_brief="A bold outdoor banner advertising a summer sale.",
        requested_quantity=1,
    )


def test_banner_design_job_resolves_to_specification():
    job = _design_job(ProductType.BANNER)

    spec = resolve_specification(job)

    assert isinstance(spec, PrintSpecification)
    assert spec.job_id == job.job_id
    assert spec.product_type == ProductType.BANNER
    assert spec.dimensions.width_mm > 0
    assert spec.dimensions.height_mm > 0
    assert spec.dimensions.min_dpi >= 300
    assert spec.color.color_mode
    assert spec.accepted_formats
    assert spec.config_source


def test_poster_resolves_deterministically():
    job = _design_job(ProductType.POSTER)

    spec_a = resolve_specification(job)
    spec_b = resolve_specification(job)

    assert spec_a.spec_id == spec_b.spec_id
    assert spec_a.dimensions.model_dump() == spec_b.dimensions.model_dump()
    assert spec_a.color.model_dump() == spec_b.color.model_dump()
    assert spec_a.accepted_formats == spec_b.accepted_formats


def test_unavailable_product_rules_raise_value_error(monkeypatch):
    # Simulate a config/rules miss by emptying the internal preset rules table.
    monkeypatch.setattr(print_specification, "_PRODUCT_RULES", {}, raising=False)

    job = _design_job(ProductType.BANNER)

    with pytest.raises(ValueError):
        resolve_specification(job)


def test_specification_is_downstream_of_design_job():
    job = _design_job(ProductType.BANNER)

    spec = resolve_specification(job)

    # Resolution needs only the DesignJob — no RawSubmission, no NormalizationResult.
    assert spec.job_id == job.job_id


def test_specification_contains_production_requirements():
    job = _design_job(ProductType.BANNER)

    spec = resolve_specification(job)

    dim_fields = set(type(spec.dimensions).model_fields.keys())
    for field in ("width_mm", "height_mm", "bleed_mm", "safe_margin_mm", "min_dpi"):
        assert field in dim_fields

    assert "color_mode" in type(spec.color).model_fields
    assert hasattr(spec, "accepted_formats")
    assert hasattr(spec, "required_asset_roles")
