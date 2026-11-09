
#!/usr/bin/env python3
"""
Optional LLM-assisted document structuring for Homeopathy Remedy Finder.

Backend: local Ollama HTTP API.
Input:  data/processed/<book>/book.json
        data/processed/<book>/cleaned_pages.jsonl
Output: data/structured_llm/<book>/*.jsonl

The original rule-based output is never overwritten.

All extracted entities and relationships are INFERRED, not VERIFIED.
The program validates proposed heading text against the source and
creates passage boundaries using exact character offsets.

Run from the repository root:
    python3 scripts/ingestion/llm_structure.py --model qwen2.5:7b
"""

import argparse
import os
import socket
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SYSTEM_PROMPT = """
You are a document structure extraction assistant.

You analyze historical homeopathic reference literature,
including English and German Materia Medica, Repertories,
and other historical medical texts.

Your task is DOCUMENT STRUCTURING, not medical advice.

Identify:
1. The probable document type.
2. Explicit remedy-entry headings.
3. Explicit rubric headings.
4. Chapter or section headings.
5. Other headings when identifiable.

Important rules:
- Use only information present in the supplied text.
- Never invent headings, remedies, rubrics, or relationships.
- Distinguish an entry heading from a name mentioned in prose.
- Preserve the exact heading text as it appears in the input.
- Only return headings whose full text occurs in the input.
- Return headings in reading order.
- Do not correct OCR spelling in the extracted heading text.
- Use "unknown" when classification is uncertain.
- Do not infer clinical efficacy or recommend treatment.
- Do not assign VERIFIED status.

Return ONLY a JSON object with this structure:

{
  "document_type": "materia_medica",
  "headings": [
    {
      "text": "ARNICA MONTANA",
      "kind": "remedy",
      "confidence": 0.9
    }
  ]
}

Allowed document_type values:
materia_medica, repertory, narrative, unknown

Allowed heading kind values:
remedy, rubric, chapter, section, unknown

Confidence is an estimate, not a calibrated probability.

If no headings are identifiable, return an empty headings array.
"""


def stable_id(prefix, *values):
    raw = "\x1f".join(map(str, values))
    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]
    return prefix + digest


def read_jsonl(path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: {exc}"
                ) from exc
            yield row


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def write_jsonl(path, records):
    atomic_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def ollama_generate(base_url, model, prompt, timeout):
    endpoint = base_url.rstrip("/") + "/api/generate"

    payload = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_ctx": 8192
        }
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:
            result = json.load(response)

    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Ollama request failed. Check that Ollama is "
            f"running and the model is installed: {exc}"
        ) from exc

    content = result.get("response", "")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "LLM did not return valid JSON"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")

    return parsed


def generate_with_retries(args, prompt):
    for attempt in range(args.retries + 1):
        try:
            return ollama_generate(args.url, args.model, prompt, args.timeout)
        except (TimeoutError, socket.timeout, urllib.error.URLError,
                RuntimeError, ValueError, OSError) as exc:
            if attempt >= args.retries:
                raise RuntimeError(f"Ollama failed after {attempt + 1} attempts: {exc}") from exc
            wait = min(30, 2 ** attempt * 2)
            print(f"Ollama error ({exc}); retry {attempt + 1}/{args.retries} in {wait}s", flush=True)
            time.sleep(wait)


