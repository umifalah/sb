# Split Bill — Backend & Receipt Extraction

Self-hosted FastAPI backend that extracts structured data from Indonesian receipt
photos (no LLM), computes a fair **proportional** split, and stores bills by a
shareable code. Includes a debug web tool to measure extraction accuracy.

See [docs/spec.md](docs/spec.md) for the design and [docs/plan.md](docs/plan.md)
for the implementation plan.

## Architecture

Quality Gate → OCR (PaddleOCR, swappable) → Parser → Reconciler produce a
structured `Receipt`. A pure-function Split Engine computes per-person shares; a
SQLite store persists bills. The pipeline is designed to **fail visibly** —
blurry images or non-reconciling totals trigger re-scan / manual review rather
than guessed numbers.

## Setup

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt   # full set incl. PaddleOCR (heavy)
```

For development without OCR (everything except live scanning):

```bash
uv pip install "pydantic>=2" pytest opencv-python-headless numpy rapidfuzz \
  fastapi uvicorn httpx python-multipart
```

## Run

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/> for the debug tool. API docs at `/docs`.

## Test

```bash
pytest -q          # fast suite (no PaddleOCR needed)
pytest -m slow     # end-to-end with real PaddleOCR + real receipt images
```

## Status

Tasks 0–10 implemented and tested (26 passing). Task 11 (end-to-end with real
PaddleOCR + real receipt photos) is pending the `paddlepaddle` install and
sample receipts — see [docs/plan.md](docs/plan.md).
