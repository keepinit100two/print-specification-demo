"""
Failing tests for the future Prompt Construction Service.

Expected future module:   app/services/print_prompt_construction.py
Expected future function:
    build_generation_request(
        design_job: DesignJob,
        specification: PrintSpecification,
        adaptation_plan: AdaptationPlan,
    ) -> GenerationRequest

These tests are expected to FAIL initially because the module does not exist
yet. They are not skipped or xfailed — they define the contract the service
must satisfy.

Schema note: the GenerationRequest contract in app/domain/print_schemas.py uses
`output_width_px` / `output_height_px` / `candidate_count` (not the
`target_width_px` / `target_height_px` / `output_count` names from the task
brief) and has no "skipped" status field. The tests use the real schema fields
and prefer ValueError for the no-generation case, per the brief's guidance.
"""

import math

import pytest

from app.domain.print_schemas import (
    AdaptationPlan,
    AssetRole,
    DesignJob,
    GenerationRequest,
    ProductType,
    ResultStatus,
    TransformationStep,
    TransformationType,
)
from app.services.print_prompt_construction import build_generation_request
from app.services.print_specification import resolve_specification


# ---------------------------------------------------------------------------
# Deterministic builders
# ---------------------------------------------------------------------------


def _design_job(brief="A bold outdoor banner advertising a summer sale."):
    submission_id = "sub-001"
    return DesignJob(
        job_id=f"job-{submission_id}",
        submission_id=submission_id,
        product_type=ProductType.BANNER,
        title="Summer sale",
        normalized_brief=brief,
        requested_quantity=1,
    )


def _generation_plan(design_job, spec):
    """An AdaptationPlan that requires generation (e.g. an upscale step)."""
    return AdaptationPlan(
        plan_id=f"plan-{design_job.job_id}",
        spec_id=spec.spec_id,
        job_id=design_job.job_id,
        status=ResultStatus.PASSED,
        requires_generation=True,
        steps=[
            TransformationStep(
                transformation=TransformationType.UPSCALE,
                target_asset_role=AssetRole.PRIMARY,
                parameters={"requirement": "min_dpi", "target_dpi": spec.dimensions.min_dpi},
                reason="Resolve min_dpi: upscale to meet required DPI",
            ),
        ],
    )


def _noop_plan(design_job, spec):
    """An AdaptationPlan that needs no generation."""
    return AdaptationPlan(
        plan_id=f"plan-{design_job.job_id}",
        spec_id=spec.spec_id,
        job_id=design_job.job_id,
        status=ResultStatus.SKIPPED,
        requires_generation=False,
        steps=[],
    )


# ---------------------------------------------------------------------------
# 1. Generation-requiring plan produces a model-ready GenerationRequest
# ---------------------------------------------------------------------------


def test_generation_plan_creates_generation_request():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = _generation_plan(design_job, spec)

    request = build_generation_request(design_job, spec, plan)

    assert isinstance(request, GenerationRequest)
    assert request.job_id == design_job.job_id
    assert request.spec_id == spec.spec_id
    assert request.prompt and request.prompt.strip()
    # negative_prompt is populated or safely defaulted (None or a real string).
    assert request.negative_prompt is None or request.negative_prompt.strip()
    assert request.candidate_count > 0
    assert request.output_width_px > 0
    assert request.output_height_px > 0
    assert request.output_format in spec.accepted_formats
    # References the adaptation plan that triggered generation.
    assert request.plan_id == plan.plan_id


# ---------------------------------------------------------------------------
# 2. A no-generation plan is rejected (no skipped status on GenerationRequest)
# ---------------------------------------------------------------------------


def test_no_generation_plan_raises_value_error():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = _noop_plan(design_job, spec)

    with pytest.raises(ValueError):
        build_generation_request(design_job, spec, plan)


# ---------------------------------------------------------------------------
# 3. Prompt carries preservation constraints + transformation reasons
# ---------------------------------------------------------------------------


def test_prompt_includes_preservation_constraints():
    brief = "A bold outdoor banner advertising a summer sale."
    design_job = _design_job(brief=brief)
    spec = resolve_specification(design_job)
    plan = _generation_plan(design_job, spec)

    request = build_generation_request(design_job, spec, plan)

    assert design_job.normalized_brief in request.prompt
    # References preserving the customer's original intent.
    assert "intent" in request.prompt.lower()
    # References the transformation reason text from the plan.
    assert any(
        step.reason and step.reason in request.prompt for step in plan.steps
    )


# ---------------------------------------------------------------------------
# 4. Target pixel dimensions derive deterministically from the spec
# ---------------------------------------------------------------------------


def test_target_pixels_derive_from_specification():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = _generation_plan(design_job, spec)

    request = build_generation_request(design_job, spec, plan)

    expected_w = math.ceil(
        spec.dimensions.width_mm / 25.4 * spec.dimensions.min_dpi
    )
    expected_h = math.ceil(
        spec.dimensions.height_mm / 25.4 * spec.dimensions.min_dpi
    )
    assert request.output_width_px == expected_w
    assert request.output_height_px == expected_h


# ---------------------------------------------------------------------------
# 5. Prompt construction does not call AI or produce candidates
# ---------------------------------------------------------------------------


def test_prompt_construction_produces_no_candidates():
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = _generation_plan(design_job, spec)

    request = build_generation_request(design_job, spec, plan)

    # The output is a request only — it carries no model outputs or invocations.
    assert not hasattr(request, "generated_candidates")
    assert not hasattr(request, "candidates")
    assert not hasattr(request, "model_invocations")


# ---------------------------------------------------------------------------
# 6. Missing adaptation plan is rejected
# ---------------------------------------------------------------------------


def test_missing_adaptation_plan_raises_value_error():
    design_job = _design_job()
    spec = resolve_specification(design_job)

    with pytest.raises(ValueError):
        build_generation_request(design_job, spec, None)
