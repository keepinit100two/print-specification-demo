"""
Artifact storage utility for generated images.

Pure file I/O helpers under artifacts/generated/. No workflow, provider,
or orchestration dependencies.
"""

import base64
import binascii
from pathlib import Path

_GENERATED_DIR = Path("artifacts") / "generated"


def ensure_generated_directory() -> Path:
    """Create artifacts/generated/ (and parents) if missing; return its Path."""
    _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    return _GENERATED_DIR


def save_generated_image(
    *,
    candidate_id: str,
    output_format: str,
    image_bytes: bytes,
) -> str:
    """
    Write image bytes to artifacts/generated/{candidate_id}.{output_format}.

    Overwrites an existing file. Returns the absolute path as a string.
    """
    if not candidate_id or not candidate_id.strip():
        raise ValueError("candidate_id is required")
    if not output_format or not output_format.strip():
        raise ValueError("output_format is required")
    if not image_bytes:
        raise ValueError("image_bytes must not be empty")

    directory = ensure_generated_directory()
    path = directory / f"{candidate_id.strip()}.{output_format.strip()}"
    path.write_bytes(image_bytes)
    return str(path.resolve())


def decode_base64_image(b64_json: str) -> bytes:
    """Decode a base64-encoded image string into raw bytes."""
    if b64_json is None or not str(b64_json).strip():
        raise ValueError("b64_json is required")

    payload = str(b64_json).strip()
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 image data") from exc
