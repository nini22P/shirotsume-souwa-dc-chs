import argparse, csv
from pathlib import Path

TEX_CONFIGS = [
    (2, 13, 14, 11, 18, 22, '18x22'),
    (17, 32, 12, 10, 20, 24, '20x24'),
    (14, 16, 14, 11, 18, 22, '18x22_face'),
    (33, 36, 12, 10, 20, 24, '20x24_face'),
]

def main():
    parser = argparse.ArgumentParser(description='Create texture layouts from mapping CSV')
    parser.add_argument('-m', '--mapping', required=True, help='Input mapping CSV')
    parser.add_argument('-o', '--output', required=True, help='Output directory for layout files')
    args = parser.parse_args()

    mapping_path = Path(args.mapping)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    with mapping_path.open('r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    for tex_start, tex_end, cols, num_rows, cell_w, cell_h, name in TEX_CONFIGS:
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
                ch = entry['char'] if entry['char'] else entry['target_jp']
                if ch == '\uFFFD':
                    ch = '\u00B7'
                c = cell % cols
                if c == 0:
                    page_lines.append('')
                page_lines[-1] += ch
            all_pages.append('\n'.join(page_lines))
        out_path = out_dir / f'{name}.txt'
        with out_path.open('w', encoding='utf-8') as f:
            f.write('\n\n'.join(all_pages))
        print(f'  {out_path} ({tex_end-tex_start+1} pages)')

if __name__ == '__main__':
    main()