def cached_page_analysis(output, book, page, args):
    pdf_page = page["pdf_page"]
    page_text = page.get("normalized_text", "")
    cache_dir = output / ".llm_checkpoints"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Cache identity includes the exact source, prompt configuration and model.
    identity = hashlib.sha256(json.dumps({
        "book": book, "page": pdf_page, "text": page_text,
        "layout": (page.get("layout") or {}).get("layout_type"),
        "model": args.model, "max_chars": args.max_chars,
        "prompt_version": 1, "system_prompt": SYSTEM_PROMPT,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    cache_file = cache_dir / f"page_{pdf_page}.json"
    if args.resume and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("identity") == identity and cached.get("complete") is True:
                print(f"PDF page {pdf_page}: restored checkpoint", flush=True)
                return cached["proposals"], cached["page_types"], cached["review_items"], cached["window_count"]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    overlap = min(400, max(0, args.max_chars // 8))
    windows = []
    cursor = 0
    while cursor < len(page_text):
        end = min(cursor + args.max_chars, len(page_text))
        windows.append((cursor, page_text[cursor:end]))
        if end == len(page_text):
            break
        cursor = end - overlap
    proposals, page_types, reviews = [], [], []
    for window_start, window_text in windows:
        prompt = build_prompt(book, pdf_page, window_text, (page.get("layout") or {}).get("layout_type"))
        response = generate_with_retries(args, prompt)
        doc_type, window_headings = classify_response(response)
        page_types.append(doc_type)
        located, rejected = locate_headings(window_text, window_headings)
        for item in rejected:
            reviews.append({"pdf_page": pdf_page, "window_start": window_start, **item})
        for heading in located:
            proposals.append({**heading,
                "start_offset": window_start + heading["start_offset"],
                "end_offset": window_start + heading["end_offset"]})
    write_json(cache_file, {"identity": identity, "complete": True,
        "proposals": proposals, "page_types": page_types,
        "review_items": reviews, "window_count": len(windows)})
    return proposals, page_types, reviews, len(windows)


def classify_response(response):
    allowed_types = {
        "materia_medica",
        "repertory",
        "narrative",
        "unknown"
    }

    allowed_kinds = {
        "remedy",
        "rubric",
        "chapter",
        "section",
        "unknown"
    }

    document_type = response.get(
        "document_type", "unknown"
    )

    if document_type not in allowed_types:
        document_type = "unknown"

    raw_headings = response.get("headings", [])

    if not isinstance(raw_headings, list):
        raise ValueError("headings must be a list")

    headings = []

    for item in raw_headings:
        if not isinstance(item, dict):
            continue

        text = item.get("text")
        kind = item.get("kind", "unknown")
        confidence = item.get("confidence", 0)

        if not isinstance(text, str) or not text.strip():
            continue

        if kind not in allowed_kinds:
            kind = "unknown"

        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0

        confidence = max(0.0, min(1.0, confidence))

        headings.append({
            "text": text.strip(),
            "kind": kind,
            "confidence": confidence
        })

    return document_type, headings


def locate_headings(text, headings):
    """
    Match proposed headings to exact source text.

    Repeated identical headings are matched in sequence.
    Unmatched suggestions are rejected.
    """

    accepted = []
    rejected = []
    cursor = 0

    for heading in headings:
        needle = heading["text"]

        # Accept only a full, standalone line, not an occurrence in prose.
        pattern = re.compile(r"(?m)^[ \t]*" + re.escape(needle) + r"[ \t]*$")
        match = pattern.search(text, cursor)
        position = match.start() + len(match.group()) - len(match.group().lstrip()) if match else -1

        if position < 0:
            rejected.append({
                **heading,
                "reason": "exact_source_match_not_found"
            })
            continue

        end = position + len(needle)

        accepted.append({
            **heading,
            "start_offset": position,
            "end_offset": end
        })

        cursor = end

    return accepted, rejected


def split_passages(text, headings):
    """
    Split a page at accepted heading boundaries.

    The first passage may be a continuation of the
    previous page. No semantic correctness is assumed.
    """

    starts = sorted({
        0,
        *[
            heading["start_offset"]
            for heading in headings
        ]
    })

    result = []

    for index, start in enumerate(starts):
        end = (
            starts[index + 1]
            if index + 1 < len(starts)
            else len(text)
        )

        if not text[start:end].strip():
            continue

        active_heading = next(
            (
                h for h in headings
                if h["start_offset"] == start
            ),
            None
        )

        result.append({
            "start_offset": start,
            "end_offset": end,
            "text": text[start:end],
            "heading": active_heading
        })

    return result


def build_prompt(book, page_number, text, layout_type=None):
    metadata = {
        "title": book.get("title"),
        "author": book.get("author"),
        "language": book.get("language"),
        "pdf_page": page_number
    }

    return (
        "Bibliographic metadata:\n"
        + json.dumps(metadata, ensure_ascii=False)
        + "\n\nAnalyze this PDF page.\n"
        + "Only extract headings actually present on this page.\n"
        + "The page may begin or end in the middle of an entry.\n"
        + "Do not invent missing headings.\n\n"
        + "Layout hint (heuristic): " + str(layout_type or "unknown") + "\n"
        + "PAGE TEXT:\n"
        + text
    )


def process_book(folder, args):
    book_path = folder / "book.json"
    pages_path = folder / "cleaned_pages.jsonl"

    book = json.loads(
        book_path.read_text(encoding="utf-8")
    )

    book_id = book["book_id"]
    output = args.output / folder.name
    output.mkdir(parents=True, exist_ok=True)

    pages = list(read_jsonl(pages_path))

    passages = []
    remedies = {}
    rubrics = {}
    heading_candidates = []
    passage_remedy = []
    passage_rubric = []
    review_items = []

    document_types = {}
    failures = []

    # Heading scope can continue to a later page.
    # Such continuity is inferred, not independently proven.
    current_entity = None
    current_chapter = None

    for page in pages:
        pdf_page = page["pdf_page"]
        text = page.get("normalized_text", "")

        if not text.strip():
            continue

        try:
            proposals, page_types, page_reviews, window_count = cached_page_analysis(output, book, page, args)
            review_items.extend(page_reviews)
        except (RuntimeError, ValueError, OSError) as exc:
            failures.append({"pdf_page": pdf_page, "error": str(exc)})
            print(f"PDF page {pdf_page}: FAILED: {exc}", flush=True)
            # A failed page must not silently become a valid one-passage result.
            current_entity = None
            if args.fail_fast:
                raise
            continue

        document_type = next(
            (kind for kind in page_types if kind != "unknown"),
            "unknown"
        )
        # Overlapping windows can produce duplicate headings.
        unique = {}
        for heading in proposals:
            key = (heading["start_offset"], heading["end_offset"])
            if key not in unique or heading["confidence"] > unique[key]["confidence"]:
                unique[key] = heading
        accepted = sorted(unique.values(), key=lambda h: h["start_offset"])
        # Reject overlapping candidates to preserve a non-overlapping partition.
        filtered = []
        for heading in accepted:
            if filtered and heading["start_offset"] < filtered[-1]["end_offset"]:
                review_items.append({"pdf_page": pdf_page, "reason": "overlapping_heading", **heading})
            else:
                filtered.append(heading)
        accepted = filtered
        document_types[str(pdf_page)] = document_type
        segments = split_passages(text, accepted)
        if len(page_types) != window_count:
            current_entity = None
            review_items.append({"pdf_page": pdf_page, "reason": "incomplete_llm_page_review"})

        for order, segment in enumerate(segments, 1):
            start = segment["start_offset"]
            end = segment["end_offset"]
            heading = segment["heading"]

            passage_id = stable_id(
                "P", book_id, pdf_page, start, end
            )

            evidence_id = None

            if heading is not None:
                evidence_id = stable_id(
                    "H",
                    book_id,
                    pdf_page,
                    heading["start_offset"],
                    heading["text"]
                )

                heading_candidates.append({
                    "candidate_id": evidence_id,
                    "passage_id": passage_id,
                    "book_id": book_id,
                    "pdf_page": pdf_page,
                    "text": heading["text"],
                    "classification": heading["kind"],
                    "confidence": heading["confidence"],
                    "start_offset": heading["start_offset"],
                    "end_offset": heading["end_offset"],
                    "verification_status": "needs_review",
                    "extraction_method": "llm"
                })

                if heading["kind"] == "remedy":
                    entity_id = stable_id(
                        "M",
                        book_id,
                        heading["text"].casefold()
                    )

                    remedies.setdefault(entity_id, {
                        "remedy_id": entity_id,
                        "canonical_name": heading["text"],
                        "source_book_id": book_id,
                        "verification_status": "inferred",
                        "evidence_candidate_id": evidence_id
                    })

                    current_entity = (
                        "remedy", entity_id, evidence_id
                    )

                elif heading["kind"] == "rubric":
                    entity_id = stable_id(
                        "R",
                        book_id,
                        heading["text"].casefold()
                    )

                    rubrics.setdefault(entity_id, {
                        "rubric_id": entity_id,
                        "rubric_name": heading["text"],
                        "parent_rubric_id": None,
                        "verification_status": "inferred",
                        "evidence_candidate_id": evidence_id
                    })

                    current_entity = (
                        "rubric", entity_id, evidence_id
                    )

                elif heading["kind"] == "chapter":
                    current_chapter = heading["text"]
                    current_entity = None

                elif heading["kind"] == "unknown":
                    current_entity = None

                # A section heading may occur inside an entry.
                # Preserve the existing entity scope for now.

            # The LLM does not receive original-text offsets.
            # The normalized span is preserved exactly, while
            # original OCR text remains in cleaned_pages.jsonl.
            passages.append({
                "passage_id": passage_id,
                "book_id": book_id,
                "original_text": None,
                "normalized_text": segment["text"],
                "chapter": current_chapter,
                "page": None,
                "pdf_page": pdf_page,
                "source_page_start_offset": start,
                "source_page_end_offset": end,
                "source_status": "ocr_unverified",
                "verification_status": "inferred",
                "extraction_method": "llm",
                "model_name": args.model
            })

            if current_entity is not None:
                kind, entity_id, source_candidate = current_entity

                relation = {
                    "passage_id": passage_id,
                    "verification_status": "inferred",
                    "provenance_candidate_id": source_candidate,
                    "relationship_method": "llm_heading_scope"
                }

                if kind == "remedy":
                    passage_remedy.append({
                        **relation,
                        "remedy_id": entity_id
                    })

                elif kind == "rubric":
                    passage_rubric.append({
                        **relation,
                        "rubric_id": entity_id
                    })

        print(
            f"{folder.name}: PDF page {pdf_page}, "
            f"{len(accepted)} headings, "
            f"{len(segments)} passages"
        )

        if args.delay > 0:
            time.sleep(args.delay)

    if failures:
        write_json(output / "llm_failures.json", {"book_id": book_id, "failed_pages": failures})
        print(f"INCOMPLETE {folder.name}: {len(failures)} failed pages; rerun with --resume", flush=True)
        # Do not overwrite previously complete exports with partial data.
        return False
    (output / "llm_failures.json").unlink(missing_ok=True)
    write_jsonl(output / "books.jsonl", [book])
    write_jsonl(output / "passages.jsonl", passages)
    write_jsonl(
        output / "heading_candidates.jsonl",
        heading_candidates
    )
    write_jsonl(
        output / "remedies.jsonl",
        remedies.values()
    )
    write_jsonl(
        output / "rubrics.jsonl",
        rubrics.values()
    )
    write_jsonl(
        output / "passage_remedy.jsonl",
        passage_remedy
    )
    write_jsonl(
        output / "passage_rubric.jsonl",
        passage_rubric
    )
    for empty_table in (
        "rubric_remedy", "symptom_terms", "synonyms", "symptom_rubric"
    ):
        write_jsonl(output / f"{empty_table}.jsonl", [])

    report = {
        "book_id": book_id,
        "model": args.model,
        "method": "llm_assisted_structuring",
        "pages": len(pages),
        "processed_nonempty_pages": sum(bool(p.get("normalized_text", "").strip()) for p in pages),
        "failed_pages": [],
        "complete": True,
        "passages": len(passages),
        "remedy_candidates": len(remedies),
        "rubric_candidates": len(rubrics),
        "heading_candidates": len(heading_candidates),
        "passage_remedy_links": len(passage_remedy),
        "passage_rubric_links": len(passage_rubric),
        "document_types_by_page": document_types,
        "review_items": review_items,
        "limitations": [
            "All extracted relationships are inferred.",
            "Exact text matching does not prove semantic correctness.",
            "Original-text offsets are not reconstructed.",
            "Long pages are analyzed in overlapping windows; headings near boundaries may be missed.",
            "Cross-page entity scope is heuristic.",
            "Rubric hierarchy and rubric-remedy relations "
            "are not automatically established.",
            "All inferred entities and relations require review before production."
        ]
    }

    write_json(
        output / "llm_structure_report.json",
        report
    )

    print(
        f"Completed {folder.name}: "
        f"{len(passages)} passages, "
        f"{len(remedies)} remedy candidates, "
        f"{len(rubrics)} rubric candidates"
    )
    return True


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "data/processed"
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/structured_llm"
    )

    parser.add_argument(
        "--model",
        default="qwen2.5:7b"
    )

    parser.add_argument(
        "--url",
        default="http://localhost:11434"
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=180
    )

    parser.add_argument(
        "--max-chars",
        type=int,
        default=12000
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0
    )

    parser.add_argument(
        "--fail-fast",
        action="store_true"
    )

    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    args = parser.parse_args()
    if args.retries < 0:
        parser.error("--retries must be nonnegative")
    if args.max_chars < 1000:
        parser.error("--max-chars must be at least 1000")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    if not args.input.exists():
        parser.error(
            f"Input directory not found: {args.input}"
        )

    if (
        (args.input / "book.json").is_file()
        and (args.input / "cleaned_pages.jsonl").is_file()
    ):
        folders = [args.input]
    else:
        folders = sorted(
            p for p in args.input.iterdir()
            if p.is_dir()
            and (p / "book.json").is_file()
            and (p / "cleaned_pages.jsonl").is_file()
        )

    if not folders:
        parser.error(
            "No processed books found. "
            "Run ingest_books.py and clean_text.py first."
        )

    failed_books = []
    for folder in folders:
        if not process_book(folder, args):
            failed_books.append(folder.name)
    if failed_books:
        raise SystemExit(f"Incomplete books: {', '.join(failed_books)}")


if __name__ == "__main__":
    main()
