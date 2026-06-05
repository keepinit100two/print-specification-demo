"""
Failing tests for the future Deterministic Transform Service.

Expected future module:   app/services/print_deterministic_transform.py
Expected future function:
    execute_deterministic_transforms(
        design_job: DesignJob,
        specification: PrintSpecification,
        adaptation_plan: AdaptationPlan,
    ) -> DeterministicTransformResult

These tests are expected to FAIL initially because the module does not exist
yet. They are not skipped or xfailed — they define the contract the service
must satisfy.

Supported deterministic transforms (MVP):
  RESIZE, DPI_ADJUSTMENT, COLOR_PROFILE_CONVERSION, PAD, CROP

Unsupported transforms (e.g. BACKGROUND_REMOVAL) should route to NEEDS_REVIEW.
This stage owns deterministic execution only — no AI, no GeneratedCandidate.
"""

import uuid

import pytest

from app.domain.print_schemas import (
    AdaptationPlan,
    AssetRole,
    DesignJob,
    DeterministicTransformResult,
    DeterministicTransformStatus,
    ProductType,
    ResultStatus,
    TransformationStep,
    TransformationType,
)
from app.services.print_deterministic_transform import execute_deterministic_transforms
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


def _adaptation_plan(design_job, spec, steps):
    return AdaptationPlan(
        plan_id=f"plan-{design_job.job_id}",
        spec_id=spec.spec_id,
        job_id=design_job.job_id,
        status=ResultStatus.PASSED,
        requires_generation=False,
        steps=steps,
    )


def _step(transformation, reason="Deterministic remediation"):
    return TransformationStep(
        transformation=transformation,
        target_asset_role=AssetRole.PRIMARY,
        parameters={"step_id": "step-001"},
        reason=reason,
    )


# ---------------------------------------------------------------------------
# 1. Empty plan succeeds with skipped result
# ---------------------------------------------------------------------------


def test_empty_plan_succeeds_skipped():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = _adaptation_plan(design_job, spec, steps=[])

    result = execute_deterministic_transforms(design_job, spec, plan)

    assert isinstance(result, DeterministicTransformResult)
    assert result.status in (ResultStatus.SKIPPED, ResultStatus.PASSED)
    assert result.transformed_assets == []
    assert result.next_steps


# ---------------------------------------------------------------------------
# 2. RESIZE transform produces a transformed asset
# ---------------------------------------------------------------------------


def test_resize_transform_produces_transformed_asset():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    resize_step = _step(TransformationType.RESIZE, reason="Resolve width_px")
    plan = _adaptation_plan(design_job, spec, steps=[resize_step])

    result = execute_deterministic_transforms(design_job, spec, plan)

    assert result.status == ResultStatus.PASSED
    assert len(result.transformed_assets) == 1
    asset = result.transformed_assets[0]
    assert asset.job_id == design_job.job_id
    assert asset.spec_id == spec.spec_id
    assert asset.plan_id == plan.plan_id
    assert any(
        s.transformation == TransformationType.RESIZE
        for s in asset.transformations_applied
    )
    assert asset.status == DeterministicTransformStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# 3. DPI_ADJUSTMENT transform produces a transformed asset
# ---------------------------------------------------------------------------


def test_dpi_adjustment_transform_produces_transformed_asset():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    dpi_step = _step(TransformationType.DPI_ADJUSTMENT, reason="Resolve min_dpi")
    plan = _adaptation_plan(design_job, spec, steps=[dpi_step])

    result = execute_deterministic_transforms(design_job, spec, plan)

    assert result.status == ResultStatus.PASSED
    assert result.transformed_assets
    assert any(
        s.transformation == TransformationType.DPI_ADJUSTMENT
        for asset in result.transformed_assets
        for s in asset.transformations_applied
    )


# ---------------------------------------------------------------------------
# 4. Unsupported transform routes to review
# ---------------------------------------------------------------------------


def test_unsupported_transform_routes_to_review():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    unsupported_step = _step(
        TransformationType.BACKGROUND_REMOVAL,
        reason="Remove background for print",
    )
    plan = _adaptation_plan(design_job, spec, steps=[unsupported_step])

    result = execute_deterministic_transforms(design_job, spec, plan)

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.reasons


# ---------------------------------------------------------------------------
# 5. Missing adaptation plan raises ValueError
# ---------------------------------------------------------------------------


def test_missing_adaptation_plan_raises_value_error():
    design_job = _design_job()
    spec = resolve_specification(design_job)

    with pytest.raises(ValueError):
        execute_deterministic_transforms(design_job, spec, None)


# ---------------------------------------------------------------------------
# 6. Deterministic path must not invoke AI
# ---------------------------------------------------------------------------


def test_deterministic_path_does_not_invoke_ai():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = _adaptation_plan(
        design_job, spec, steps=[_step(TransformationType.RESIZE)]
    )

    result = execute_deterministic_transforms(design_job, spec, plan)

    assert not hasattr(result, "generated_candidates")
    assert not hasattr(result, "candidates")
    assert not hasattr(result, "model_invocations")
    assert not hasattr(result, "generation_request")


# ---------------------------------------------------------------------------
# 7. Correct job/spec/plan references preserved
# ---------------------------------------------------------------------------


def test_result_references_job_spec_and_plan():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = _adaptation_plan(
        design_job, spec, steps=[_step(TransformationType.PAD)]
    )

    result = execute_deterministic_transforms(design_job, spec, plan)

    assert result.job_id == design_job.job_id
    assert result.spec_id == spec.spec_id
    assert result.plan_id == plan.plan_id
