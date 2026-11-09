# Document Ingestion Pipeline

This directory contains Python scripts for processing historical homeopathic reference books and preparing structured, traceable data for the Homeopathy Remedy Finder system.


## Features

- Batch PDF processing
- OCR using OCRmyPDF and Tesseract; retain `ocr.pdf` and the page-delimited `ocr.txt` sidecar
- Convert OCR sidecar TXT into page-aware `pages.jsonl` (without using PDF text extraction as the OCR text source)
- English and German text extraction
- Page-based source tracking
- Conservative OCR text cleaning and normalization
- Preservation of original and normalized text
- Detection of suspected OCR hyphenation errors
- Two optional structuring modes: rule-based and Ollama-assisted LLM extraction
- PDF text-block geometry, font-size extraction, and heuristic possible-spread detection
- Automatic heuristic heading detection (including mixed-case candidates)
- Preliminary remedy and rubric candidate identification
- Auditable candidate entities; conservative rule mode withholds uncertain passage-to-remedy and passage-to-rubric relationships
- JSONL generation for Books, Passages, Chunks, and candidate entities
- Text chunking with character offsets and overlap
- Data integrity and relationship validation
- Quality and validation reports
- Optional LLM identification of heading, remedy, rubric, and section candidates
- LLM-assisted page-local passage segmentation using exact normalized-text offsets
- Separate LLM output and review reports without overwriting rule-based data
- Preparation for PostgreSQL import and RAG retrieval

**Current limitations:** The layout module flags possible two-page spreads but does not guarantee page splitting or corrected OCR reading order. The rule-based classifier may mistake ordinary headings for remedies and does not automatically establish uncertain passage-remedy links. `reading_order.py` provides checks but is not necessarily invoked by the main pipeline.  Neither heuristic nor LLM-based recognition is semantically verified. All inferred relationships require review. Complex layouts, OCR errors, printed page numbers, and cross-page entity boundaries may need manual verification. The LLM extension does not reconstruct original-text offsets; original OCR text remains in the processed page records. Both structuring modes can be chunked and structurally validated, but validation does not establish semantic correctness. PostgreSQL import and embedding generation are not yet implemented.

## Requirements

- Python 3
- PyMuPDF
- OCRmyPDF
- Tesseract OCR
- English and/or German Tesseract language data
- Ghostscript (installed automatically with Homebrew `ocrmypdf` on macOS; install separately on Windows)
- Optional for LLM mode: Ollama and a locally installed model (example: `qwen2.5:7b`)

The LLM extension uses Python standard-library HTTP requests; no additional Ollama Python SDK is required.

## Project Structure

```text
scripts/ingestion/
├── ingest_books.py
├── clean_text.py
├── structure_books.py
├── llm_structure.py
├── chunk_books.py
├── validate_data.py
├── detect_layout.py
├── detect_headings.py
├── recognize_remedies.py
├── reading_order.py
├── requirements.txt
└── README.md

.claude/skills/ingest-books/
└── SKILL.md          # Claude Code skill for running this pipeline

data/
├── raw/
├── processed/
├── structured/
├── structured_v2/
├── structured_llm/
└── samples/
```

### Directory Descriptions

- `data/raw/`: Original PDF documents.
- `data/processed/`: Per-book `ocr.pdf`, `ocr.txt`, `pages.jsonl`, `cleaned_pages.jsonl`, metadata, and quality reports.
- `data/structured/`: Legacy rule-based outputs (if retained).
- `data/structured_v2/`: Updated rule-based candidates, passages, chunks, and validation reports (new default).
- `data/structured_llm/`: Independent LLM-assisted JSONL records, chunks, validation reports, and LLM review reports.
- `data/samples/`: Small datasets for testing and evaluation.

## Setup

Run all commands from the repository root.

### 1. Install OCR Tools (macOS)

```bash
brew install ocrmypdf
brew install tesseract-lang
```

### 1b. Install OCR Tools (Windows)

OCRmyPDF on Windows needs Tesseract and Ghostscript installed separately; OCRmyPDF itself is installed with pip into the virtual environment (Step 2–3). Run the following in PowerShell:

