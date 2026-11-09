
#!/usr/bin/env python3
"""Create normalized-text chunks with exact offsets and coverage."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_jsonl(path):
    with path.open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def split_with_offsets(text, max_chars=900, overlap=120):
    """
    Yield [start, end) slices.

    Offsets refer to normalized_text,
    not the original OCR text.
    """
    if not text.strip():
        return

    cursor = 0
    n = len(text)

    while cursor < n:
        start = cursor

        while start < n and text[start].isspace():
            start += 1

        if start >= n:
            break

        end = min(start + max_chars, n)

        if end < n:
            search_start = (
                start + max_chars // 2
            )

            candidates = [
                text.rfind(
                    '\n\n',
                    search_start,
                    end
                ),
                text.rfind(
                    ' ',
                    search_start,
                    end
                ),
            ]

            cut = max(candidates)

            if cut > start:
                end = cut

        while (
            end > start
            and text[end - 1].isspace()
        ):
            end -= 1

        if end <= start:
            raise ValueError(
                'No progress while chunking'
            )

        yield (
            start,
            end,
            text[start:end]
        )

        if end >= n or not text[end:].strip():
            break

        # Start the next chunk inside the previous one.
        cursor = max(
            start + 1,
            end - overlap
        )


def process(folder, max_chars, overlap):
    chunks = []

    for passage in read_jsonl(
        folder / 'passages.jsonl'
    ):
        pid = passage['passage_id']

        parts = split_with_offsets(
            passage['normalized_text'],
            max_chars,
            overlap
        )

        for order, (start, end, chunk) in enumerate(
            parts, 1
        ):
            chunks.append({
                'chunk_id': f'{pid}-c{order:04d}',
                'passage_id': pid,
                'book_id': passage.get('book_id'),
                'pdf_page': passage.get('pdf_page'),
                'remedy_scope_status': passage.get('remedy_scope_status', 'unverified'),
                'chunk_text': chunk,
                'chunk_order': order,
                'start_offset': start,
                'end_offset': end,
            })

    with (
        folder / 'chunks.jsonl'
    ).open('w', encoding='utf-8') as f:
        for row in chunks:
            f.write(
                json.dumps(row, ensure_ascii=False) + '\n'
            )

    print(
        f'{folder.name}: {len(chunks)} chunks'
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--input',
        type=Path,
        default=ROOT / 'data/structured'
    )

    parser.add_argument(
        '--max-chars',
        type=int,
        default=900
    )

    parser.add_argument(
        '--overlap',
        type=int,
        default=120
    )

    args = parser.parse_args()

    if (
        args.max_chars < 50
        or not 0 <= args.overlap < args.max_chars // 2
    ):
        parser.error(
            'Require max-chars >= 50 '
            'and 0 <= overlap < max-chars/2'
        )

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

    for folder in folders:
        if (folder / 'passages.jsonl').exists():
            process(
                folder,
                args.max_chars,
                args.overlap
            )


if __name__ == '__main__':
    main()
