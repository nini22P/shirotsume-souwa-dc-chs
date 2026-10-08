from __future__ import annotations
import argparse, struct, re

import numpy as np
from numba import njit, prange
from pathlib import Path
from PIL import Image

from mrg import SECTOR


@njit(cache=True)
def expand4(v: int) -> int:
    return (v << 4) | v

@njit(cache=True)
def expand5(v: int) -> int:
    return (v << 3) | (v >> 2)

@njit(cache=True)
def expand6(v: int) -> int:
    return (v << 2) | (v >> 4)


@njit(cache=True)
def decode_color(value: int, fmt: int) -> tuple[int, int, int, int]:
    if fmt == 0x01:
        r = expand5((value >> 11) & 0x1f)
        g = expand6((value >> 5) & 0x3f)
        b = expand5(value & 0x1f)
        return (r, g, b, 255)
    if fmt == 0x02:
        a = expand4((value >> 12) & 0x0f)
        r = expand4((value >> 8) & 0x0f)
        g = expand4((value >> 4) & 0x0f)
        b = expand4(value & 0x0f)
        return (r, g, b, a)
    raise ValueError('unsupported pixel format')


@njit(cache=True)
def encode_color(r: int, g: int, b: int, a: int, fmt: int) -> int:
    if fmt == 0x01:
        return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    if fmt == 0x02:
        return ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
    raise ValueError('unsupported pixel format')


@njit(cache=True)
def untwiddle(i: int) -> tuple[int, int]:
    x = y = bit = 0
    while i:
        x |= (i & 1) << bit
        i >>= 1
        y |= (i & 1) << bit
        i >>= 1
        bit += 1
    return x, y


@njit(cache=True)
def twiddle(bx: int, by: int, bits: int = 7) -> int:
    idx = 0
    for i in range(bits):
        idx |= ((bx >> i) & 1) << (2 * i)
        idx |= ((by >> i) & 1) << (2 * i + 1)
    return idx


@njit(cache=True)
def _njit_decode_vq(payload_arr: np.ndarray, fmt: int, data_fmt: int, width: int, height: int, codebook_size: int) -> np.ndarray:
    blocks_w = width // 2
    blocks_h = height // 2
    indices = payload_arr[codebook_size:]

    n_codebook = codebook_size // 8
    codebook = np.zeros((n_codebook, 4, 4), dtype=np.uint8)

    for i in range(n_codebook):
        off = i * 8
        for j in range(4):
            val = payload_arr[off + j*2] | (payload_arr[off + j*2 + 1] << 8)
            r, g, b, a = decode_color(val, fmt)
            codebook[i, j, 0] = r
            codebook[i, j, 1] = g
            codebook[i, j, 2] = b
            codebook[i, j, 3] = a

    rgba = np.zeros(width * height * 4, dtype=np.uint8)
    for i in range(len(indices)):
        code_idx = indices[i]
        bx, by = untwiddle(i)
        if bx >= blocks_w or by >= blocks_h:
            continue
        
        for py in range(2):
            for px in range(2):
                dst = ((by * 2 + py) * width + bx * 2 + px) * 4
                c_idx = py * 2 + px
                rgba[dst] = codebook[code_idx, c_idx, 0]
                rgba[dst + 1] = codebook[code_idx, c_idx, 1]
                rgba[dst + 2] = codebook[code_idx, c_idx, 2]
                rgba[dst + 3] = codebook[code_idx, c_idx, 3]
    return rgba


def decode_vq(payload: bytes, fmt: int, data_fmt: int, width: int, height: int) -> bytes:
    if data_fmt == 0x03:
        codebook_size = 0x800
    elif data_fmt == 0x10:
        codebook_size = width * height // 4
    else:
        raise ValueError('unsupported data format')
    
    payload_arr = np.frombuffer(payload, dtype=np.uint8)
    rgba = _njit_decode_vq(payload_arr, fmt, data_fmt, width, height, codebook_size)
    return rgba.tobytes()


