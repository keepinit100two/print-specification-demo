"""
Deterministic Transform Service.

Executes supported AdaptationPlan steps without AI, image libraries, or file I/O.
Produces TransformedAsset outputs and a DeterministicTransformResult — separate
from the AI generation path (GeneratedCandidate / ModelInvocationRecord).
"""

from typing import List, Set

from app.domain.print_schemas import (
    AdaptationPlan,
    DesignJob,
    DeterministicTransformResult,
    DeterministicTransformStatus,
    PrintSpecification,
    ResultStatus,
    StageIssue,
    TransformationStep,
    TransformationType,
    TransformedAsset,
)

_SUPPORTED_TRANSFORMS: Set[TransformationType] = {
    TransformationType.RESIZE,
    TransformationType.DPI_ADJUSTMENT,
    TransformationType.PAD,
    TransformationType.CROP,
    TransformationType.COLOR_PROFILE_CONVERSION,
}

# Unsupported without review routing (UPSCALE, BACKGROUND_REMOVAL, RECOLOR,
# BLEED_EXTENSION, and any other non-supported type) -> NEEDS_REVIEW.


def _transformed_asset(
    *,
    adaptation_plan: AdaptationPlan,
    specification: PrintSpecification,
    design_job: DesignJob,
    step: TransformationStep,
    index: int,
) -> TransformedAsset:
    """Build a placeholder TransformedAsset for a supported deterministic step."""
    asset_suffix = f"{adaptation_plan.plan_id}-{index:03d}"
    return TransformedAsset(
        transformed_asset_id=f"transformed-{asset_suffix}",
        job_id=design_job.job_id,
        spec_id=specification.spec_id,
        plan_id=adaptation_plan.plan_id,
        uri=f"artifact://transformed/{asset_suffix}",
        transformations_applied=[step],
        status=DeterministicTransformStatus.SUCCEEDED,
        metadata={
            "transformation": step.transformation.value,
            "placeholder": True,
        },
    )


def execute_deterministic_transforms(
    design_job: DesignJob,
    specification: PrintSpecification,
    adaptation_plan: AdaptationPlan,
) -> DeterministicTransformResult:
    """Execute deterministic adaptation steps and return transform outputs."""
    if adaptation_plan is None:
        raise ValueError("adaptation_plan is required to execute deterministic transforms")

    result_id = f"dtransform-{adaptation_plan.plan_id}"

    if not adaptation_plan.steps:
        return DeterministicTransformResult(
            result_id=result_id,
            job_id=design_job.job_id,
            spec_id=specification.spec_id,
            plan_id=adaptation_plan.plan_id,
            status=ResultStatus.SKIPPED,
            transformed_assets=[],
            next_steps="No deterministic transforms required; proceed without transform execution.",
        )

    unsupported_steps: List[TransformationStep] = [
        step
        for step in adaptation_plan.steps
        if step.transformation not in _SUPPORTED_TRANSFORMS
    ]
    if unsupported_steps:
        reasons = [
            StageIssue(
                code="UNSUPPORTED_TRANSFORM",
                message=(
                    f"Deterministic execution does not support "
                    f"{step.transformation.value}; human review or future implementation required."
                ),
                field="adaptation_plan",
            )
            for step in unsupported_steps
        ]
        return DeterministicTransformResult(
            result_id=result_id,
            job_id=design_job.job_id,
            spec_id=specification.spec_id,
            plan_id=adaptation_plan.plan_id,
            status=ResultStatus.NEEDS_REVIEW,
            transformed_assets=[],
            reasons=reasons,
            next_steps=(
                "Review unsupported deterministic transform steps before proceeding."
            ),
        )

    transformed_assets = [
        _transformed_asset(
            adaptation_plan=adaptation_plan,
            specification=specification,
            design_job=design_job,
            step=step,
            index=index,
        )
        for index, step in enumerate(adaptation_plan.steps, start=1)
    ]

    return DeterministicTransformResult(
        result_id=result_id,
        job_id=design_job.job_id,
        spec_id=specification.spec_id,
        plan_id=adaptation_plan.plan_id,
        status=ResultStatus.PASSED,
        transformed_assets=transformed_assets,
        next_steps=(
            f"Applied {len(transformed_assets)} deterministic transform(s); "
            "proceed to validation or downstream routing."
        ),
    )