```powershell
winget install --id UB-Mannheim.TesseractOCR
winget install --id ArtifexSoftware.GhostScript
```

Without `winget`, download the installers manually:

- Tesseract: <https://github.com/UB-Mannheim/tesseract/wiki>
- Ghostscript: <https://ghostscript.com/releases/gsdnld.html>

**German language data:** The Tesseract installer includes English by default. Select German under *Additional language data* during installation, or download `deu.traineddata` from <https://github.com/tesseract-ocr/tessdata> and copy it into `C:\Program Files\Tesseract-OCR\tessdata\`.

**PATH:** Make sure both tools are on `PATH` (adjust the Ghostscript version to the installed one), then open a new terminal:

```powershell
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$userPath;C:\Program Files\Tesseract-OCR;C:\Program Files\gs\gs10.05.1\bin", "User")
```

(You can also add these folders via *System Properties → Environment Variables*.)

After creating and activating the virtual environment (Step 2), install OCRmyPDF and verify the setup:

```powershell
python -m pip install ocrmypdf
tesseract --version
tesseract --list-langs   # should include eng and deu
gswin64c --version
ocrmypdf --version
```

Alternatively, run the whole pipeline inside WSL (Ubuntu) and follow the Linux packages: `sudo apt install ocrmypdf tesseract-ocr-eng tesseract-ocr-deu`.

### 2. Create a Python Virtual Environment

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### 3. Install Python Dependencies

```bash
python -m pip install -r scripts/ingestion/requirements.txt
```

### 4. Create Data Directories

```bash
mkdir -p data/raw data/processed data/structured data/structured_v2 data/structured_llm data/samples
```

### 5. Install and Configure Ollama (Optional)

Only required for LLM-assisted structuring. On macOS with Homebrew:

```bash
brew install ollama
```

Start the Ollama server if it is not already running (keep this terminal open):

```bash
ollama serve
```

In a second terminal, download and check the model:

```bash
ollama pull qwen2.5:7b
ollama list
```

The default local endpoint is `http://localhost:11434`. If Ollama is already running as a Homebrew background service (`brew services start ollama`), do not also run `ollama serve`. The rule-based pipeline does not require Ollama or any LLM.

## Claude Code Skill

