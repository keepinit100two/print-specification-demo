"""
Print AI Generation Service (actuator).

Executes a GenerationRequest and returns GeneratedCandidate outputs plus a
ModelInvocationRecord for observability. This is the GENERATION stage.

Modes (via PRINT_GENERATION_MODE env var):
  - fake (default): deterministic local generator for tests and demos.
  - openai: OpenAI Images API via app.services.openai_image_client.

This service must NOT:
  - validate outputs,
  - approve outputs,
  - create ApprovalDecision,
  - modify files on disk, or
  - decide workflow control flow.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.domain.print_schemas import (
    GeneratedCandidate,
    GenerationRequest,
    ImageProperties,
    InvocationStatus,
    ModelInvocationRecord,
)
from app.services.openai_image_client import generate_openai_images

_FAKE_PROVIDER = "local-fake"
_FAKE_MODEL = "deterministic-mvp-generator"
_DEFAULT_MODE = "fake"
_DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1"
_OPENAI_MVP_SIZE = "1024x1024"


def _generation_mode() -> str:
    """Resolve generation mode from env; default is fake."""
    return os.environ.get("PRINT_GENERATION_MODE", _DEFAULT_MODE).strip().lower()


def _candidate_id(request_id: str, index: int) -> str:
    return f"candidate-{request_id}-{index:03d}"


def _candidate_uri(candidate_id: str, output_format: str) -> str:
    return f"artifact://generated/{candidate_id}.{output_format}"


def _latency_ms(started_at: datetime, completed_at: datetime) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


def _validate_generation_request(generation_request: GenerationRequest) -> int:
    if generation_request is None:
        raise ValueError("generation_request is required")
    count = generation_request.candidate_count
    if count <= 0:
        raise ValueError("candidate_count must be greater than zero")
    return count


def _openai_image_size(width_px: int, height_px: int) -> str:
    """
    Map requested pixel dimensions to a supported OpenAI Images API size.

    Exact print dimensions are often unsupported; use landscape/portrait/square
    presets for MVP, otherwise fall back to 1024x1024.
    """
    if width_px <= 0 or height_px <= 0:
        return _OPENAI_MVP_SIZE
    ratio = width_px / height_px
    if ratio > 1.2:
        return "1792x1024"
    if ratio < 0.8:
        return "1024x1792"
    return _OPENAI_MVP_SIZE


def _openai_model_name(model: Optional[str] = None) -> str:
    """Resolve model name for invocation records when the client is not called."""
    if model:
        return model
    return (
        os.environ.get("PRINT_OPENAI_IMAGE_MODEL", _DEFAULT_OPENAI_IMAGE_MODEL).strip()
        or _DEFAULT_OPENAI_IMAGE_MODEL
    )


def _cost_estimate_from_usage(usage: Dict[str, Any]) -> Optional[float]:
    for key in ("total_cost", "cost", "estimated_cost"):
        if key in usage and usage[key] is not None:
            try:
                return float(usage[key])
            except (TypeError, ValueError):
                pass
    return None


def _failed_openai_invocation(
    generation_request: GenerationRequest,
    *,
    model: str,
    started_at: datetime,
    completed_at: datetime,
    error_message: str,
) -> ModelInvocationRecord:
    return ModelInvocationRecord(
        invocation_id=f"invocation-{generation_request.request_id}",
        request_id=generation_request.request_id,
        provider="openai",
        model_name=model,
        status=InvocationStatus.FAILED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
        generated_candidate_ids=[],
        error_code="OPENAI_GENERATION_ERROR",
        error_message=error_message,
        retry_count=0,
    )


def _candidates_from_provider_outputs(
    generation_request: GenerationRequest,
    provider_result,
) -> List[GeneratedCandidate]:
    """Map provider outputs into workflow GeneratedCandidate contracts."""
    candidates: List[GeneratedCandidate] = []

    for output in provider_result.outputs:
        index = output.index + 1
        candidate_id = _candidate_id(generation_request.request_id, index)
        has_b64_json = bool(output.b64_json)

        metadata: Dict[str, Any] = {
            "provider": provider_result.provider,
            "model_name": provider_result.model_name,
            "request_id": generation_request.request_id,
            "job_id": generation_request.job_id,
            "spec_id": generation_request.spec_id,
            "openai_response_index": output.index,
            "has_b64_json": has_b64_json,
        }
        if output.url and not has_b64_json:
            metadata["openai_image_url"] = output.url
        if has_b64_json and output.b64_json:
            metadata["b64_json_length"] = len(output.b64_json)

        candidates.append(
            GeneratedCandidate(
                candidate_id=candidate_id,
                request_id=generation_request.request_id,
                uri=_candidate_uri(candidate_id, generation_request.output_format),
                properties=ImageProperties(
                    width_px=generation_request.output_width_px,
                    height_px=generation_request.output_height_px,
                    dpi=generation_request.target_dpi,
                    file_format=generation_request.output_format,
                ),
                metadata=metadata,
            )
        )

    return candidates


def _generate_fake(
    generation_request: GenerationRequest,
) -> Tuple[List[GeneratedCandidate], List[ModelInvocationRecord]]:
    """Deterministic local fake generator (no network, no API keys)."""
    count = _validate_generation_request(generation_request)
    started_at = datetime.utcnow()

    candidates: List[GeneratedCandidate] = []
    for index in range(1, count + 1):
        candidate_id = _candidate_id(generation_request.request_id, index)
        candidates.append(
            GeneratedCandidate(
                candidate_id=candidate_id,
                request_id=generation_request.request_id,
                uri=_candidate_uri(candidate_id, generation_request.output_format),
                properties=ImageProperties(
                    width_px=generation_request.output_width_px,
                    height_px=generation_request.output_height_px,
                    dpi=generation_request.target_dpi,
                    file_format=generation_request.output_format,
                ),
                metadata={
                    "job_id": generation_request.job_id,
                    "spec_id": generation_request.spec_id,
                    "provider": _FAKE_PROVIDER,
                    "model_name": _FAKE_MODEL,
                },
            )
        )

    completed_at = datetime.utcnow()
    candidate_ids = [c.candidate_id for c in candidates]

    invocation = ModelInvocationRecord(
        invocation_id=f"invocation-{generation_request.request_id}",
        request_id=generation_request.request_id,
        provider=_FAKE_PROVIDER,
        model_name=_FAKE_MODEL,
        status=InvocationStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
        generated_candidate_ids=candidate_ids,
        retry_count=0,
    )

    return candidates, [invocation]


def _generate_openai(
    generation_request: GenerationRequest,
) -> Tuple[List[GeneratedCandidate], List[ModelInvocationRecord]]:
    """OpenAI mode: delegate to the provider client, map into workflow contracts."""
    count = _validate_generation_request(generation_request)

    size = _openai_image_size(
        generation_request.output_width_px,
        generation_request.output_height_px,
    )
    model = _openai_model_name()
    started_at = datetime.utcnow()

    try:
        provider_result = generate_openai_images(
            prompt=generation_request.prompt,
            candidate_count=count,
            size=size,
            model=model,
        )
    except ValueError:
        # Configuration errors (e.g. missing API key) propagate to the caller.
        raise
    except Exception as exc:
        completed_at = datetime.utcnow()
        message = str(exc) or "OpenAI image generation failed"
        return [], [
            _failed_openai_invocation(
                generation_request,
                model=model,
                started_at=started_at,
                completed_at=completed_at,
                error_message=message,
            )
        ]

    completed_at = datetime.utcnow()
    candidates = _candidates_from_provider_outputs(generation_request, provider_result)

    invocation = ModelInvocationRecord(
        invocation_id=f"invocation-{generation_request.request_id}",
        request_id=generation_request.request_id,
        provider=provider_result.provider,
        model_name=provider_result.model_name,
        status=InvocationStatus.SUCCEEDED,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=_latency_ms(started_at, completed_at),
        generated_candidate_ids=[c.candidate_id for c in candidates],
        retry_count=0,
        usage={
            **provider_result.usage,
            "openai_size": provider_result.size,
            "requested_b64_json": provider_result.requested_b64_json,
        },
        cost_estimate=_cost_estimate_from_usage(provider_result.usage),
    )

    return candidates, [invocation]


def generate_candidates(
    generation_request: GenerationRequest,
) -> Tuple[List[GeneratedCandidate], List[ModelInvocationRecord]]:
    """
    Execute a GenerationRequest and return candidates plus invocation records.

    Default mode is fake: deterministic, no network, no OPENAI_API_KEY required.
    """
    mode = _generation_mode()

    if mode == "openai":
        return _generate_openai(generation_request)

    # Default and any unknown mode falls back to fake for safe local behavior.
    return _generate_fake(generation_request)
