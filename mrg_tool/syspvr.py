from __future__ import annotations
import csv
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from .common import SECTOR

_CHANNEL_WEIGHTS = np.array([0.3, 0.59, 0.11, 4.0], dtype=np.float32)
_SQRT_WEIGHTS = np.sqrt(np.tile(_CHANNEL_WEIGHTS, 4))


def _decode_colors(vals: np.ndarray, fmt: int) -> np.ndarray:
    """将小端 16 位像素数组解码为 (..., 4) uint8 RGBA。"""
    v = vals.astype(np.uint32)
    rgba = np.empty(v.shape + (4,), dtype=np.uint8)
    if fmt == 0x01:
        r = (v >> 11) & 0x1F
        g = (v >> 5) & 0x3F
        b = v & 0x1F
        rgba[..., 0] = (r << 3) | (r >> 2)
        rgba[..., 1] = (g << 2) | (g >> 4)
        rgba[..., 2] = (b << 3) | (b >> 2)
        rgba[..., 3] = 0xFF
    elif fmt == 0x02:
        rgba[..., 0] = ((v >> 8) & 0xF) * 0x11
        rgba[..., 1] = ((v >> 4) & 0xF) * 0x11
        rgba[..., 2] = (v & 0xF) * 0x11
        rgba[..., 3] = ((v >> 12) & 0xF) * 0x11
    else:
        raise ValueError(f'unsupported pixel format: 0x{fmt:02x}')
    return rgba


def _encode_colors(rgba: np.ndarray, fmt: int) -> np.ndarray:
    """将 (..., 4) uint8 RGBA 打包为小端 16 位像素数组。"""
    r = rgba[..., 0].astype(np.uint16)
    g = rgba[..., 1].astype(np.uint16)
    b = rgba[..., 2].astype(np.uint16)
    a = rgba[..., 3].astype(np.uint16)
    if fmt == 0x01:
        raw = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    elif fmt == 0x02:
        raw = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
    else:
        raise ValueError(f'unsupported pixel format: 0x{fmt:02x}')
    return raw.astype('<u2')


def _twiddle_map(blocks_w: int, blocks_h: int) -> np.ndarray:
    """生成行优先块索引 -> Morton (twiddled) 索引的映射表。"""
    bits = max(blocks_w - 1, blocks_h - 1, 1).bit_length()
    bx = np.arange(blocks_w, dtype=np.int32)[None, :]
    by = np.arange(blocks_h, dtype=np.int32)[:, None]
    t = np.zeros((blocks_h, blocks_w), dtype=np.int32)
    for bit in range(bits):
        t |= ((bx >> bit) & 1) << (2 * bit)
        t |= ((by >> bit) & 1) << (2 * bit + 1)
    return t.ravel()


