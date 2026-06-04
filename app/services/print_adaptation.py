"""
Print Adaptation Planning Service.

Turns a ComplianceResult into a deterministic AdaptationPlan: an ordered, reviewable
list of transformation intents that would bring submitted assets into compliance.
This is the ADAPTATION stage. It is a *plan only* and performs no side effects.

It must NOT:
  - generate images,
  - build a GenerationRequest,
  - call any model,
  - re-measure compliance,
  - inspect image files, or
  - read configuration.

Everything here is deterministic and pure: it consumes the already-measured
ComplianceResult and maps each non-compliant finding to a TransformationStep.
"""

from typing import Any, Dict, List, Optional

from app.domain.print_schemas import (
    AdaptationPlan,
    AssetRole,
    ComplianceFinding,
    ComplianceResult,
    DesignJob,
    PrintSpecification,
    ResultStatus,
    StageIssue,
    TransformationStep,
    TransformationType,
)

# Transformations that may require image generation/synthesis (vs. pure
# deterministic pixel ops). Used to set AdaptationPlan.requires_generation.
_GENERATION_TRANSFORMS = {TransformationType.UPSCALE}


def _step_for_finding(
    finding: ComplianceFinding,
    specification: PrintSpecification,
    step_id: str,
) -> TransformationStep:
    """Map a single non-compliant finding to a deterministic transformation step."""
    requirement = finding.requirement
    parameters: Dict[str, Any] = {
        "step_id": step_id,
        "requirement": requirement,
        "expected": finding.expected,
        "actual": finding.actual,
    }

    if requirement == "min_dpi":
        transformation = TransformationType.UPSCALE
        parameters["target_dpi"] = specification.dimensions.min_dpi
        reason = (
            f"Resolve min_dpi: upscale to meet required {specification.dimensions.min_dpi} DPI"
        )
    elif requirement in ("width_px", "height_px"):
        transformation = TransformationType.RESIZE
        parameters["target_px"] = finding.expected
        reason = f"Resolve {requirement}: resize to meet required pixel dimensions"
    elif requirement == "file_format":
        # No dedicated file-format enum member exists; encode the remediation
        # explicitly in reason + parameters using an existing transformation type.
        transformation = TransformationType.COLOR_PROFILE_CONVERSION
        target_format = (
            specification.accepted_formats[0]
            if specification.accepted_formats
            else None
        )
        parameters["output_format_target"] = target_format
        reason = (
            f"Resolve file_format: convert/export to accepted format '{target_format}'"
        )
    else:
        # Generic deterministic remediation for any other requirement.
        transformation = TransformationType.RESIZE
        reason = f"Resolve {requirement}: apply deterministic remediation"

    return TransformationStep(
        transformation=transformation,
        target_asset_role=AssetRole.PRIMARY,
        parameters=parameters,
        reason=reason,
    )


def create_adaptation_plan(
    design_job: DesignJob,
    specification: PrintSpecification,
    compliance_result: ComplianceResult,
) -> AdaptationPlan:
    """Build a deterministic AdaptationPlan from a ComplianceResult."""
    if compliance_result is None:
        raise ValueError("compliance_result is required to create an adaptation plan")

    plan_id = f"plan-{design_job.job_id}"

    # Print-ready -> no adaptation needed.
    if compliance_result.is_print_ready and compliance_result.status == ResultStatus.PASSED:
        return AdaptationPlan(
            plan_id=plan_id,
            spec_id=specification.spec_id,
            job_id=design_job.job_id,
            status=ResultStatus.SKIPPED,
            requires_generation=False,
            steps=[],
            reasons=[],
            next_steps="No adaptation required; assets are print-ready.",
        )

    non_compliant = [f for f in compliance_result.findings if not f.compliant]

    steps: List[TransformationStep] = []
    for index, finding in enumerate(non_compliant, start=1):
        step_id = f"step-{index:03d}"
        steps.append(_step_for_finding(finding, specification, step_id))

    requires_generation = any(
        step.transformation in _GENERATION_TRANSFORMS for step in steps
    )

    reasons: List[StageIssue] = [
        StageIssue(
            code="ADAPTATION_REQUIRED",
            message=f"{len(steps)} transformation step(s) planned to reach compliance",
            field="compliance_result",
        )
    ]

    next_steps: Optional[str]
    if steps:
        next_steps = (
            f"Apply {len(steps)} planned transformation(s); "
            + ("generation required." if requires_generation else "deterministic transforms only.")
        )
    else:
        next_steps = "No transformations could be derived; route for review."

    return AdaptationPlan(
        plan_id=plan_id,
        spec_id=specification.spec_id,
        job_id=design_job.job_id,
        status=ResultStatus.PASSED,
        requires_generation=requires_generation,
        steps=steps,
        reasons=reasons,
        next_steps=next_steps,
    )
