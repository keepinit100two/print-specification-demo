"""
Print Prompt Construction Service.

Turns an AdaptationPlan (plus its DesignJob and PrintSpecification) into a
strict, model-ready GenerationRequest. This is the PROMPT CONSTRUCTION stage.

It is deterministic and pure: it builds a structured prompt and resolves the
exact output constraints (pixel dimensions, color mode, format) from the
specification. It does NOT call any model, produce GeneratedCandidate objects,
create ModelInvocationRecord entries, validate outputs, perform file I/O, or
read external configuration.
"""

import math
from typing import List

from app.domain.print_schemas import (
    AdaptationPlan,
    DesignJob,
    GenerationRequest,
    PrintSpecification,
)

# Fixed, deterministic number of candidates to request per generation.
_CANDIDATE_COUNT = 3

# Safe default constraint applied to every request so generation preserves the
# customer's intent and brand-critical elements.
_DEFAULT_NEGATIVE_PROMPT = (
    "Do not alter logos, text, brand marks, faces, or critical layout elements "
    "unless explicitly requested."
)


def _required_px(length_mm: float, min_dpi: int) -> int:
    """Pixels required to satisfy `length_mm` at `min_dpi` (mm -> inches -> px)."""
    return math.ceil(length_mm / 25.4 * min_dpi)


def _build_prompt(
    design_job: DesignJob,
    specification: PrintSpecification,
    adaptation_plan: AdaptationPlan,
) -> str:
    """Compose a deterministic, structured generation prompt."""
    dims = specification.dimensions
    lines: List[str] = [
        f"Produce a print-ready {specification.product_type.value} design.",
        f"Design brief: {design_job.normalized_brief}",
        (
            "Preserve the customer's original intent and brand identity; do not "
            "introduce content that contradicts the brief."
        ),
        (
            f"Target output: {dims.width_mm}mm x {dims.height_mm}mm at "
            f"{dims.min_dpi} DPI, color mode {specification.color.color_mode}."
        ),
    ]

    step_reasons = [step.reason for step in adaptation_plan.steps if step.reason]
    if step_reasons:
        lines.append("Apply the following adaptations while preserving intent:")
        lines.extend(f"- {reason}" for reason in step_reasons)

    return "\n".join(lines)


def build_generation_request(
    design_job: DesignJob,
    specification: PrintSpecification,
    adaptation_plan: AdaptationPlan,
) -> GenerationRequest:
    """Build a strict, model-ready GenerationRequest from an AdaptationPlan."""
    if adaptation_plan is None:
        raise ValueError("adaptation_plan is required to build a generation request")

    if not adaptation_plan.requires_generation:
        raise ValueError(
            "adaptation_plan does not require generation; no GenerationRequest is needed"
        )

    if not specification.accepted_formats:
        raise ValueError(
            "specification has no accepted_formats; cannot resolve an output format"
        )

    dims = specification.dimensions
    output_width_px = _required_px(dims.width_mm, dims.min_dpi)
    output_height_px = _required_px(dims.height_mm, dims.min_dpi)

    return GenerationRequest(
        request_id=f"genreq-{adaptation_plan.plan_id}",
        spec_id=specification.spec_id,
        job_id=design_job.job_id,
        plan_id=adaptation_plan.plan_id,
        prompt=_build_prompt(design_job, specification, adaptation_plan),
        negative_prompt=_DEFAULT_NEGATIVE_PROMPT,
        output_width_px=output_width_px,
        output_height_px=output_height_px,
        target_dpi=dims.min_dpi,
        color_mode=specification.color.color_mode,
        output_format=specification.accepted_formats[0],
        candidate_count=_CANDIDATE_COUNT,
    )
