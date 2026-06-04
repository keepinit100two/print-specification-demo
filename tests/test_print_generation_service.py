"""
Failing tests for the future AI Generation Service (fake mode only).

Expected future module:   app/services/print_generation.py
Expected future function:
    generate_candidates(
        generation_request: GenerationRequest,
    ) -> tuple[list[GeneratedCandidate], list[ModelInvocationRecord]]

These tests are expected to FAIL initially because the module does not exist
yet. They are not skipped or xfailed — they define the contract the service
must satisfy.

Architecture:
  - Fake mode: deterministic local generator (default for unit tests).
  - OpenAI mode: real provider, env/config only — NOT exercised here.
  - No network, no OPENAI_API_KEY, no OpenAI client imports required.

Schema note: GeneratedCandidate uses `uri` (not `output_uri`) and has no
top-level `job_id` / `spec_id` fields. Tests assert `uri` for artifact
location and require job/spec traceability in `metadata` alongside
`request_id` linkage to the GenerationRequest.
"""

import os

import pytest

from app.domain.print_schemas import (
    AdaptationPlan,
    AssetRole,
    DesignJob,
    GenerationRequest,
    InvocationStatus,
    ProductType,
    ResultStatus,
    TransformationStep,
    TransformationType,
)
from app.services.print_generation import generate_candidates
from app.services.print_prompt_construction import build_generation_request
from app.services.print_specification import resolve_specification


# ---------------------------------------------------------------------------
# Deterministic builders
# ---------------------------------------------------------------------------


def _design_job(brief="A bold outdoor banner advertising a summer sale."):
    submission_id = "sub-gen-001"
    return DesignJob(
        job_id=f"job-{submission_id}",
        submission_id=submission_id,
        product_type=ProductType.BANNER,
        title="Summer sale",
        normalized_brief=brief,
        requested_quantity=1,
    )


def _generation_request(candidate_count=3):
    design_job = _design_job()
    spec = resolve_specification(design_job)
    plan = AdaptationPlan(
        plan_id=f"plan-{design_job.job_id}",
        spec_id=spec.spec_id,
        job_id=design_job.job_id,
        status=ResultStatus.PASSED,
        requires_generation=True,
        steps=[
            TransformationStep(
                transformation=TransformationType.UPSCALE,
                target_asset_role=AssetRole.PRIMARY,
                parameters={"requirement": "min_dpi"},
                reason="Resolve min_dpi: upscale to meet required DPI",
            ),
        ],
    )
    request = build_generation_request(design_job, spec, plan)
    return request.model_copy(update={"candidate_count": candidate_count})


# ---------------------------------------------------------------------------
# 1. Fake mode returns candidates and a single invocation record
# ---------------------------------------------------------------------------


def test_fake_mode_returns_candidates_and_invocation():
    request = _generation_request(candidate_count=3)

    candidates, invocations = generate_candidates(request)

    assert isinstance(candidates, list)
    assert isinstance(invocations, list)
    assert len(candidates) == request.candidate_count
    assert len(invocations) == 1
    assert invocations[0].request_id == request.request_id
    assert set(invocations[0].generated_candidate_ids) == {
        c.candidate_id for c in candidates
    }


# ---------------------------------------------------------------------------
# 2. Candidates reference request and carry deterministic properties
# ---------------------------------------------------------------------------


def test_candidates_reference_request_and_spec():
    request = _generation_request(candidate_count=2)

    candidates, _ = generate_candidates(request)

    for candidate in candidates:
        assert candidate.request_id == request.request_id
        assert candidate.candidate_id
        assert candidate.uri
        assert candidate.uri.startswith("artifact://generated/")
        assert candidate.properties is not None
        assert candidate.properties.width_px == request.output_width_px
        assert candidate.properties.height_px == request.output_height_px
        assert candidate.properties.dpi == request.target_dpi
        assert candidate.properties.file_format == request.output_format
        # Traceability: job/spec carried in metadata when not on schema fields.
        assert candidate.metadata.get("job_id") == request.job_id
        assert candidate.metadata.get("spec_id") == request.spec_id


# ---------------------------------------------------------------------------
# 3. Invocation record captures fake observability
# ---------------------------------------------------------------------------


def test_invocation_record_fake_observability():
    request = _generation_request(candidate_count=2)

    candidates, invocations = generate_candidates(request)
    invocation = invocations[0]

    assert invocation.provider == "local-fake"
    assert invocation.model_name == "deterministic-mvp-generator"
    assert invocation.status == InvocationStatus.SUCCEEDED
    assert invocation.started_at is not None
    assert invocation.completed_at is not None
    assert invocation.latency_ms is not None
    assert invocation.latency_ms >= 0
    assert invocation.retry_count == 0
    assert set(invocation.generated_candidate_ids) == {
        c.candidate_id for c in candidates
    }


# ---------------------------------------------------------------------------
# 4. Fake generation is deterministic for the same request
# ---------------------------------------------------------------------------


def test_fake_generation_is_deterministic():
    request = _generation_request(candidate_count=3)

    candidates_a, _ = generate_candidates(request)
    candidates_b, _ = generate_candidates(request)

    ids_a = [c.candidate_id for c in candidates_a]
    ids_b = [c.candidate_id for c in candidates_b]
    uris_a = [c.uri for c in candidates_a]
    uris_b = [c.uri for c in candidates_b]

    assert ids_a == ids_b
    assert uris_a == uris_b
    for a, b in zip(candidates_a, candidates_b):
        assert a.properties.width_px == b.properties.width_px
        assert a.properties.height_px == b.properties.height_px
        assert a.properties.dpi == b.properties.dpi
        assert a.properties.file_format == b.properties.file_format


# ---------------------------------------------------------------------------
# 5. Invalid candidate count fails safely
# ---------------------------------------------------------------------------


def test_invalid_candidate_count_raises_value_error():
    request = _generation_request(candidate_count=3)
    invalid = request.model_copy(update={"candidate_count": 0})

    with pytest.raises(ValueError):
        generate_candidates(invalid)


# ---------------------------------------------------------------------------
# 6. Generation does not validate or approve outputs
# ---------------------------------------------------------------------------


def test_generation_does_not_validate_or_approve():
    request = _generation_request(candidate_count=1)

    candidates, invocations = generate_candidates(request)

    for candidate in candidates:
        assert not hasattr(candidate, "approval_status")
        assert not hasattr(candidate, "validation_result")
        assert not hasattr(candidate, "approval_decision")
    assert not any(hasattr(i, "approval_decision") for i in invocations)


# ---------------------------------------------------------------------------
# 7. OpenAI mode is not required for unit tests (fake default)
# ---------------------------------------------------------------------------


def test_fake_mode_default_no_openai_required(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PRINT_GENERATION_MODE", raising=False)

    request = _generation_request(candidate_count=1)
    candidates, invocations = generate_candidates(request)

    assert len(candidates) == 1
    assert invocations[0].provider == "local-fake"
    assert "openai" not in os.environ.get("PRINT_GENERATION_MODE", "fake").lower()
