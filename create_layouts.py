import argparse
import csv
import json
from pathlib import Path

BASE_18X22_START = 57


def main() -> None:
    parser = argparse.ArgumentParser(description='Create texture layouts from mapping CSV')
    parser.add_argument('-m', '--mapping', required=True, help='Input mapping CSV')
    parser.add_argument('-o', '--output', required=True, help='Output directory for layout files')
    args = parser.parse_args()

    mapping_path = Path(args.mapping)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    with mapping_path.open('r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    max_used = 0
    for idx, entry in enumerate(rows):
        ch = entry['char'] if entry['char'] else entry['target_jp']
        if ch and ch != '\uFFFD':
            max_used = idx
    active_slots = max_used + 1

    pages_18 = (active_slots + 153) // 154
    pages_20 = (active_slots + 119) // 120
    start_18 = BASE_18X22_START
    start_20 = start_18 + pages_18

    tex_configs = [
        (start_18, start_18 + pages_18 - 1, 14, 11, 18, 22, '18x22'),
        (start_20, start_20 + pages_20 - 1, 12, 10, 20, 24, '20x24'),
        (14, 16, 14, 11, 18, 22, '18x22_face'),
        (33, 36, 12, 10, 20, 24, '20x24_face'),
    ]

    for tex_start, tex_end, cols, num_rows, _, _, name in tex_configs:
        cells_per_page = cols * num_rows
        all_pages: list[str] = []
        for tex_idx in range(tex_start, tex_end + 1):
            page = tex_idx - tex_start
            start_idx = page * cells_per_page
            page_lines: list[str] = []
            for cell in range(cells_per_page):
                idx = start_idx + cell
                if idx >= len(rows):
                    break
                entry = rows[idx]
                if name.endswith('_face') and entry.get('is_face') != '1':
                    ch = '\u00B7'
                else:
                    ch = entry['char'] if entry['char'] else entry['target_jp']
                    if not ch or ch == '\uFFFD':
                        ch = '\u00B7'
                c = cell % cols
                if c == 0:
                    page_lines.append('')
                page_lines[-1] += ch
            all_pages.append('\n'.join(page_lines))
        out_path = out_dir / f'{name}.txt'
        with out_path.open('w', encoding='utf-8') as f:
            f.write('\n\n'.join(all_pages))
        print(f'  {out_path} ({tex_end - tex_start + 1} pages, {tex_start}..{tex_end})')

    meta = {
        'active_slots': active_slots,
        'page_18x22': start_18,
        'pages_18x22': pages_18,
        'page_20x24': start_20,
        'pages_20x24': pages_20,
    }
    (out_dir / 'layout_meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