@njit(cache=True, parallel=True)
def _njit_kmeans(blocks: np.ndarray, codebook: np.ndarray, n_blocks: int, n_codebook: int,
                  weights: np.ndarray) -> np.ndarray:
    b_flat = blocks.reshape(n_blocks, 16)
    
    for _ in range(30):
        c_flat = codebook.reshape(n_codebook, 16)
        labels = np.zeros(n_blocks, dtype=np.uint32)
        
        for i in prange(n_blocks):
            min_dist = 0x7FFFFFFF
            best_idx = 0
            for j in range(n_codebook):
                dist = 0
                for k in range(16):
                    d = b_flat[i, k] - c_flat[j, k]
                    dist += d * d * weights[k]
                if dist < min_dist:
                    min_dist = dist
                    best_idx = j
            labels[i] = best_idx
            
        counts = np.zeros(n_codebook, dtype=np.int32)
        sums = np.zeros((n_codebook, 16), dtype=np.int64)
        for i in range(n_blocks):
            lbl = labels[i]
            counts[lbl] += 1
            for k in range(16):
                sums[lbl, k] += b_flat[i, k]
                
        for j in range(n_codebook):
            if counts[j] > 0:
                for k in range(16):
                    c_flat[j, k] = sums[j, k] // counts[j]

    c_flat = codebook.reshape(n_codebook, 16)
    best_c = np.zeros(n_blocks, dtype=np.uint8)
    for i in prange(n_blocks):
        min_dist = 0x7FFFFFFF
        best_idx = 0
        for j in range(n_codebook):
            dist = 0
            for k in range(16):
                d = b_flat[i, k] - c_flat[j, k]
                dist += d * d * weights[k]
            if dist < min_dist:
                min_dist = dist
                best_idx = j
        best_c[i] = best_idx
        
    return best_c


