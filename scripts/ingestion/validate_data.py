
#!/usr/bin/env python3
"""Validate v1.4 corpus references, chunk coverage and provenance."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TABLES = (
    'books',
    'passages',
    'chunks',
    'remedies',
    'rubrics',
    'passage_remedy',
    'passage_rubric',
    'rubric_remedy',
    'symptom_terms',
    'synonyms',
    'symptom_rubric',
    'heading_candidates',
)

IDS = {
    'books': 'book_id',
    'passages': 'passage_id',
    'chunks': 'chunk_id',
    'remedies': 'remedy_id',
    'rubrics': 'rubric_id',
    'symptom_terms': 'term_id',
    'synonyms': 'synonym_id',
    'heading_candidates': 'candidate_id',
}

RELATIONS = {
    'passage_remedy': (
        ('passage_id', 'passages'),
        ('remedy_id', 'remedies'),
    ),
    'passage_rubric': (
        ('passage_id', 'passages'),
        ('rubric_id', 'rubrics'),
    ),
    'rubric_remedy': (
        ('rubric_id', 'rubrics'),
        ('remedy_id', 'remedies'),
    ),
    'symptom_rubric': (
        ('term_id', 'symptom_terms'),
        ('rubric_id', 'rubrics'),
    ),
}


def read_jsonl(path):
    with path.open(encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f'{path}:{n}: {exc}'
                    ) from exc

                if not isinstance(obj, dict):
                    raise ValueError(
                        f'{path}:{n}: expected JSON object'
                    )

                yield obj


def validate(folder, processed_root, production=False):
    errors = []
    warnings = []
    tables = {}

    # Load all structured JSONL files.
    for name in TABLES:
        path = folder / f'{name}.jsonl'

        if not path.is_file():
            errors.append(
                f'Missing {path.name}'
            )
            tables[name] = []
        else:
            try:
                tables[name] = list(
                    read_jsonl(path)
                )
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
                tables[name] = []

    # Validate unique identifiers.
    ids = {}

    for name, field in IDS.items():
        values = [
            row.get(field)
            for row in tables[name]
        ]

        counts = Counter(values)

        for value, count in counts.items():
            if (
                not isinstance(value, str)
                or not value.strip()
                or count > 1
            ):
                errors.append(
                    f'{name}: invalid/duplicate '
                    f'{field}={value!r} '
                    f'(count {count})'
                )

        ids[name] = set(values)

    # Validate book metadata and rights.
    for book in tables['books']:
        bid = book.get('book_id')

        for field in (
            'title',
            'author',
            'edition',
            'year',
            'language',
            'source_url',
            'rights_status',
        ):
            if field not in book:
                errors.append(
                    f'{bid}: missing book field {field}'
                )

        if book.get('rights_status') != 'approved':
            message = (
                f'{bid}: rights not approved; '
                'exclude from production'
            )

            if production:
                errors.append(message)
            else:
                warnings.append(message)

    # Load original cleaned pages.
    source = (
        processed_root
        / folder.name
        / 'cleaned_pages.jsonl'
    )

    if not source.is_file():
        errors.append(
            f'Missing cleaned pages: {source}'
        )
        source_pages = {}
    else:
        try:
            source_pages = {
                (p['book_id'], p['pdf_page']): p
                for p in read_jsonl(source)
            }
        except (ValueError, KeyError) as exc:
            errors.append(str(exc))
            source_pages = {}

    passages = {
        p.get('passage_id'): p
        for p in tables['passages']
    }

    # Validate Passage -> Book and source text.
    for passage in tables['passages']:
        pid = passage.get('passage_id')

        if passage.get('book_id') not in ids['books']:
            errors.append(
                f'{pid}: invalid book_id'
            )

        src = source_pages.get(
            (
                passage.get('book_id'),
                passage.get('pdf_page')
            )
        )

        if not src:
            errors.append(
                f'{pid}: missing source page'
            )

        else:
            page_text = src.get('normalized_text', '')
            start = passage.get('source_page_start_offset')
            end = passage.get('source_page_end_offset')
            if start is not None or end is not None:
                if (not isinstance(start, int) or isinstance(start, bool)
                        or not isinstance(end, int) or isinstance(end, bool)
                        or not 0 <= start < end <= len(page_text)):
                    errors.append(f'{pid}: invalid source page offsets')
                elif passage.get('normalized_text') != page_text[start:end]:
                    errors.append(f'{pid}: source span text mismatch')
                if passage.get('original_text') is not None:
                    warnings.append(f'{pid}: original_text for sliced passage not independently mapped')
            elif any(
                passage.get(field) != src.get(field)
                for field in ('original_text', 'normalized_text')
            ):
                errors.append(f'{pid}: original/normalized source mismatch')

        normalized = passage.get('normalized_text')

        if (
            not isinstance(normalized, str)
            or not normalized.strip()
        ):
            errors.append(
                f'{pid}: empty normalized text'
            )

        for field in (
            'original_text',
            'normalized_text',
            'chapter',
            'page',
        ):
            if field not in passage:
                errors.append(
                    f'{pid}: missing {field}'
                )

    # Verify that LLM passages partition each nonempty source page.
    sliced_by_page = defaultdict(list)
    for passage in tables['passages']:
        if 'source_page_start_offset' in passage or 'source_page_end_offset' in passage:
            key = (passage.get('book_id'), passage.get('pdf_page'))
            a = passage.get('source_page_start_offset')
            b = passage.get('source_page_end_offset')
            if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool):
                sliced_by_page[key].append((a, b))
    for key, spans in sliced_by_page.items():
        source_page = source_pages.get(key)
        if not source_page:
            continue
        page_text = source_page.get('normalized_text', '')
        cursor = 0
        for a, b in sorted(spans):
            if a < cursor:
                errors.append(f'{key}: overlapping passage spans')
            if page_text[cursor:a].strip():
                errors.append(f'{key}: uncovered non-whitespace page text')
            cursor = max(cursor, b)
        if page_text[cursor:].strip():
            errors.append(f'{key}: uncovered trailing page text')
    for key, source_page in source_pages.items():
        if source_page.get('normalized_text', '').strip() and key not in sliced_by_page:
            if not any(p.get('book_id') == key[0] and p.get('pdf_page') == key[1]
                       for p in tables['passages']):
                errors.append(f'{key}: nonempty source page has no passage')

    # Validate Chunk -> Passage.
    ranges = defaultdict(list)
    orders = defaultdict(list)

    for chunk in tables['chunks']:
        cid = chunk.get('chunk_id')
        pid = chunk.get('passage_id')

        passage = passages.get(pid)

        if passage is None:
            errors.append(
                f'{cid}: missing passage_id {pid}'
            )
            continue

        text = passage.get('normalized_text', '')

        a = chunk.get('start_offset')
        b = chunk.get('end_offset')

        if (
            not isinstance(a, int)
            or not isinstance(b, int)
            or not 0 <= a < b <= len(text)
        ):
            errors.append(
                f'{cid}: invalid offsets'
            )
            continue

        if text[a:b] != chunk.get('chunk_text'):
            errors.append(
                f'{cid}: chunk does not match offsets'
            )

        ranges[pid].append((a, b))

        order = chunk.get('chunk_order')

        if (
            not isinstance(order, int)
            or order < 1
        ):
            errors.append(
                f'{cid}: invalid chunk_order'
            )
        else:
            orders[pid].append(order)

    # Check coverage and ordering.
    for pid, passage in passages.items():
        text = passage.get('normalized_text', '')

        if not isinstance(text, str):
            continue

        spans = sorted(
            ranges.get(pid, [])
        )

        if not spans and text.strip():
            errors.append(
                f'{pid}: no chunks'
            )
            continue

        covered_until = 0

        for a, b in spans:
            if text[covered_until:a].strip():
                errors.append(
                    f'{pid}: uncovered non-whitespace '
                    f'at {covered_until}:{a}'
                )

            covered_until = max(
                covered_until,
                b
            )

        if text[covered_until:].strip():
            errors.append(
                f'{pid}: uncovered trailing text'
            )

        sequence = sorted(
            orders.get(pid, [])
        )

        expected = list(
            range(1, len(sequence) + 1)
        )

        if sequence != expected:
            errors.append(
                f'{pid}: non-contiguous or '
                'duplicate chunk_order'
            )

    # Validate entity relationships.
    candidate_ids = ids['heading_candidates']

    for name, fields in RELATIONS.items():
        seen = set()

        for relation in tables[name]:
            key = tuple(
                relation.get(field)
                for field, _ in fields
            )

            if key in seen:
                errors.append(
                    f'{name}: duplicate relation {key}'
                )

            seen.add(key)

            for field, target in fields:
                value = relation.get(field)

                if value not in ids[target]:
                    errors.append(
                        f'{name}: broken {field}={value!r}'
                    )

            status = relation.get(
                'verification_status'
            )

            if status not in (
                'verified',
                'inferred'
            ):
                errors.append(
                    f'{name}: invalid '
                    f'verification_status {status!r}'
                )

            if status == 'inferred':
                provenance = relation.get(
                    'provenance_candidate_id'
                )

                if provenance not in candidate_ids:
                    errors.append(
                        f'{name}: inferred relation lacks '
                        'valid heading provenance'
                    )

                if production:
                    errors.append(
                        f'{name}: inferred relation '
                        'prohibited from production '
                        'verified corpus'
                    )

    for passage in tables['passages']:
        if passage.get('remedy_scope_status') == 'ambiguous':
            warnings.append(f"{passage.get('passage_id')}: ambiguous remedy scope; manual review needed")

    # Validate rubric hierarchy.
    for rubric in tables['rubrics']:
        parent = rubric.get(
            'parent_rubric_id'
        )

        if (
            parent is not None
            and parent not in ids['rubrics']
        ):
            errors.append(
                f"rubric {rubric.get('rubric_id')}: "
                "broken parent"
            )

    # Validate synonym references.
    for synonym in tables['synonyms']:
        if (
            synonym.get('term_id')
            not in ids['symptom_terms']
        ):
            errors.append(
                f"synonym {synonym.get('synonym_id')}: "
                "broken term"
            )

    report = {
        'book_folder': folder.name,
        'production_mode': production,
        'counts': {
            name: len(rows)
            for name, rows in tables.items()
        },
        'error_count': len(errors),
        'warning_count': len(warnings),
        'errors': errors,
        'warnings': warnings,
        'notice': (
            'Passing validates structure, not OCR accuracy, '
            'printed pages, or semantic correctness.'
        ),
    }

    (
        folder / 'validation_report.json'
    ).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ),
        encoding='utf-8'
    )

    print(
        f'{folder.name}: '
        f'{len(errors)} errors, '
        f'{len(warnings)} warnings'
    )

    return bool(errors)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--input',
        type=Path,
        default=ROOT / 'data/structured'
    )

    parser.add_argument(
        '--processed',
        type=Path,
        default=ROOT / 'data/processed'
    )

    parser.add_argument(
        '--production',
        action='store_true',
        help=(
            'Fail on unapproved rights '
            'and inferred relationships'
        )
    )

    args = parser.parse_args()

    if not args.input.exists():
        parser.error(
            f'Input does not exist: {args.input}'
        )

    folders = (
        [args.input]
        if (args.input / 'passages.jsonl').exists()
        else sorted(
            p for p in args.input.iterdir()
            if p.is_dir()
        )
    )

    failed = False

    for folder in folders:
        if (folder / 'passages.jsonl').exists():
            failed = (
                validate(
                    folder,
                    args.processed,
                    args.production
                )
                or failed
            )

    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
