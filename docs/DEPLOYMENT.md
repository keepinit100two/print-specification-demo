# Deployment (Tier-1 template)

This project is a reusable Tier-1 control plane:
Ingest → Normalize → Decide → Act → Observe → Improve

The print-specification workflow extends that template with a dedicated
orchestration spine (`app/services/print_orchestrator.py`) and stage services
(normalization through validation). This doc covers local run, print-related
environment variables, and manual generation smoke tests.

---

## Environment variables

Copy `.env.example` to `.env` and fill values. Do not commit `.env`.

### Server / ops (template)

- `PORT` (default 8080)
- `OPS_API_KEY` (used by protected /ops endpoints; added in Group 3)

### Print workflow — AI generation (optional)

Used by `app/services/print_generation.py` and `scripts/demo_generate_image.py`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRINT_GENERATION_MODE` | `fake` | `fake` = deterministic local generator (tests, offline). `openai` = OpenAI Images API. |
| `OPENAI_API_KEY` | — | Required when `PRINT_GENERATION_MODE=openai`. |
| `PRINT_OPENAI_IMAGE_MODEL` | `gpt-image-1` | OpenAI image model id passed to `openai_image_client`. |

**Fake mode** needs no API key and performs no network I/O. **OpenAI mode** calls
`openai_image_client` and may write returned images to `artifacts/generated/`
via `generated_artifact_store`.

Example `.env` fragment for OpenAI smoke testing:

```env
PRINT_GENERATION_MODE=openai
OPENAI_API_KEY=sk-...
PRINT_OPENAI_IMAGE_MODEL=gpt-image-1
```

---

## Manual smoke test: image generation

Before or alongside orchestrator integration, verify generation in isolation:

```bash
python scripts/demo_generate_image.py
```

The script builds a sample `DesignJob`, resolves a `PrintSpecification`, constructs
a `GenerationRequest`, calls `generate_candidates()`, and prints candidate URIs,
artifact paths (when saved), and `ModelInvocationRecord` metadata. It loads `.env`
from the repo root when `python-dotenv` is installed.

Use `PRINT_GENERATION_MODE=fake` in CI and unit tests; use `openai` only when
you intend to hit the live API.

---

## Local run (venv)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

Run the print workflow test suite (no OpenAI required with default `fake` mode):

```bash
pytest
```
