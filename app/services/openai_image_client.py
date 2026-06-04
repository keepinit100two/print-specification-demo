"""
OpenAI Images API client boundary.

Owns lazy OpenAI import, API key validation, and images.generate calls.
Returns a small provider-neutral result — no workflow contracts, no file I/O,
no artifact writes, and no print validation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_DEFAULT_MODEL = "gpt-image-1"
_PROVIDER = "openai"


@dataclass(frozen=True)
class OpenAIImageOutput:
    """One image returned by the OpenAI Images API."""

    index: int
    b64_json: Optional[str] = None
    url: Optional[str] = None
    revised_prompt: Optional[str] = None


@dataclass(frozen=True)
class OpenAIImageGenerationResult:
    """Provider-neutral outcome of an OpenAI image generation call."""

    provider: str
    model_name: str
    size: str
    outputs: List[OpenAIImageOutput]
    usage: Dict[str, Any] = field(default_factory=dict)
    requested_b64_json: bool = False


def _resolve_api_key(api_key: Optional[str]) -> str:
    key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI image generation")
    return key


def _resolve_model(model: Optional[str]) -> str:
    resolved = (
        model
        or os.environ.get("PRINT_OPENAI_IMAGE_MODEL", _DEFAULT_MODEL).strip()
        or _DEFAULT_MODEL
    )
    return resolved


def _extract_usage(response: Any) -> Dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {"raw": str(usage)}


def _extract_outputs(response: Any) -> List[OpenAIImageOutput]:
    data = getattr(response, "data", None) or []
    outputs: List[OpenAIImageOutput] = []
    for index, item in enumerate(data):
        outputs.append(
            OpenAIImageOutput(
                index=index,
                b64_json=getattr(item, "b64_json", None),
                url=getattr(item, "url", None),
                revised_prompt=getattr(item, "revised_prompt", None),
            )
        )
    return outputs


def _call_images_generate(client: Any, **kwargs: Any) -> tuple[Any, bool]:
    """
    Call images.generate, preferring b64_json when supported.

    Retries without response_format only when the client/model rejects it.
    Other provider exceptions propagate to the caller.
    """
    try:
        response = client.images.generate(response_format="b64_json", **kwargs)
        return response, True
    except TypeError:
        response = client.images.generate(**kwargs)
        return response, False
    except Exception as exc:
        message = str(exc).lower()
        if "response_format" in message or "b64_json" in message:
            response = client.images.generate(**kwargs)
            return response, False
        raise


def generate_openai_images(
    *,
    prompt: str,
    candidate_count: int,
    size: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> OpenAIImageGenerationResult:
    """
    Generate images via the OpenAI Images API.

    OpenAI is imported only when this function runs. Provider exceptions
    (other than response_format fallback) are not caught here.
    """
    if candidate_count <= 0:
        raise ValueError("candidate_count must be greater than zero")
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required for OpenAI image generation")

    resolved_key = _resolve_api_key(api_key)
    resolved_model = _resolve_model(model)

    from openai import OpenAI

    client = OpenAI(api_key=resolved_key)
    response, requested_b64 = _call_images_generate(
        client,
        model=resolved_model,
        prompt=prompt,
        n=candidate_count,
        size=size,
    )

    return OpenAIImageGenerationResult(
        provider=_PROVIDER,
        model_name=resolved_model,
        size=size,
        outputs=_extract_outputs(response),
        usage=_extract_usage(response),
        requested_b64_json=requested_b64,
    )
