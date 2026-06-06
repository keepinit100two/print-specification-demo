# Deployment (Tier-1 template)

This project is a reusable Tier-1 control plane:
Ingest → Normalize → Decide → Act → Observe → Improve

The print-specification workflow extends that template with a dedicated
orchestration spine (`app/services/print_orchestrator.py`) and stage services
from normalization through production packaging and completion.

**Architecture docs:**

- `docs/PRINT_WORKFLOW_CONTRACT_MAP.md` — contracts, subsystems, branch paths
- `docs/PRINT_ORCHESTRATION_CONTRACT.md` — spine responsibilities and wiring
- `docs/IMPLEMENTATION_SEQUENCE.md` — phased build history and completion status

**Workflow (implemented and orchestrated):**

```
SUBMITTED → NORMALIZATION → SPECIFICATION → COMPLIANCE → ADAPTATION
    ├── DETERMINISTIC_TRANSFORM
    └── AI_GENERATION
→ VALIDATION → APPROVAL_PACKAGE → APPROVAL_DECISION → PRODUCTION_PACKAGE → COMPLETED
```

This doc covers local run, print-related environment variables, OpenAI boundary,
artifact storage, and manual generation smoke tests.

---

## Workflow Completion Status

- All workflow stages **implemented** and **orchestrated** (Phases 1–11).
- End-to-end happy path **tested** (`test_end_to_end_happy_path_smoke`).
- **233 tests passing** — use `PRINT_GENERATION_MODE=fake` (default) so CI needs no API key.

---

## Environment variables

Copy `.env.example` to `.env` and fill values. Do not commit `.env`.

### Server / ops (template)

- `PORT` (default 8080)
- `OPS_API_KEY` (used by protected /ops endpoints; added in Group 3)

### Print workflow — AI generation (optional)

Used by `app/services/print_generation.py` and `scripts/demo_generate_image.py`.
The orchestrator **never** calls OpenAI directly — only `AIGenerationService`
(`generate_candidates`) does, and only on the AI remediation branch when
`AdaptationPlan.requires_generation` is true.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRINT_GENERATION_MODE` | `fake` | `fake` = deterministic local generator (tests, offline, default). `openai` = OpenAI Images API. |
| `OPENAI_API_KEY` | — | Required when `PRINT_GENERATION_MODE=openai`. |
| `PRINT_OPENAI_IMAGE_MODEL` | `gpt-image-1` | OpenAI image model id passed to `openai_image_client`. |

**Fake mode** (default) needs no API key and performs no network I/O. This is
the intended mode for `pytest` and orchestration tests (including the end-to-end
smoke test, which monkeypatches `generate_candidates`).

**OpenAI mode** calls `app/services/openai_image_client.py` (provider boundary
only — lazy import, API key check, `images.generate`) and may persist returned
base64 images via `app/services/generated_artifact_store.py` under
`artifacts/generated/{candidate_id}.{format}`. Contract URIs remain
`artifact://generated/...`; the store is a local persistence helper, not a
workflow stage.

**Why AI is not the default path:** adaptation planning selects deterministic
transforms when possible; generation runs only when `requires_generation=True`.
Even then, deployment defaults to `fake` unless explicitly configured for OpenAI.

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

Expected: **233 tests passing**, including orchestration wiring for all phases
and the end-to-end happy path smoke test.

---

## Known MVP Tradeoffs

- `ValidationResult.validated_candidate_ids` stores both candidate and
  transformed-asset output ids.
- Deterministic-transform validation may use a placeholder candidate when
  `deterministic_transform` is not yet on the run bundle.
- Approval decisions are submitted via `request.metadata["approval_decision"]`.
- No external email delivery for owner review yet.

See `docs/PRINT_WORKFLOW_CONTRACT_MAP.md` for full detail.
