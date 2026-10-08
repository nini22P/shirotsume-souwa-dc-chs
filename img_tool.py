import argparse, os, struct
from PIL import Image
from mrg import SECTOR, read_hed_entries, parse_nam, encode_hed_entry, build_nam


FMT_NAMES = {
    0x01: 'RGB565',
    0x02: 'RGB565',
    0x11: 'ARGB1555',
    0x12: 'ARGB1555',
    0x41: 'ARGB4444',
    0x42: 'ARGB4444',
    0x43: 'ARGB4444 bytepacked',
}


def _hed_path(mrg_path):
    root, _ = os.path.splitext(mrg_path)
    return root + '.HED'


def _nam_path(mrg_path):
    root, _ = os.path.splitext(mrg_path)
    return root + '.NAM'


def pack_pixel(argb, fmt):
    a = (argb >> 24) & 0xFF
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF
    if fmt in (0x01, 0x02):
        raw = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    elif fmt in (0x11, 0x12):
        raw = ((a >> 7) << 15) | ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
    else:  # 0x41, 0x42
        raw = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
    # decode does xchg al,ah (bswap16) on stream LE word, so write pre-bswapped as LE
    swapped = ((raw >> 8) & 0xFF) | ((raw & 0xFF) << 8)
    return struct.pack('<H', swapped)


def reblock(pixels, w, h, w_buf):
    unpacked = [0] * (w_buf * 64)
    buf = 0
    remain = w
    while remain > 0:
        block = min(64, remain)
        for row in range(h):
            x = w - remain
            for c in range(block):
                unpacked[buf + c] = pixels[row * w + x + c]
            buf += 64
        remain -= block
        buf += ((-h) & 63) * 64
    return unpacked


def pack_stream(unpacked, fmt):
    if fmt == 0x43:
        return encode_bytepacked(unpacked)
    out = bytearray()
    i = 0
    n = len(unpacked)
    while i < n:
        cnt = min(64, n - i)
        out.append(((cnt - 1) << 2) | 3)
        for k in range(cnt):
            out.extend(pack_pixel(unpacked[i + k], fmt))
        i += cnt
    return bytes(out)


def encode_bytepacked(pixels):
    out = bytearray()
    L2 = 0
    for pixel in pixels:
        a = (pixel >> 24) & 0xFF
        r = (pixel >> 16) & 0xFF
        g = (pixel >> 8) & 0xFF
        b = pixel & 0xFF
        nA = a >> 4
        nR = r >> 4
        nG = g >> 4
        nB = b >> 4
        ah = (L2 >> 8) & 0xFF
        if (ah + 0x10) >= 0x100:
            out.append((nG << 4) | nR)
            L2 = 0
        else:
            out.append((nG << 4) | nR)
            out.append((nB << 4) | nA)
            L2 = (0xF0 | ((nB << 4) | nA)) << 8
    return bytes(out)


def _img_header(data):
    return (
        struct.unpack_from('>H', data, 4)[0],
        struct.unpack_from('>H', data, 8)[0],
        struct.unpack_from('>H', data, 10)[0],
        data[1],
    )


def _png_to_argb(img):
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    w, h = img.size
    raw = img.tobytes()
    pixels = [0] * (w * h)
    for i in range(w * h):
        offset = i * 4
        r, g, b, a = raw[offset], raw[offset + 1], raw[offset + 2], raw[offset + 3]
        pixels[i] = (a << 24) | (r << 16) | (g << 8) | b
    return pixels


def _check_channels(png_mode, out_fmt):
    warnings = []
    has_alpha = (png_mode == 'RGBA')
    if has_alpha and out_fmt in (0x01, 0x02):
        warnings.append('RGBA → RGB565: alpha discarded')
    if not has_alpha and out_fmt in (0x11, 0x12, 0x41, 0x42, 0x43):
        warnings.append('RGB → ARGB: alpha filled 0xFF')
    return warnings


def _scan_replace_dir(replace_dir):
    mapping = {}
    if not os.path.isdir(replace_dir):
        return mapping
    for e in os.scandir(replace_dir):
        if not e.is_file():
            continue
        base, ext = os.path.splitext(e.name)
        if ext.lower() != '.png':
            continue
        mapping[base] = e.path
    return mapping


