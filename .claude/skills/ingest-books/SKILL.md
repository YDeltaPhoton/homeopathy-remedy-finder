---
name: ingest-books
description: Run or troubleshoot the document ingestion pipeline in scripts/ingestion (PDF OCR → cleaning → rule-based or Ollama LLM structuring → chunking → validation). Use when the user wants to ingest, OCR, re-process, structure, chunk, or validate homeopathic reference books under data/, or asks why a pipeline step failed.
argument-hint: "[pdf-or-dir] [--lang eng|deu|eng+deu] [--mode rule|llm|both]"
---

# Ingest Books Pipeline

Runs the pipeline documented in `scripts/ingestion/README.md`. Read that README when you need detail beyond this file. Always run commands **from the repository root**.

## Inputs to settle first

Infer these from the user's request; ask only if they truly cannot be inferred:

- **Source**: a PDF or directory (default `data/raw/`).
- **Language**: `eng`, `deu`, or `eng+deu` (Tesseract codes). Check the book title/content if unsure; historical German repertories → `deu`.
- **OCR mode**: `always` for scanned books (default choice for this project), `auto` to accept a good existing text layer, `never` to skip OCR.
- **Structuring mode**: `rule` (default, no LLM), `llm` (needs Ollama), or `both`.
- **Overwrite**: only pass `--overwrite` if the user wants to regenerate an existing book. Never pass it on your own.

## Step 0: Preflight

Detect the platform and check tools before running anything:

```bash
# Activate venv (macOS/Linux: source .venv/bin/activate; Windows: .venv\Scripts\Activate.ps1)
python -c "import fitz; print('PyMuPDF', fitz.__doc__)"
ocrmypdf --version
tesseract --list-langs      # must include every code in --lang
```

On Windows also check `gswin64c --version` (Ghostscript). If something is missing, point the user to the README "Setup" section for their OS (macOS: Homebrew; Windows: winget + `pip install ocrmypdf`) instead of improvising installs. Ask before installing system packages.

For LLM mode: `curl -s http://localhost:11434/api/tags` must answer and list the model (default `qwen2.5:7b`). If not, tell the user to run `ollama serve` / `ollama pull <model>`.

Create missing data folders:

```bash
mkdir -p data/raw data/processed data/structured_v2 data/structured_llm data/samples
```

## Step 1: OCR and page extraction

```bash
python scripts/ingestion/ingest_books.py <source> --lang <lang> --ocr <mode> [--manifest data/samples/books_manifest.json] [--overwrite]
```

- Output: `data/processed/<book>/` with `ocr.pdf`, `ocr.txt`, `pages.jsonl`, `book.json`, `report.json`.
- `FileExistsError` → the book was already processed. Report it and ask whether to `--overwrite` (back up first) or skip to Step 2.
- `auto` may stop and ask for `--ocr always` when some pages look unreliable; relay that, don't silently switch.
- OCR is slow on large books: run it in the background and wait for completion rather than polling.

## Step 2: Cleaning

```bash
python scripts/ingestion/clean_text.py
```

Produces `cleaned_pages.jsonl`, `hyphen_candidates.json`, `clean_report.json` per book.

## Step 3: Structuring

Rule mode:

```bash
python scripts/ingestion/structure_books.py            # → data/structured_v2/
```

LLM mode (independent of rule mode; can run on one book via `--input data/processed/<book>`):

```bash
python scripts/ingestion/llm_structure.py --input data/processed --output data/structured_llm --model qwen2.5:7b
```

`--resume` is on by default; pass `--no-resume` when the input, prompts, or model changed and a fresh analysis is wanted.

## Step 4–5: Chunk and validate each branch

`chunk_books.py` and `validate_data.py` default to the legacy `data/structured/`, so **always pass `--input`**:

```bash
python scripts/ingestion/chunk_books.py    --input data/structured_v2
python scripts/ingestion/validate_data.py  --input data/structured_v2 --processed data/processed

python scripts/ingestion/chunk_books.py    --input data/structured_llm
python scripts/ingestion/validate_data.py  --input data/structured_llm --processed data/processed
```

Add `--production` to validation only when the user asks for production checks.
Never chunk or validate a branch while its structurer is still writing.

## Freshness rules

- New `pages.jsonl` → rerun cleaning and everything downstream.
- New `cleaned_pages.jsonl` → rerun the chosen structurer, chunker, and validator.
- `--overwrite` on Step 1 does not regenerate downstream outputs.

## Reporting back

Summarize from the JSON reports (`report.json`, `clean_report.json`, `structure_report.json` / `llm_structure_report.json`, `validation_report.json`): pages processed, passages/chunks/candidates counts, validation errors and warnings. State clearly that:

- Validation is structural, not semantic; remedy/rubric candidates and relationships stay `inferred` until human review.
- Candidate counts are not accuracy metrics.

## Guardrails

- Never modify files in `data/raw/`.
- Never delete or overwrite outputs under `data/processed/` or `data/structured*/` without the user's confirmation.
- Don't commit anything under `data/` (it is git-ignored).