The pipeline has a project skill for [Claude Code](https://claude.com/claude-code) at `.claude/skills/ingest-books/SKILL.md`. It checks the OCR tools and Ollama, runs the steps below in order with the correct `--input` paths for each branch, and summarizes the JSON reports. It does not overwrite existing outputs without asking.

Invoke it from the repository root in Claude Code:

```text
/ingest-books data/raw --lang deu --mode rule
/ingest-books data/raw/<book>.pdf --lang eng --mode both
```

Or describe the task in plain language (e.g. "OCR the new German PDFs in data/raw and run the rule-based pipeline"); Claude loads the skill automatically. Update the skill when script arguments or default paths change.

## Usage

Run the following steps from the repository root.

### Step 1: PDF OCR and Text Extraction

Place PDF documents in `data/raw/`.

For German documents:

```bash
python scripts/ingestion/ingest_books.py data/raw --lang deu --ocr always
```

For English documents:

```bash
python scripts/ingestion/ingest_books.py data/raw --lang eng --ocr always
```

For mixed English and German documents:

```bash
python scripts/ingestion/ingest_books.py data/raw --lang eng+deu --ocr always
```

OCR modes:

- `auto`: Accept an existing PDF text layer when all pages pass a basic text check. If some pages appear unreliable and a text layer exists, stop and request explicit `--ocr always`; otherwise perform OCR.
- `always`: Run OCRmyPDF with `--force-ocr` on every PDF page and save `ocr.txt` with `--sidecar`. This can rasterize existing PDF text; check the results.
- `never`: Do not run OCR; extract the existing PDF text layer and save it in the same TXT/JSONL format.

In all modes, `ocr.txt` is retained. For `always`, it is OCRmyPDF sidecar text. For `auto` when existing text is accepted, or `never`, it is text extracted from the PDF text layer. The script checks that TXT page segments match the PDF page count before writing page records.

If the processed book already exists, the script preserves it and raises `FileExistsError` unless you explicitly use `--overwrite`. To regenerate the same book after changing the ingestion code:

```bash
python scripts/ingestion/ingest_books.py data/raw \
  --lang deu \
  --ocr always \
  --overwrite
```

`--overwrite` replaces that book’s ingestion outputs; it does **not** automatically regenerate cleaned pages, rule-based outputs, or LLM outputs. Rerun all downstream steps. Back up any outputs you need to keep.

Optional bibliographic metadata can be supplied using `--manifest`:

```bash
python scripts/ingestion/ingest_books.py data/raw \
  --lang deu \
  --manifest data/samples/books_manifest.json
```

The manifest is a JSON object keyed by the exact PDF filename. It may contain title, author, edition, year, language, source URL, and rights status.

Output directory: `data/processed/`. Each book folder retains `ocr.pdf`, `ocr.txt`, `pages.jsonl`, `book.json`, and `report.json`. The `pages.jsonl` text comes from the TXT source; it is not re-extracted from `ocr.pdf` after OCR. PDF text-block geometry and heuristic layout metadata may also be recorded, but those PDF blocks are not used as text offsets into the OCR sidecar. This does not repair OCR text order or automatically split all spreads.

Each processed book receives its own directory.

### Step 2: Text Cleaning

```bash
python scripts/ingestion/clean_text.py
```

This step reads `pages.jsonl` (already converted from `ocr.txt`), preserves its original page text, and writes cleaned/normalized text to `cleaned_pages.jsonl`. It does **not** directly edit `ocr.txt` or generate a cleaned TXT by default. Suspected hyphenation errors are reported. Available layout metadata is carried forward for later analysis.

Output files include:

- `cleaned_pages.jsonl`
- `hyphen_candidates.json`
- `clean_report.json`

### Step 3A: Rule-Based Document Structuring (No LLM)

```bash
python scripts/ingestion/structure_books.py
```

This step calls `detect_headings.py` and `recognize_remedies.py` to identify candidate headings, including mixed-case names, and attempts preliminary classification of remedy and rubric entries.

No manually maintained remedy dictionary is required.

The updated rule mode does **not** automatically assign uncertain passage-to-remedy or passage-to-rubric links. Candidate headings and candidate remedies remain unverified. Its default output is `data/structured_v2/`; use `--out` to override.

Output files include:

- `books.jsonl`
- `passages.jsonl`
- `remedies.jsonl`
- `rubrics.jsonl`
- `passage_remedy.jsonl`
- `passage_rubric.jsonl`
- `rubric_remedy.jsonl`
- `heading_candidates.jsonl`
- `structure_report.json`

Additional empty relationship files may be generated for later processing.

### Step 3B: LLM-Assisted Document Structuring (Optional Alternative)

Instead of Step 3A, you can run the independent LLM extension **after Step 2**:

```bash
python scripts/ingestion/llm_structure.py \
  --model qwen2.5:7b \
  --input data/processed \
  --output data/structured_llm \
  --resume
```

The script reads `book.json` and `cleaned_pages.jsonl` from each book folder under `data/processed/`—**not** `ocr.txt` directly—then calls the local Ollama API, and writes results under `data/structured_llm/<book-folder>/`. It does **not** require running `structure_books.py` first, and it does **not** overwrite `data/structured/`.

To process one already-cleaned book, replace `<book-folder>` with its actual directory name:

```bash
python scripts/ingestion/llm_structure.py \
  --input data/processed/<book-folder> \
  --model qwen2.5:7b
```

LLM output files include:

- `books.jsonl`
- `passages.jsonl`
- `remedies.jsonl`
- `rubrics.jsonl`
- `passage_remedy.jsonl`
- `passage_rubric.jsonl`
- `rubric_remedy.jsonl` (currently empty)
- `heading_candidates.jsonl`
- `llm_structure_report.json`
- Empty placeholder files for `symptom_terms`, `synonyms`, and `symptom_rubric` (until implemented)

The LLM proposes headings and classifies candidate entities. Proposed headings are matched against exact text in the normalized page. A page can be divided into multiple passages at detected headings. Relationships remain `inferred`, not `verified`.

**Limitations of the current implementation:**

- Extraction is page-based; continuation across pages uses a heuristic and may be incorrect.
- Long pages are analyzed in overlapping windows (`--max-chars` defaults to 12,000 characters per window); headings near window boundaries may still be missed.
- The `original_text` field in LLM passages is currently `null`; the original OCR text remains in `data/processed/`.
- The model's confidence values are estimates, not calibrated probabilities.
- Re-running this script overwrites that book's LLM output files.
- The updated `chunk_books.py` can process LLM passages when pointed to `data/structured_llm/`, and the updated `validate_data.py` checks page-slice offsets and coverage. Validation does not prove that inferred headings or relationships are semantically correct.

### Step 4: Text Chunking (Either Mode)

Rule-based output:

```bash
python scripts/ingestion/chunk_books.py --input data/structured_v2
```

LLM-assisted output:

```bash
python scripts/ingestion/chunk_books.py --input data/structured_llm
```

This step splits normalized passages into smaller retrieval units.

Each chunk includes:

- `chunk_id`
- `passage_id`
- `chunk_text`
- `chunk_order`
- `start_offset`
- `end_offset`

Default settings:

- Maximum chunk size: 900 characters
- Target overlap: 120 characters

Custom settings:

```bash
python scripts/ingestion/chunk_books.py \
  --max-chars 1200 \
  --overlap 150
```

Output:

- `chunks.jsonl`

Character offsets refer to `normalized_text`, not the original PDF text.

### Step 5: Data Validation (Either Mode)

Rule-based output:

```bash
python scripts/ingestion/validate_data.py --input data/structured_v2
```

LLM-assisted output (after its chunking step):

```bash
python scripts/ingestion/validate_data.py \
  --input data/structured_llm \
  --processed data/processed
```

This step checks:

- Required structured files
- Unique entity identifiers
- Book-to-Passage references
- Passage-to-Chunk references
- Chunk text offsets and coverage
- Remedy and rubric relationship references
- Inferred relationship provenance
- Source text consistency (full-page passages or page-slice offsets for LLM passages)
- Coverage and non-overlap of LLM page slices
- Source rights status

Output:

- `validation_report.json`

For stricter production validation:

```bash
python scripts/ingestion/validate_data.py --input data/structured_v2 --production
```

Production mode rejects unapproved source rights and inferred relationships in the validated corpus.

**Important:** Structural validation does not guarantee OCR accuracy or semantic correctness.

## Data Processing Workflow

```text
data/raw/
    |
    v
ingest_books.py  [OCRmyPDF --sidecar, uses detect_layout.py]
    |
    v
data/processed/<book>/ocr.pdf + ocr.txt
    |
    v
data/processed/<book>/pages.jsonl
    |
    v
clean_text.py
    |
    v
data/processed/<book>/cleaned_pages.jsonl
    |
    +------------------------------+
    |                              |
    v                              v
structure_books.py            llm_structure.py
[uses detect_headings.py,
 recognize_remedies.py]
    |                              |
    v                              v
data/structured_v2/           data/structured_llm/
    |                              |
    v                              v
chunk_books.py --input data/structured_v2    chunk_books.py --input data/structured_llm
    |                              |
    v                              v
validate_data.py --input data/structured_v2  validate_data.py --input data/structured_llm
    |                              |
    +------------------------------+
                   |
                   v
       Structurally validated JSONL
                   |
                   v
       Human review of inferred facts
                   |
                   v
       PostgreSQL import (future)
                   |
                   v
       Embedding and hybrid retrieval (future)
```

Both branches produce independent artifacts. Structural validation is not semantic verification and does not automatically approve inferred relationships for production.

## Data Model

The ingestion pipeline prepares records corresponding to the core entities defined in Specification v1.4.

| Entity | Purpose |
|---|---|
| Book | Bibliographic and source metadata |
| Passage | Source text and page-level provenance |
| Chunk | Retrieval unit with offsets |
| Remedy | Candidate homeopathic remedy entity |
| Rubric | Candidate symptom or repertory category |
| Passage–Remedy | Candidate source-to-remedy relationship |
| Passage–Rubric | Candidate source-to-rubric relationship |
| Rubric–Remedy | Future verified or inferred rubric-remedy associations |

The generated JSONL files are intermediate ingestion artifacts and are not themselves the PostgreSQL database.

## Notes

- Original PDF documents must remain unchanged.
- Original and normalized text must be stored separately; the current LLM output retains the original text only in the upstream cleaned-page records.
- PDF page numbers must be preserved for source traceability.
- Printed page numbers must not be inferred without evidence.
- Candidate remedy and rubric relationships must remain distinguishable from verified relationships.
- Automatic heading detection does not guarantee correct semantic classification.
- OCR corrections must preserve the original text and remain auditable.
- Raw documents and generated processing outputs should normally be excluded from Git.
- Source rights must be checked before production use.
- The ingestion pipeline is intended to process historical literature, not to establish clinical effectiveness.

## Future Development

- Improved OCR error detection and correction
- Layout-aware heading and chapter recognition
- More reliable remedy and rubric identification
- Additional semantic and provenance checks for both structuring modes
- Mapping normalized-text offsets back to original OCR text
- Evaluation of LLM extraction against manually reviewed reference pages
- Reliable cross-page scope tracking and rubric hierarchy extraction
- More reliable passage segmentation at actual section boundaries
- Automated verification and review workflows
- Printed page number extraction and verification
- PostgreSQL import using the Go backend
- Embedding generation with model version tracking
- Hybrid retrieval indexing
- End-to-end ingestion automation

## Updated module roles and execution notes

The four helper modules live alongside the six main scripts in `scripts/ingestion/`. They are **not** separate CLI stages:

- `detect_layout.py`: imported by `ingest_books.py` to collect PDF text-block coordinates and font sizes, and flag possible two-page spreads.
- `detect_headings.py`: imported by `structure_books.py` to find candidate heading spans.
- `recognize_remedies.py`: imported by `structure_books.py` to classify candidate headings conservatively.
- `reading_order.py`: auxiliary reading-order diagnostics; do not assume the pipeline automatically runs it.

Run the two structuring modes independently after OCR and cleaning. Rule mode defaults to `data/structured_v2/`; LLM mode defaults to `data/structured_llm/`. Chunk and validate each branch separately. Do not run chunking or validation against a branch while its structuring script is still writing outputs.

If you already have cleaned pages, you can test the updated rule-based structurer immediately without redoing OCR:

```bash
python scripts/ingestion/structure_books.py
python scripts/ingestion/chunk_books.py --input data/structured_v2
python scripts/ingestion/validate_data.py --input data/structured_v2 --processed data/processed
```

To regenerate ingestion data using the TXT-sidecar workflow, run `ingest_books.py` with `--ocr always --overwrite`, then rerun `clean_text.py` and the chosen structuring, chunking, and validation stages. This redoes OCR; `--ocr never` instead reads an existing PDF text layer and does not perform OCR. Back up processed outputs before reprocessing.

**Implementation status:** This pipeline supports heuristic extraction and structural validation; it does not guarantee reliable double-page separation, full remedy scope recovery across pages, or semantic verification. Candidate count is not an accuracy metric.

## TXT source and freshness checks

- `ocr.txt` is an intermediate text source, not the LLM input file. Page boundaries are represented by form-feed characters (`\f`).
- `pages.jsonl` holds the TXT-derived text and PDF page metadata. `cleaned_pages.jsonl` is the normalized input for either structuring mode.
- If you regenerate `pages.jsonl`, rerun cleaning. If you regenerate `cleaned_pages.jsonl`, rerun the selected structurer, chunker, and validator.
- Existing LLM checkpoints may be reusable only when the implementation correctly invalidates them for changed input, model, and prompts. Verify checkpoint behavior before treating an `--resume` run as a fresh reanalysis.
- A structurally valid JSONL file does not guarantee accurate OCR, reading order, or remedy classification.
