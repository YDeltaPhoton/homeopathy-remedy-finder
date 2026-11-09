
#!/usr/bin/env python3
"""Conservative OCR cleanup while retaining unmodified original text."""

import argparse
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BREAK = re.compile(
    r'([^\W\d_]{2,})-\n([^\W\d_]{2,})',
    re.UNICODE
)


def read_jsonl(path):
    with path.open(encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f'{path}:{n}: {e}'
                    ) from e


def clean(text):
    text = unicodedata.normalize(
        'NFC',
        text.replace('\r\n', '\n').replace('\r', '\n')
    )

    text = text.replace('\u00a0', ' ')
    text = text.replace('\u00ad', '')

    text = re.sub(
        r'[ \t]+$',
        '',
        text,
        flags=re.M
    )

    text = re.sub(
        r'[ \t]{2,}',
        ' ',
        text
    )

    return re.sub(
        r'\n{3,}',
        '\n\n',
        text
    ).strip()


def process(folder):
    source = folder / 'pages.jsonl'

    rows = []
    candidates = []

    for page in read_jsonl(source):
        original = page.get(
            'original_text',
            page.get('text', '')
        )

        normalized = clean(original)

        row = dict(
            page,
            original_text=original,
            normalized_text=normalized,
            text=normalized
        )

        rows.append(row)

        for match in BREAK.finditer(normalized):
            candidates.append({
                'pdf_page': page['pdf_page'],
                'original': match.group(),
                'suggested_join': (
                    match.group(1) + match.group(2)
                ),
                'status': 'review_required',
            })

    with (
        folder / 'cleaned_pages.jsonl'
    ).open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(
                json.dumps(row, ensure_ascii=False) + '\n'
            )

    (
        folder / 'hyphen_candidates.json'
    ).write_text(
        json.dumps(
            candidates,
            ensure_ascii=False,
            indent=2
        ),
        encoding='utf-8'
    )

    report = {
        'pages': len(rows),
        'hyphen_candidates': len(candidates),
        'original_characters': sum(
            len(r['original_text']) for r in rows
        ),
        'normalized_characters': sum(
            len(r['normalized_text']) for r in rows
        ),
        'empty_normalized_pages': [
            r['pdf_page']
            for r in rows
            if not r['normalized_text']
        ],
    }

    (
        folder / 'clean_report.json'
    ).write_text(
        json.dumps(report, indent=2),
        encoding='utf-8'
    )

    print(
        f'{folder.name}: cleaned {len(rows)} pages'
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--input',
        type=Path,
        default=ROOT / 'data/processed'
    )

    args = parser.parse_args()

    folders = (
        [args.input]
        if (args.input / 'pages.jsonl').exists()
        else sorted(
            p for p in args.input.iterdir()
            if p.is_dir()
        )
    )

    for folder in folders:
        if (folder / 'pages.jsonl').exists():
            process(folder)


if __name__ == '__main__':
    main()
