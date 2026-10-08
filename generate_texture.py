import argparse, sys
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
            tile = Image.new('RGBA', (expand_w, expand_h), (0, 0, 0, 0))
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

    for pi, page_text in enumerate(pages):
        lines = [line for line in page_text.split('\n') if line]
        if not lines:
            continue
        page_num = args.page_start + pi
        img = Image.new('RGBA', (TEX_SIZE, TEX_SIZE), (0, 0, 0, 0))
        render_page(img, lines, args.cell_w, args.cell_h, font,
                    args.scale_x, args.offset_x, args.offset_y)
        out_path = out_dir / f'{page_num:03d}.png'
        img.save(out_path)
        print(f'  {out_path}', file=sys.stderr)

    print(f'{len(pages)} pages -> {out_dir}/', file=sys.stderr)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