@njit(cache=True)
def _njit_kmeans_init(blocks: np.ndarray, n_codebook: int) -> np.ndarray:
    n_blocks = blocks.shape[0]
    codebook = np.zeros((n_codebook, 4, 4), dtype=np.int32)
    used = np.zeros(n_blocks, dtype=np.uint8)

    best_first = 0
    best_var = -1.0
    for i in range(n_blocks):
        a_sum = 0.0
        for p in range(4):
            a_sum += blocks[i, p, 3]
        a_mean = a_sum / 4.0
        var = 0.0
        for p in range(4):
            d = blocks[i, p, 3] - a_mean
            var += d * d
        if var > best_var:
            best_var = var
            best_first = i
    used[best_first] = 1
    for c in range(4):
        for d in range(4):
            codebook[0, c, d] = blocks[best_first, c, d]

    sample_step = max(1, n_blocks // 2048)
    sample_n = min(2048, n_blocks)

    for ci in range(1, n_codebook):
        max_dist = -1.0
        best_idx = 0
        for si in range(sample_n):
            i = si * sample_step
            if i >= n_blocks:
                break
            if used[i]:
                continue
            min_d = 1e30
            for cj in range(ci):
                dist = 0.0
                for c in range(4):
                    for d in range(4):
                        diff = float(blocks[i, c, d]) - float(codebook[cj, c, d])
                        dist += diff * diff
                if dist < min_d:
                    min_d = dist
            if min_d > max_dist:
                max_dist = min_d
                best_idx = i
        used[best_idx] = 1
        for c in range(4):
            for d in range(4):
                codebook[ci, c, d] = blocks[best_idx, c, d]

    return codebook


@njit(cache=True)
def _njit_encode_vq(rgba: np.ndarray, fmt: int, data_fmt: int, width: int, height: int, n_codebook: int) -> np.ndarray:
    blocks_w = width // 2
    blocks_h = height // 2
    n_blocks = blocks_w * blocks_h
    
    blocks = np.zeros((n_blocks, 4, 4), dtype=np.int32)
    idx = 0
    for by in range(blocks_h):
        for bx in range(blocks_w):
            for py in range(2):
                for px in range(2):
                    dst = ((by * 2 + py) * width + bx * 2 + px) * 4
                    c_idx = py * 2 + px
                    blocks[idx, c_idx, 0] = rgba[dst]
                    blocks[idx, c_idx, 1] = rgba[dst + 1]
                    blocks[idx, c_idx, 2] = rgba[dst + 2]
                    blocks[idx, c_idx, 3] = rgba[dst + 3]
            idx += 1
    
    weights = np.zeros(16, dtype=np.float64)
    for p in range(4):
        base = p * 4
        weights[base] = 0.3      # R
        weights[base + 1] = 0.59  # G
        weights[base + 2] = 0.11  # B
        weights[base + 3] = 4.0   # A (4x for font edge quality)
        
    if n_codebook >= n_blocks:
        codebook = np.zeros((n_codebook, 4, 4), dtype=np.int32)
        for i in range(n_blocks):
            for c in range(4):
                for d in range(4):
                    codebook[i, c, d] = blocks[i, c, d]
        if n_codebook > n_blocks:
            for i in range(n_codebook - n_blocks):
                for c in range(4):
                    for d in range(4):
                        codebook[n_blocks + i, c, d] = blocks[i, c, d]
        best_c = np.arange(n_blocks, dtype=np.uint8)
    else:
        codebook = _njit_kmeans_init(blocks, n_codebook)
        best_c = _njit_kmeans(blocks, codebook, n_blocks, n_codebook, weights)
        
    payload_size = n_codebook * 8 + n_blocks
    payload = np.zeros(payload_size, dtype=np.uint8)
    
    for i in range(n_codebook):
        for j in range(4):
            r = codebook[i, j, 0]
            g = codebook[i, j, 1]
            b = codebook[i, j, 2]
            a = codebook[i, j, 3]
            v = encode_color(r, g, b, a, fmt)
            off = i * 8 + j * 2
            payload[off] = v & 0xFF
            payload[off + 1] = (v >> 8) & 0xFF
            
    cb_offset = n_codebook * 8
    for by in range(blocks_h):
        for bx in range(blocks_w):
            ti = twiddle(bx, by)
            bi = by * blocks_w + bx
            payload[cb_offset + ti] = best_c[bi]
            
    return payload


def encode_vq(rgba: bytes, fmt: int, data_fmt: int, width: int, height: int) -> bytes:
    if data_fmt == 0x03:
        n_codebook = 256
    elif data_fmt == 0x10:
        n_codebook = (width * height // 4) // 8
    else:
        raise ValueError('unsupported data_fmt')

    rgba_arr = np.frombuffer(rgba, dtype=np.uint8)
    payload_arr = _njit_encode_vq(rgba_arr, fmt, data_fmt, width, height, n_codebook)
    return payload_arr.tobytes()


def decode_pvr(pvr: bytes) -> tuple[int, int, int, int, bytes]:
    if pvr[:4] != b'GBIX' or pvr[0x10:0x14] != b'PVRT':
        raise ValueError('not a GBIX/PVRT texture')
    size = struct.unpack_from('<I', pvr, 0x14)[0]
    fmt = pvr[0x18]
    data_fmt = pvr[0x19]
    width, height = struct.unpack_from('<HH', pvr, 0x1c)
    payload = pvr[0x20:0x20 + size - 8]
    rgba = decode_vq(payload, fmt, data_fmt, width, height)
    return width, height, fmt, data_fmt, rgba


def encode_pvr(rgba: bytes, width: int, height: int,
               fmt: int = 0x02, data_fmt: int = 0x03) -> bytes:
    payload = encode_vq(rgba, fmt, data_fmt, width, height)

    gbix_data = struct.pack('<II', 0, 0)  # GBIX payload
    gbix_hdr = b'GBIX' + struct.pack('<I', 8) + gbix_data  # 16 bytes

    pvr_size = len(payload) + 8
    pvrt_hdr = b'PVRT' + struct.pack('<I', pvr_size) + bytes([fmt, data_fmt, 0, 0]) + struct.pack('<HH', width, height)

    return gbix_hdr + pvrt_hdr + payload  # 0x20 + len(payload)


def parse_syspvr_entries(data: bytes) -> list[dict]:
    first_sector, first_inner, _, first_size = struct.unpack_from('<HHHH', data, 0)
    first_offset = first_sector * SECTOR + (first_inner & (SECTOR - 1))
    entries = []
    for i in range(first_offset // 8):
        sector, inner, _, size = struct.unpack_from('<HHHH', data, i * 8)
        if size == 0:
            continue
        offset = sector * SECTOR + (inner & (SECTOR - 1))
        if offset + size > len(data):
            continue
        if data[offset:offset + 4] != b'GBIX' or data[offset + 16:offset + 20] != b'PVRT':
            continue
        pvr_size = struct.unpack_from('<I', data, offset + 20)[0]
        if pvr_size + 0x18 != size:
            continue
        fmt = data[offset + 24]
        data_fmt = data[offset + 25]
        width, height = struct.unpack_from('<HH', data, offset + 28)
        entries.append(dict(idx=i, offset=offset, size=size,
                            fmt=fmt, data_fmt=data_fmt, width=width, height=height))
    return entries


def write_meta(dir_path: Path, entries: list[dict]) -> None:
    import csv
    fieldnames = ['idx', 'width', 'height', 'fmt', 'data_fmt']
    with open(dir_path / '_meta.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in entries:
            w.writerow({c: e[c] for c in fieldnames})


def read_meta(dir_path: Path) -> list[dict]:
    import csv
    csv_path = dir_path / '_meta.csv'
    if not csv_path.exists():
        return []
    with open(csv_path, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def unpack(mrg_path: Path, out_dir: Path) -> None:
    data = mrg_path.read_bytes()
    entries = parse_syspvr_entries(data)
    out_dir.mkdir(parents=True, exist_ok=True)
    for e in entries:
        offset, size = e['offset'], e['size']
        pvr_blob = data[offset:offset + size]
        try:
            w, h, fmt, dfmt, rgba = decode_pvr(pvr_blob)
        except Exception as exc:
            print(f'  [{e["idx"]:03d}] decode failed: {exc}')
            continue
        img = Image.frombytes('RGBA', (w, h), rgba)
        img = img.transpose(Image.Transpose.TRANSPOSE)
        png_name = f'{e["idx"]:03d}.png'
        img.save(out_dir / png_name)
        e['png_path'] = png_name
        print(f'  [{e["idx"]:03d}] {png_name}')
    write_meta(out_dir, entries)
    print(f'unpacked {len(entries)} entries -> {out_dir}')


def pack(src_dir: Path, out_path: Path) -> None:
    entries = read_meta(src_dir)
    if not entries:
        print(f'[error] _meta.csv not found or empty in {src_dir}, cannot pack.')
        return

    for e in entries:
        e['idx'] = int(e['idx'])
        e['width'] = int(e['width'])
        e['height'] = int(e['height'])
        e['fmt'] = int(e['fmt'])
        e['data_fmt'] = int(e['data_fmt'])
        candidates = list(src_dir.glob(f'{e["idx"]:03d}.png'))
        if not candidates:
            print(f'  [{e["idx"]:03d}] PNG not found, skipping')
            continue
        e['path'] = candidates[0]

    entries = [e for e in entries if e.get('path') and e['path'].exists()]
    entries.sort(key=lambda x: x['idx'])

    if not entries:
        print('[error] no valid PNG entries found')
        return

    encoded = []
    for e in entries:
        img = Image.open(e['path']).convert('RGBA')
        img = img.transpose(Image.Transpose.TRANSPOSE)
        w, h = img.size
        width = e['width'] or w
        height = e['height'] or h
        fmt = e['fmt']
        data_fmt = e['data_fmt']
        rgba = img.tobytes()
        pvr = encode_pvr(rgba, width, height, fmt, data_fmt)
        encoded.append(dict(
            idx=e['idx'],
            width=width,
            height=height,
            fmt=fmt,
            data_fmt=data_fmt,
            pvr_data=pvr,
            size=len(pvr),
            orig_name=e['path'].name,
        ))
        print(f'  [{e["idx"]:03d}] {e["path"].name}')

    encoded.sort(key=lambda x: x['idx'])
    if not encoded:
        print('[error] no entries to pack')
        return

    max_idx = max(e['idx'] for e in encoded)
    raw_header_size = (max_idx + 1) * 8
    first_data = raw_header_size
    if first_data % SECTOR:
        first_data += SECTOR - (first_data % SECTOR)

    header_end = min(first_data, 256 * 8)
    buf = bytearray(b'\xff\xff' * (header_end // 2))
    buf.extend(b'\x00' * (first_data - header_end))
    cursor = first_data
    for e in encoded:
        sector = cursor // SECTOR
        inner = cursor % SECTOR
        flags = 0x0001
        if e['data_fmt'] == 0x03:
            flags |= 0x0008
        hdr = struct.pack('<HHHH', sector, inner, flags, e['size'])
        buf[e['idx'] * 8:e['idx'] * 8 + 8] = hdr
        buf.extend(e['pvr_data'])
        cursor += e['size']

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(buf))
    print(f'packed {len(encoded)} entries -> {out_path}')


def main():
    p = argparse.ArgumentParser(description='SYSPVR tool')
    sub = p.add_subparsers(dest='command', required=True)

    p_unpack = sub.add_parser('unpack', help='Unpack MRG to PNGs')
    p_unpack.add_argument('-i', '--input', required=True, help='Input .mrg file')
    p_unpack.add_argument('-o', '--output', required=True, help='Output directory with PNGs + _meta.csv')

    p_pack = sub.add_parser('pack', help='Pack PNGs to MRG')
    p_pack.add_argument('-i', '--input', required=True, help='Input directory with PNGs + _meta.csv')
    p_pack.add_argument('-o', '--output', required=True, help='Output .mrg file')

    args = p.parse_args()   
    
    match args.command:
        case 'unpack':
            unpack(Path(args.input), Path(args.output))
        case 'pack':
            pack(Path(args.input), Path(args.output))
        case _:
            p.print_help()


if __name__ == '__main__':
    main()
