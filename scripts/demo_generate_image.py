"""
Manual smoke test: build a sample GenerationRequest and call generate_candidates().

Verifies OpenAI image generation (when PRINT_GENERATION_MODE=openai) and artifact
saving before orchestrator wiring. Not a pytest test — run from repo root:

    python scripts/demo_generate_image.py

Requires OPENAI_API_KEY when PRINT_GENERATION_MODE=openai.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Run from repository root so `app` imports resolve.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.domain.print_schemas import (
    AdaptationPlan,
    AssetRole,
    DesignJob,
    ProductType,
    ResultStatus,
    TransformationStep,
    TransformationType,
)
from app.services.print_generation import generate_candidates
from app.services.print_prompt_construction import build_generation_request
from app.services.print_specification import resolve_specification


def _build_demo_design_job() -> DesignJob:
    return DesignJob(
        job_id="job-demo-openai",
        submission_id="sub-demo-openai",
        product_type=ProductType.BANNER,
        title="Weekend Detail Promo",
        normalized_brief=(
            "A clean professional banner for a weekend car detailing promotion, "
            "bold readable text, high contrast, premium local business feel."
        ),
        requested_quantity=1,
    )


def _build_demo_adaptation_plan(design_job: DesignJob, spec) -> AdaptationPlan:
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
                parameters={"requirement": "print_ready"},
                reason=(
                    "Create a print-ready high-resolution version while preserving "
                    "the customer's original intent."
                ),
            ),
        ],
    )


def main() -> None:
    if load_dotenv is not None:
        load_dotenv()
    print(f"dotenv_loaded={load_dotenv is not None}")

    mode = os.environ.get("PRINT_GENERATION_MODE", "fake")
    print(f"PRINT_GENERATION_MODE={mode}")

    design_job = _build_demo_design_job()
    spec = resolve_specification(design_job)
    plan = _build_demo_adaptation_plan(design_job, spec)
    generation_request = build_generation_request(design_job, spec, plan)
    generation_request = generation_request.model_copy(update={"output_format": "png"})

    print(f"request_id={generation_request.request_id}")
    print(f"candidate_count={generation_request.candidate_count}")

    candidates, invocations = generate_candidates(generation_request)

    if not candidates:
        print("WARNING: generate_candidates returned zero candidates.")

    for candidate in candidates:
        artifact_saved = candidate.metadata.get("artifact_saved")
        print(f"candidate_id={candidate.candidate_id}")
        print(f"  uri={candidate.uri}")
        if artifact_saved is not None:
            print(f"  artifact_saved={artifact_saved}")
        if candidate.metadata.get("artifact_path"):
            print(f"  artifact_path={candidate.metadata['artifact_path']}")
        if candidate.metadata.get("artifact_error"):
            print(f"  artifact_error={candidate.metadata['artifact_error']}")

    for invocation in invocations:
        print(f"invocation provider={invocation.provider}")
        print(f"invocation model_name={invocation.model_name}")
        print(f"invocation status={invocation.status}")
        print(f"invocation latency_ms={invocation.latency_ms}")
        if invocation.error_code:
            print(f"invocation error_code={invocation.error_code}")
        if invocation.error_message:
            print(f"invocation error_message={invocation.error_message}")


if __name__ == "__main__":
    main()
