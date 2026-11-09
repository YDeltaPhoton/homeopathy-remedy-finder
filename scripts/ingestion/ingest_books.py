#!/usr/bin/env python3
"""Page-aware PDF ingestion, preserving OCR sidecar TXT and PDF."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def write_jsonl(path, rows):
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def pdf_text_pages(pdf):
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return [page.get_text('text', sort=True) for page in doc]


def suspicious(text):
    if len(text.strip()) < 80:
        return True
    letters = sum(char.isalpha() for char in text)
    return (letters / len(text) < 0.35 or
            text.count('\ufffd') / len(text) > 0.005)


def parse_sidecar(path, expected_pages):
    """Require one form-feed-delimited text segment per PDF page."""
    content = path.read_text(encoding='utf-8-sig')
    parts = content.split('\f')
    if parts and not parts[-1].strip() and len(parts) == expected_pages + 1:
        parts.pop()
    if len(parts) != expected_pages:
        raise ValueError(
            f'OCR TXT page mismatch: PDF has {expected_pages} pages, '
            f'but TXT has {len(parts)} segments. Refusing to guess page numbers. '
            'This can occur with skipped text-layer pages or OCR failures.'
        )
    return parts


def save_text_pages(path, pages):
    # Include a trailing form-feed so even empty final pages remain representable.
    path.write_text(''.join(page + '\f' for page in pages), encoding='utf-8')


def run_ocr(source, stage, language, mode, original_pages):
    """Return (pages, layout_pdf, ocr_executed, text_source, warnings)."""
    txt_path = stage / 'ocr.txt'
    pdf_path = stage / 'ocr.pdf'
    warnings = []

    if mode == 'never':
        # Deliberately not direct OCR; preserve a consistent TXT/JSONL interface.
        shutil.copy2(source, pdf_path)
        save_text_pages(txt_path, original_pages)
        warnings.append('OCR disabled: TXT was extracted from the existing PDF text layer')
        return original_pages, pdf_path, False, 'pdf_text_layer', warnings

    if mode == 'auto' and all(not suspicious(p) for p in original_pages):
        shutil.copy2(source, pdf_path)
        save_text_pages(txt_path, original_pages)
        warnings.append('Existing text layer accepted: TXT was extracted using PyMuPDF, not OCR sidecar')
        return original_pages, pdf_path, False, 'pdf_text_layer', warnings

    if not shutil.which('ocrmypdf'):
        raise RuntimeError('ocrmypdf is not installed. Install with: brew install ocrmypdf')

    has_text_layer = any(p.strip() for p in original_pages)
    if mode == 'auto' and has_text_layer:
        raise RuntimeError(
            'PDF has an existing or partial text layer, but some pages appear '
            'unreliable. To avoid an incomplete --skip-text sidecar, automatic '
            'mode stopped. For a scanned book, rerun with --ocr always to '
            'force OCR on all pages (review quality afterward).'
        )

    command = ['ocrmypdf', '--deskew', '-l', language,
               '--sidecar', str(txt_path)]
    if mode == 'always':
        command.append('--force-ocr')
    command.extend([str(source), str(pdf_path)])
    subprocess.run(command, check=True)
    if not txt_path.is_file() or not pdf_path.is_file():
        raise RuntimeError('OCRmyPDF did not produce both ocr.txt and ocr.pdf')
    pages = parse_sidecar(txt_path, len(original_pages))
    warnings.append('OCR text and reading order require manual review')
    if mode == 'always':
        warnings.append('Forced OCR rasterizes existing text; review text quality')
    return pages, pdf_path, True, 'ocr_sidecar', warnings


def metadata_for(pdf, manifest):
    item = manifest.get(pdf.name, {})
    if not isinstance(item, dict):
        raise ValueError(f'Manifest entry for {pdf.name} must be an object')
    metadata = {key: item.get(key) for key in
                ('title', 'author', 'edition', 'year', 'source_url')}
    return metadata, item.get('rights_status', 'unreviewed'), item


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()[:16]


def process(pdf, out, lang, mode, manifest, overwrite):
    import pymupdf
    from detect_layout import extract_layout

    pdf = pdf.resolve()
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    book_id = sha256_file(pdf)
    dest = out / f'{pdf.stem}-{book_id[:8]}'
    managed_files = ('ocr.pdf', 'ocr.txt', 'pages.jsonl', 'book.json', 'report.json')
    if dest.exists() and any((dest / name).exists() for name in managed_files) and not overwrite:
        raise FileExistsError(
            f'Output already exists: {dest}. Existing files were preserved. '
            'Use --overwrite only if you intend to regenerate ingestion outputs.'
        )
    out.mkdir(parents=True, exist_ok=True)
    original_pages = pdf_text_pages(pdf)
    if not original_pages:
        raise ValueError(f'PDF has no pages: {pdf}')

    with tempfile.TemporaryDirectory(prefix='.ingest-', dir=out) as temporary:
        stage = Path(temporary)
        pages, layout_pdf, ocr_used, text_source, warnings = run_ocr(
            pdf, stage, lang, mode, original_pages
        )
        with pymupdf.open(layout_pdf) as doc:
            if len(doc) != len(pages):
                raise ValueError('OCR PDF page count does not match TXT page count')
            layouts = [extract_layout(page) for page in doc]
        # Text blocks come from the PDF layer and may differ from the sidecar.
        # Do not use their offsets to index sidecar text.
        if text_source == 'ocr_sidecar':
            for layout in layouts:
                layout['text_source'] = 'ocr_sidecar'
                layout['blocks_text_offsets_compatible'] = False
        metadata, rights, item = metadata_for(pdf, manifest)
        language = item.get('language') or {
            'eng': 'en', 'deu': 'de', 'eng+deu': 'en+de'
        }.get(lang, lang)
        book = {
            'book_id': book_id, **metadata, 'language': language,
            'rights_status': rights, 'source_pdf': pdf.name
        }
        write_json(stage / 'book.json', book)
        write_jsonl(stage / 'pages.jsonl', [
            {
                'book_id': book_id,
                'source_pdf': pdf.name,
                'pdf_page': number,
                'original_text': text,
                'text': text,
                'layout': layouts[number - 1],
            }
            for number, text in enumerate(pages, 1)
        ])
        report = {
            'book_id': book_id,
            'pages': len(pages),
            'ocr_executed': ocr_used,
            'text_source': text_source,
            'ocr_txt_saved': True,
            'ocr_pdf_saved': True,
            'possible_spread_pages': [
                i for i, layout in enumerate(layouts, 1)
                if layout['layout_type'] == 'possible_spread'
            ],
            'empty_pages': [i for i, text in enumerate(pages, 1) if not text.strip()],
            'short_text_pages': [
                i for i, text in enumerate(pages, 1) if len(text.strip()) < 80
            ],
            'rights_status': rights,
            'warnings': [
                'PDF page numbers are not necessarily printed page numbers',
                'Layout text blocks are only heuristic and may not match OCR TXT',
                'Unreviewed rights are not production approval',
                *warnings,
            ],
        }
        write_json(stage / 'report.json', report)

        dest.mkdir(parents=True, exist_ok=True)
        for name in managed_files:
            (stage / name).replace(dest / name)

    print(f'{pdf.name} -> {dest} ({len(pages)} pages, source={text_source})')
    if overwrite:
        print('NOTE: Regenerate cleaned_pages.jsonl and downstream structured data; old outputs may be stale.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', nargs='?', type=Path, default=ROOT / 'data/raw')
    parser.add_argument('--out', type=Path, default=ROOT / 'data/processed')
    parser.add_argument('--lang', default='eng', help='eng, deu, or eng+deu')
    parser.add_argument('--ocr', choices=['auto', 'always', 'never'], default='auto')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--overwrite', action='store_true',
                        help='Regenerate existing ingestion outputs; downstream files need rerunning')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8')) if args.manifest else {}
    paths = sorted(args.input.glob('*.pdf')) if args.input.is_dir() else [args.input]
    if not paths:
        parser.error('No PDF files found')
    for pdf in paths:
        process(pdf, args.out, args.lang, args.ocr, manifest, args.overwrite)


if __name__ == '__main__':
    main()