def replace(orig_mrg, replace_dir, out_mrg, dry_run=False):
    hed_path = _hed_path(orig_mrg)
    nam_path = _nam_path(orig_mrg)
    entries = read_hed_entries(hed_path)
    names = None
    nam_raw = None
    if os.path.exists(nam_path):
        with open(nam_path, 'rb') as f:
            nam_raw = f.read()
        names = parse_nam(nam_raw, len(entries))
    with open(orig_mrg, 'rb') as f:
        mrg = f.read()

    replacements = _scan_replace_dir(replace_dir)

    plans = []
    for i, (offset, size) in enumerate(entries):
        name = names[i] if names and names[i] else f'{i:04d}'
        safe = name.replace('/', '_').replace('\\', '_')
        matched_path = replacements.get(safe) or replacements.get(name)
        if matched_path is None:
            for fmt_key in (f'{i:07d}', f'${i:07d}'):
                if fmt_key in replacements:
                    matched_path = replacements[fmt_key]
                    break
        plans.append((i, name, matched_path, offset, size, matched_path is not None))

    if dry_run:
        print(f'=== DRY RUN: {len(entries)} entries, {sum(1 for p in plans if p[5])} to replace ===\n')
        for i, name, replace_path, offset, size, do_replace in plans:
            if not do_replace:
                continue
            data = mrg[offset:offset + 13]
            w_buf, w, h, fmt = _img_header(data)
            print(f'  [{i:4d}] {name}  fmt=0x{fmt:02x}({FMT_NAMES[fmt]})  {w}x{h}  orig_sz={size}')
        return

    out_hed = bytearray()
    offset = 0
    with open(out_mrg, 'wb') as mrg_out:
        for i, name, replace_path, orig_off, orig_sz, do_replace in plans:
            if do_replace:
                data = mrg[orig_off:orig_off + 13]
                w_buf, w, h, fmt = _img_header(data)
                print(f'  [{i:4d}] {name}: fmt 0x{fmt:02x} ({FMT_NAMES[fmt]}) {w}x{h}')
                img = Image.open(replace_path)
                if img.size != (w, h):
                    raise SystemExit(f'ERROR: {replace_path} size {img.size} != required {(w, h)}')
                src_mode = img.mode
                pixels = _png_to_argb(img)
                warns = _check_channels(src_mode, fmt)
                for msg in warns:
                    print(f'    WARN: {msg}')
                unpacked = reblock(pixels, w, h, w_buf)
                stream = pack_stream(unpacked, fmt)
                payload = data + stream
            else:
                payload = mrg[orig_off:orig_off + orig_sz]
            out_hed += encode_hed_entry(offset, len(payload))
            mrg_out.write(payload)
            offset += len(payload)
            pad = (SECTOR - (offset % SECTOR)) % SECTOR
            if pad:
                # padding: 0x0c at every 16-aligned abs file position, rest 0x00
                pad_buf = bytearray(pad)
                rel = (-offset) % 16
                while rel < pad:
                    pad_buf[rel] = 0x0C
                    rel += 16
                mrg_out.write(bytes(pad_buf))
                offset += pad

    out_hed += b'\xFF' * 16
    with open(_hed_path(out_mrg), 'wb') as f:
        f.write(out_hed)
    nam_out = None
    if nam_raw is not None:
        nam_out = nam_raw
    elif names is not None:
        nam_out = build_nam(names)
    if nam_out is not None:
        with open(_nam_path(out_mrg), 'wb') as f:
            f.write(nam_out)
    print(f'\n{len(entries)} files packed -> {out_mrg}')


def main():
    p = argparse.ArgumentParser(description='MRG image tool')
    sub = p.add_subparsers(dest='command', required=True)

    p_replace = sub.add_parser('replace', help='Replace images in MRG with PNGs')
    p_replace.add_argument('-i', '--input', required=True, help='Original .mrg file')
    p_replace.add_argument('-d', '--dir', required=True, help='PNG replace directory')
    p_replace.add_argument('-o', '--output', required=True, help='Output .mrg file')
    p_replace.add_argument('--dry-run', action='store_true', help='Preview only, no file output')

    args = p.parse_args()
    
    match args.command:
        case 'replace':
            replace(args.input, args.dir, args.output, dry_run=args.dry_run)
        case _:
            p.print_help()


if __name__ == '__main__':
    main()