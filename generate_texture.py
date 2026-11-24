import argparse, csv, sys
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

TEX_SIZE = 256
EXPAND_RATIO = 2

def render_page(img: Image.Image, lines: list[str], cell_w: int, cell_h: int,
                font, scale_x: float, offset_x: int, offset_y: int):
    for r, row_str in enumerate(lines):
        for c, ch in enumerate(row_str):
            if ch in ('\u00B7', '\uFFFD', ''):
                continue
            expand_w = int(cell_w * EXPAND_RATIO)
            expand_h = int(cell_h * EXPAND_RATIO)
            tile = Image.new('RGBA', (expand_w, expand_h), (255, 255, 255, 0))
            tile_draw = ImageDraw.Draw(tile)
            ax = expand_w // 2 + offset_x * EXPAND_RATIO
            ay = expand_h // 2 + offset_y * EXPAND_RATIO
            tile_draw.text((ax, ay), ch, font=font, fill=(255, 255, 255, 255), anchor='ms')
            target_w = int(cell_w * scale_x)
            target_h = cell_h
            if target_w != expand_w or target_h != expand_h:
                tile = tile.resize((target_w, target_h), Image.Resampling.LANCZOS)
            paste_x = c * cell_w + (cell_w - target_w) // 2
            paste_y = r * cell_h + (cell_h - target_h) // 2
            img.paste(tile, (paste_x, paste_y), tile)

def main():
    parser = argparse.ArgumentParser(description='Render textures from combined layout')
    parser.add_argument('-i', '--input', required=True, help='Input combined layout .txt file')
    parser.add_argument('-o', '--output', required=True, help='Output directory for PNG files')
    parser.add_argument('--page-start', type=int, default=0, help='Starting page number (default: 0)')
    parser.add_argument('--cell-w', type=int, default=18, help='Cell width in pixels (default: 18)')
    parser.add_argument('--cell-h', type=int, default=22, help='Cell height in pixels (default: 22)')
    parser.add_argument('--font', required=True, help='Path to TTF font file')
    parser.add_argument('--font-size', type=int, required=True, help='Font size in pixels')
    parser.add_argument('--scale-x', type=float, default=1.0, help='Horizontal scale factor (default: 1.0)')
    parser.add_argument('--offset-x', type=int, default=0, help='Global horizontal offset in pixels (default: 0)')
    parser.add_argument('--offset-y', type=int, default=0, help='Global vertical offset in pixels (default: 0)')
    parser.add_argument('--shrink-pages', default='', help='Comma-separated page ranges (e.g. 2-13,17-32) to replace with 32x32 blank placeholders')
    args = parser.parse_args()

    layout_path = Path(args.input)
    font_path = Path(args.font)
    out_dir = Path(args.output)

    if not layout_path.exists():
        print(f'ERROR: {layout_path} not found', file=sys.stderr)
        return 1
    if not font_path.exists():
        print(f'ERROR: {font_path} not found', file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    with layout_path.open('r', encoding='utf-8') as f:
        content = f.read()

    pages = content.split('\n\n')
    font = ImageFont.truetype(str(font_path), args.font_size)

    page_specs: dict[int, tuple[int, int, int, int]] = {}
    for pi, page_text in enumerate(pages):
        lines = [line for line in page_text.split('\n') if line]
        page_num = args.page_start + pi
        has_glyphs = any(ch not in ('\u00B7', '\uFFFD', '') for line in lines for ch in line)
        if not has_glyphs:
            # 空页写 32x32 透明占位（data_fmt=0x10，仅 544B），避免 MRG 头部出现 0xFFFF 空洞截断后续页
            img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
            page_specs[page_num] = (32, 32, 2, 0x10)
        else:
            img = Image.new('RGBA', (TEX_SIZE, TEX_SIZE), (255, 255, 255, 0))
            render_page(img, lines, args.cell_w, args.cell_h, font,
                        args.scale_x, args.offset_x, args.offset_y)
            page_specs[page_num] = (TEX_SIZE, TEX_SIZE, 2, 3)
        out_path = out_dir / f'{page_num:03d}.png'
        img.save(out_path)
        print(f'  {out_path}', file=sys.stderr)

    if args.shrink_pages:
        blank_32 = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        for part in args.shrink_pages.split(','):
            part = part.strip()
            if not part:
                continue
            lo, hi = (int(x) for x in part.split('-')) if '-' in part else (int(part), int(part))
            for idx in range(lo, hi + 1):
                blank_32.save(out_dir / f'{idx:03d}.png')
                page_specs[idx] = (32, 32, 2, 0x10)

    meta_csv = out_dir / '_meta.csv'
    if meta_csv.exists():
        with meta_csv.open('r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        by_idx = {int(r['idx']): r for r in rows}
        for page_num, (w, h, fmt, dfmt) in page_specs.items():
            by_idx[page_num] = {
                'idx': str(page_num),
                'width': str(w),
                'height': str(h),
                'fmt': str(fmt),
                'data_fmt': str(dfmt),
            }
        sorted_rows = [by_idx[k] for k in sorted(by_idx)]
        with meta_csv.open('w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['idx', 'width', 'height', 'fmt', 'data_fmt'])
            w.writeheader()
            w.writerows(sorted_rows)

    print(f'{len(pages)} pages -> {out_dir}/', file=sys.stderr)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