def decode_vq(payload: bytes, fmt: int, data_fmt: int, width: int, height: int) -> bytes:
    if data_fmt == 0x03:
        codebook_size = 0x800
    elif data_fmt == 0x10:
        codebook_size = width * height // 4
    else:
        raise ValueError(f'unsupported data format: 0x{data_fmt:02x}')

    n_codebook = codebook_size // 8
    cb_u16 = np.frombuffer(payload, dtype='<u2', count=n_codebook * 4).reshape(n_codebook, 4)
    codebook = _decode_colors(cb_u16, fmt)  # (n_codebook, 4, 4)

    indices = np.frombuffer(payload, dtype=np.uint8, offset=codebook_size)
    blocks_w, blocks_h = width // 2, height // 2
    tmap = _twiddle_map(blocks_w, blocks_h)
    img = (
        codebook[indices[tmap]]
        .reshape(blocks_h, blocks_w, 2, 2, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(height, width, 4)
    )
    return np.ascontiguousarray(img).tobytes()


def _extract_blocks(rgba: bytes, width: int, height: int) -> np.ndarray:
    blocks_w, blocks_h = width // 2, height // 2
    arr = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4)
    return (
        arr.reshape(blocks_h, 2, blocks_w, 2, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(-1, 16)
    )


def _kmeans_init(x: np.ndarray, blocks_u8: np.ndarray, n_codebook: int) -> np.ndarray:
    """使用加权欧氏距离的最远点采样初始化码本，复杂度 O(K * N_sample)。"""
    n_blocks = x.shape[0]
    centroids = np.empty((n_codebook, 16), dtype=np.float32)

    alpha_var = np.var(blocks_u8.reshape(n_blocks, 4, 4)[:, :, 3], axis=1)
    first = int(np.argmax(alpha_var))
    centroids[0] = x[first]

    step = max(1, n_blocks // 2048)
    sample = x[::step][:2048]
    min_dist = np.full(sample.shape[0], np.inf, dtype=np.float32)

    for ci in range(1, n_codebook):
        diff = sample - centroids[ci - 1]
        d2 = np.einsum('ij,ij->i', diff, diff)
        np.minimum(min_dist, d2, out=min_dist)
        best = int(np.argmax(min_dist))
        centroids[ci] = sample[best]
        min_dist[best] = 0.0

    return centroids


def _kmeans(
    x: np.ndarray, centroids: np.ndarray, max_iter: int = 30
) -> tuple[np.ndarray, np.ndarray]:
    """基于矩阵乘法的加权 K-means，支持收敛早停。"""
    k = centroids.shape[0]
    x_norm2 = np.einsum('ij,ij->i', x, x)[:, None]
    prev_labels: np.ndarray | None = None
    labels = np.empty(x.shape[0], dtype=np.intp)

    for _ in range(max_iter):
        c_norm2 = np.einsum('ij,ij->i', centroids, centroids)
        dists = x_norm2 + c_norm2[None, :] - 2.0 * (x @ centroids.T)
        labels = np.argmin(dists, axis=1)
        if prev_labels is not None and np.array_equal(labels, prev_labels):
            break
        prev_labels = labels

        counts = np.bincount(labels, minlength=k)
        non_empty = counts > 0
        for d in range(16):
            sums = np.bincount(labels, weights=x[:, d], minlength=k)
            centroids[non_empty, d] = sums[non_empty] / counts[non_empty]
    else:
        c_norm2 = np.einsum('ij,ij->i', centroids, centroids)
        labels = np.argmin(x_norm2 + c_norm2[None, :] - 2.0 * (x @ centroids.T), axis=1)

    return centroids, labels.astype(np.uint8)


def encode_vq(rgba: bytes, fmt: int, data_fmt: int, width: int, height: int) -> bytes:
    if data_fmt == 0x03:
        n_codebook = 256
    elif data_fmt == 0x10:
        n_codebook = (width * height // 4) // 8
    else:
        raise ValueError(f'unsupported data_fmt: 0x{data_fmt:02x}')

    blocks_u8 = _extract_blocks(rgba, width, height)
    n_blocks = blocks_u8.shape[0]

    if n_codebook >= n_blocks:
        codebook_u8 = np.zeros((n_codebook, 4, 4), dtype=np.uint8)
        b4 = blocks_u8.reshape(n_blocks, 4, 4)
        codebook_u8[:n_blocks] = b4
        if n_codebook > n_blocks:
            codebook_u8[n_blocks:] = b4[: n_codebook - n_blocks]
        labels = np.arange(n_blocks, dtype=np.uint8)
    else:
        x = blocks_u8.astype(np.float32) * _SQRT_WEIGHTS
        init_c = _kmeans_init(x, blocks_u8, n_codebook)
        centroids, labels = _kmeans(x, init_c)
        codebook_u8 = np.clip(np.rint(centroids / _SQRT_WEIGHTS), 0, 255).astype(np.uint8).reshape(
            n_codebook, 4, 4
        )

    cb_bytes = _encode_colors(codebook_u8, fmt).tobytes()
    tmap = _twiddle_map(width // 2, height // 2)
    twiddled = np.zeros(max(n_blocks, int(tmap.max()) + 1), dtype=np.uint8)
    twiddled[tmap] = labels
    return cb_bytes + twiddled.tobytes()


def decode_pvr(pvr: bytes) -> tuple[int, int, int, int, bytes]:
    if pvr[:4] != b'GBIX' or pvr[0x10:0x14] != b'PVRT':
        raise ValueError('not a GBIX/PVRT texture')
    size = struct.unpack_from('<I', pvr, 0x14)[0]
    fmt = pvr[0x18]
    data_fmt = pvr[0x19]
    width, height = struct.unpack_from('<HH', pvr, 0x1C)
    payload = pvr[0x20 : 0x20 + size - 8]
    rgba = decode_vq(payload, fmt, data_fmt, width, height)
    return width, height, fmt, data_fmt, rgba


def encode_pvr(
    rgba: bytes, width: int, height: int, fmt: int = 0x02, data_fmt: int = 0x03
) -> bytes:
    payload = encode_vq(rgba, fmt, data_fmt, width, height)

    gbix_data = struct.pack('<II', 0, 0)
    gbix_hdr = b'GBIX' + struct.pack('<I', 8) + gbix_data

    pvr_size = len(payload) + 8
    pvrt_hdr = (
        b'PVRT'
        + struct.pack('<I', pvr_size)
        + bytes([fmt, data_fmt, 0, 0])
        + struct.pack('<HH', width, height)
    )

    return gbix_hdr + pvrt_hdr + payload


def parse_syspvr_entries(data: bytes) -> list[dict]:
    first_sector, first_inner, _, _ = struct.unpack_from('<HHHH', data, 0)
    first_offset = first_sector * SECTOR + (first_inner & (SECTOR - 1))
    entries: list[dict] = []
    for i in range(first_offset // 8):
        sector, inner, _, size = struct.unpack_from('<HHHH', data, i * 8)
        if size == 0:
            continue
        offset = sector * SECTOR + (inner & (SECTOR - 1))
        if offset + size > len(data):
            continue
        if data[offset : offset + 4] != b'GBIX' or data[offset + 16 : offset + 20] != b'PVRT':
            continue
        pvr_size = struct.unpack_from('<I', data, offset + 20)[0]
        if pvr_size + 0x18 != size:
            continue
        fmt = data[offset + 24]
        data_fmt = data[offset + 25]
        width, height = struct.unpack_from('<HH', data, offset + 28)
        entries.append(
            dict(
                idx=i,
                offset=offset,
                size=size,
                fmt=fmt,
                data_fmt=data_fmt,
                width=width,
                height=height,
            )
        )
    return entries


def write_meta(dir_path: Path, entries: list[dict]) -> None:
    fieldnames = ['idx', 'width', 'height', 'fmt', 'data_fmt']
    with open(dir_path / '_meta.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in entries:
            w.writerow({c: e[c] for c in fieldnames})


def read_meta(dir_path: Path) -> list[dict]:
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
        pvr_blob = data[offset : offset + size]
        try:
            w, h, _, _, rgba = decode_pvr(pvr_blob)
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

    encoded: list[dict] = []
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
        encoded.append(
            dict(
                idx=e['idx'],
                width=width,
                height=height,
                fmt=fmt,
                data_fmt=data_fmt,
                pvr_data=pvr,
                size=len(pvr),
                orig_name=e['path'].name,
            )
        )
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
        buf[e['idx'] * 8 : e['idx'] * 8 + 8] = hdr
        buf.extend(e['pvr_data'])
        cursor += e['size']

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(buf))
    print(f'packed {len(encoded)} entries -> {out_path}')
