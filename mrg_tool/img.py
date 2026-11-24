import os
import struct
from collections import Counter
import numpy as np
from PIL import Image
from .common import SECTOR, encode_hed_entry, hed_path, nam_path, parse_nam, read_hed_entries

FMT_NAMES: dict[int, str] = {
    0x01: 'RGB565',
    0x02: 'RGB565',
    0x11: 'ARGB1555',
    0x12: 'ARGB1555',
    0x41: 'ARGB4444',
    0x42: 'ARGB4444',
    0x43: 'ARGB4444 bytepacked',
}

# 0x43 扩展层合成表（arc_mod/mod_ssdc43.asm）
T43 = (0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 15)
_T43_LUT = np.array(T43, dtype=np.uint8)

# T 表缺少 8：低 nibble 为 8 时取最接近的可表示值（7 或 9）
_T43_INV: dict[int, int] = {}
for _i, _v in enumerate(T43):
    _T43_INV.setdefault(_v, _i)
_T43_SNAP_LUT = np.array(
    [
        _T43_INV[_v]
        if _v in _T43_INV
        else min(range(16), key=lambda i: (abs(T43[i] - _v), T43[i]))
        for _v in range(16)
    ],
    dtype=np.uint8,
)

_RGBA_OPAQUE_BLACK: int = struct.unpack('=I', b'\x00\x00\x00\xff')[0]
_STRUCT_U16_BE = [struct.Struct(f'>{n}H') for n in range(65)]
_COLOR_LUTS: dict[int, list[int]] = {}
_LUT43: list[int] | None = None


# ---------- 像素格式查找表（主机字节序 uint32 = RGBA） ----------

def _get_color_lut(fmt: int) -> list[int]:
    kind = fmt & 0xF0
    if kind in _COLOR_LUTS:
        return _COLOR_LUTS[kind]

    v = np.arange(65536, dtype=np.uint32)
    rgba = np.empty((65536, 4), dtype=np.uint8)
    if kind == 0x00:
        # RGB565：G 最低位同时补入各通道 bit2，再将高 2 位回填到低 2 位
        r = (v >> 11) & 0x1F
        g = (v >> 5) & 0x3F
        b = v & 0x1F
        cf = (g & 1) << 2
        rgba[:, 0] = (r << 3) + cf + (r >> 3)
        rgba[:, 1] = (g << 2) + (g >> 4)
        rgba[:, 2] = (b << 3) + cf + (b >> 3)
        rgba[:, 3] = 0xFF
    elif kind == 0x10:
        # ARGB1555
        r = (v >> 10) & 0x1F
        g = (v >> 5) & 0x1F
        b = v & 0x1F
        rgba[:, 0] = (r << 3) | (r >> 2)
        rgba[:, 1] = (g << 3) | (g >> 2)
        rgba[:, 2] = (b << 3) | (b >> 2)
        rgba[:, 3] = np.where(v >> 15, 0xFF, 0x00)
    else:
        # ARGB4444
        rgba[:, 0] = ((v >> 8) & 0xF) * 0x11
        rgba[:, 1] = ((v >> 4) & 0xF) * 0x11
        rgba[:, 2] = (v & 0xF) * 0x11
        rgba[:, 3] = ((v >> 12) & 0xF) * 0x11

    lut = rgba.view(np.uint32).ravel().tolist()
    _COLOR_LUTS[kind] = lut
    return lut


def _get_lut43() -> list[int]:
    global _LUT43
    if _LUT43 is not None:
        return _LUT43
    v = np.arange(4096, dtype=np.uint32)
    rgba = np.empty((4096, 4), dtype=np.uint8)
    rgba[:, 0] = ((v >> 8) & 0xF) * 0x11
    rgba[:, 1] = ((v >> 4) & 0xF) * 0x11
    rgba[:, 2] = (v & 0xF) * 0x11
    rgba[:, 3] = 0xFF
    _LUT43 = rgba.view(np.uint32).ravel().tolist()
    return _LUT43


def _pack_pixels_be16(rgba: np.ndarray, fmt: int) -> bytes:
    """将 (N, 4) uint8 RGBA 打包为大端 16 位像素字节流。"""
    r = rgba[:, 0].astype(np.uint16)
    g = rgba[:, 1].astype(np.uint16)
    b = rgba[:, 2].astype(np.uint16)
    a = rgba[:, 3].astype(np.uint16)
    if fmt in (0x01, 0x02):
        raw = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    elif fmt in (0x11, 0x12):
        raw = ((a >> 7) << 15) | ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
    else:
        raw = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
    return raw.astype('>u2').tobytes()


# ---------- 图像解码 / 编码 ----------

def _img_header(blob: bytes) -> tuple[int, int, int, int]:
    return (
        struct.unpack_from('>H', blob, 4)[0],
        struct.unpack_from('>H', blob, 8)[0],
        struct.unpack_from('>H', blob, 10)[0],
        blob[1],
    )


def _is_image(blob: bytes) -> bool:
    return (
        len(blob) >= 13
        and blob[0] == 0x10
        and blob[1] in FMT_NAMES
        and blob[2] == 0x00
        and blob[3] == 0x40
        and blob[6] == 0x05
        and blob[7] == 0x01
    )


def _decode_image(blob: bytes) -> tuple[int, int, int, np.ndarray]:
    w_buf, w, h, fmt = _img_header(blob)
    npix = w_buf * 64
    out = [0] * npix
    si = 13
    n = len(blob)
    oi = 0
    hist = [0] * 64
    hb = 0
    default_px = _RGBA_OPAQUE_BLACK if (fmt < 0x10 or fmt == 0x43) else 0
    k = 128 if (fmt & 0x0F) == 2 else 64
    is_43 = fmt == 0x43
    lut = _get_lut43() if is_43 else _get_color_lut(fmt)
    carry: int | None = None

    while oi < npix:
        if si >= n:
            raise ValueError('truncated stream')
        b = blob[si]
        si += 1
        if carry is not None:
            ctrl = (carry << 4) | (b >> 4)
            carry = b & 0xF
        else:
            ctrl = b
        run = (ctrl >> 2) + 1
        op = ctrl & 3
        if op == 0:
            fill = default_px if oi == 0 else out[oi - 1]
            end = min(oi + run, npix)
            out[oi:end] = [fill] * (end - oi)
            oi = end
        elif op == 1:
            end = min(oi + run, npix)
            if oi >= k:
                out[oi:end] = out[oi - k : end - k]
                oi = end
            else:
                while oi < end:
                    s = oi - k
                    out[oi] = out[s] if s >= 0 else default_px
                    oi += 1
        elif op == 2:
            out[oi] = hist[(run - 1) & 63]
            oi += 1
        else:
            end = min(oi + run, npix)
            if is_43:
                # 12-bit RGB444 半字节流：无进位读 2 字节留半字节，有进位读 1 字节凑齐 3 半字节
                while oi < end:
                    if carry is None:
                        b1 = blob[si]
                        b2 = blob[si + 1]
                        si += 2
                        c = lut[(b1 << 4) | (b2 >> 4)]
                        carry = b2 & 0xF
                    else:
                        b1 = blob[si]
                        si += 1
                        c = lut[(carry << 8) | b1]
                        carry = None
                    out[oi] = c
                    hist[hb] = c
                    hb = (hb + 1) & 63
                    oi += 1
            else:
                cnt = end - oi
                vals = _STRUCT_U16_BE[cnt].unpack_from(blob, si)
                si += cnt * 2
                for v in vals:
                    c = lut[v]
                    out[oi] = c
                    hist[hb] = c
                    hb = (hb + 1) & 63
                    oi += 1

    return w, h, fmt, np.array(out, dtype=np.uint32)


def _encode_43(tiles_rgba: np.ndarray) -> bytes:
    """0x43 压缩：保持控制字节字面相位，原始像素成对按 3 字节（6 nibble）输出。"""
    pixels = np.ascontiguousarray(tiles_rgba).view(np.uint32).ravel().tolist()
    nibbles = (tiles_rgba[:, :3] >> 4).tolist()
    out = bytearray()
    n = len(pixels)
    i = 0
    k = 64
    hist: list[int | None] = [None] * 64
    slot: dict[int, int] = {}
    hb = 0
    pend_idx: int | None = None

    while i < n:
        if pend_idx is None:
            best = 0
            kind = 0
            if i:
                r = 0
                prev = pixels[i - 1]
                while r < 64 and i + r < n and pixels[i + r] == prev:
                    r += 1
                if r > 1:
                    best, kind = r, 0
            if i >= k and pixels[i] == pixels[i - k]:
                r = 1
                while r < 64 and i + r < n and pixels[i + r] == pixels[i - k + r]:
                    r += 1
                if r > best:
                    best, kind = r, 1
            if best > 1:
                out.append(((best - 1) << 2) | kind)
                i += best
                continue
            q = slot.get(pixels[i])
            if q is not None:
                out.append((q << 2) | 2)
                i += 1
                continue
            pend_idx = i
            i += 1
            continue

        r0, g0, b0 = nibbles[pend_idx]
        r1, g1, b1 = nibbles[i]
        out += bytes((0x07, (r0 << 4) | g0, (b0 << 4) | r1, (g1 << 4) | b1))
        for idx in (pend_idx, i):
            p = pixels[idx]
            old = hist[hb]
            if old is not None and slot.get(old) == hb:
                del slot[old]
            hist[hb] = p
            slot[p] = hb
            hb = (hb + 1) & 63
        pend_idx = None
        i += 1

    return bytes(out)


def _encode_stream(tiles_rgba: np.ndarray, fmt: int) -> bytes:
    """op0/op1/op2/op3 贪心压缩。"""
    pixels = np.ascontiguousarray(tiles_rgba).view(np.uint32).ravel().tolist()
    packed = _pack_pixels_be16(tiles_rgba, fmt)
    out = bytearray()
    n = len(pixels)
    i = 0
    k = 128 if (fmt & 0x0F) == 2 else 64
    hist: list[int | None] = [None] * 64
    slot: dict[int, int] = {}
    hb = 0

    while i < n:
        best = 0
        kind = 0
        if i:
            r = 0
            prev = pixels[i - 1]
            while r < 64 and i + r < n and pixels[i + r] == prev:
                r += 1
            if r > 1:
                best, kind = r, 0
        if i >= k and pixels[i] == pixels[i - k]:
            r = 1
            while r < 64 and i + r < n and pixels[i + r] == pixels[i - k + r]:
                r += 1
            if r > best:
                best, kind = r, 1
        if best > 1:
            out.append(((best - 1) << 2) | kind)
            i += best
            continue
        q = slot.get(pixels[i])
        if q is not None:
            out.append((q << 2) | 2)
            i += 1
            continue
        j = i
        while j < n and j - i < 64:
            if j > i and pixels[j] == pixels[j - 1]:
                break
            if j >= k and pixels[j] == pixels[j - k]:
                break
            if j > i and pixels[j] in slot:
                break
            j += 1
        if j == i:
            j = i + 1
        out.append(((j - i - 1) << 2) | 3)
        out += packed[i * 2 : j * 2]
        while i < j:
            p = pixels[i]
            old = hist[hb]
            if old is not None and slot.get(old) == hb:
                del slot[old]
            hist[hb] = p
            slot[p] = hb
            hb = (hb + 1) & 63
            i += 1

    return bytes(out)


def _deblock(tiles_u32: np.ndarray, w: int, h: int) -> np.ndarray:
    bw = (w + 63) // 64
    rows = ((h + 63) // 64) * 64
    img_u32 = tiles_u32.reshape(bw, rows, 64).transpose(1, 0, 2).reshape(rows, bw * 64)[:h, :w]
    return np.ascontiguousarray(img_u32).view(np.uint8).reshape(h, w, 4)


def _reblock(rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    bw = (w + 63) // 64
    rows = ((h + 63) // 64) * 64
    pad_h, pad_w = rows - h, bw * 64 - w
    if pad_h or pad_w:
        rgba = np.pad(rgba, ((0, pad_h), (0, pad_w), (0, 0)))
    return np.ascontiguousarray(rgba.reshape(rows, bw, 64, 4).transpose(1, 0, 2, 3)).reshape(-1, 4)


def _trailer(blob: bytes, tiles: int) -> bytes | None:
    end = len(blob)
    while end > 0 and blob[end - 1] == 0xFF:
        end -= 1
    start = end - tiles - 1
    if start >= 13 and blob[end - 1] == 0xCC:
        return blob[start:end]
    return None


def _encode_image(rgba: np.ndarray, w: int, h: int, fmt: int, trailer: bytes | None) -> bytes:
    w_buf = ((w + 63) // 64) * 64 * ((h + 63) // 64)
    tiles_rgba = _reblock(rgba, w, h)
    stream = _encode_43(tiles_rgba) if fmt == 0x43 else _encode_stream(tiles_rgba, fmt)
    if trailer is None:
        tiles = w_buf // 64
        trailer = b'\xCC' + b'\x01' * (tiles - 1) + b'\xCC'
    head = (
        bytes((0x10, fmt, 0x00, 0x40))
        + struct.pack('>H', w_buf)
        + bytes((0x05, 0x01))
        + struct.pack('>HH', w, h)
        + b'\x00'
    )
    return head + stream + trailer


def _compose43(base_tiles_u32: np.ndarray, ext_tiles_u32: np.ndarray) -> np.ndarray:
    b = base_tiles_u32.view(np.uint8).reshape(-1, 4)
    e = ext_tiles_u32.view(np.uint8).reshape(-1, 4)
    out = np.empty_like(e)
    out[:, :3] = (b[:, :3] & 0xF0) | _T43_LUT[e[:, :3] >> 4]
    out[:, 3] = 0xFF
    return out.view(np.uint32).ravel()


def _split43(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    base = np.empty_like(rgba)
    ext = np.empty_like(rgba)
    ch = rgba[..., :3]
    base[..., :3] = (ch >> 4) * 0x11
    base[..., 3] = 0xFF
    ext[..., :3] = _T43_SNAP_LUT[ch & 0xF] * 0x11
    ext[..., 3] = 0xFF
    return base, ext


# ---------- 自包含容器 ----------

def _read_table(blob: bytes) -> list[tuple[int, int, int]]:
    entries: list[tuple[int, int, int]] = []
    for n in range(SECTOR // 8):
        a, b, c, d = struct.unpack_from('<4H', blob, n * 8)
        if a == 0xFFFF:
            break
        entries.append((a * SECTOR + b, c, d))
    return entries


def _is_container(blob: bytes) -> bool:
    if len(blob) < SECTOR + 13:
        return False
    try:
        table = _read_table(blob)
    except struct.error:
        return False
    return bool(table) and table[0][0] == SECTOR


def _split_container(blob: bytes) -> list[bytes]:
    table = _read_table(blob)
    records: list[bytes] = []
    for i, (off, _, _) in enumerate(table):
        end = table[i + 1][0] if i + 1 < len(table) else len(blob)
        records.append(blob[off:end].rstrip(b'\xFF'))
    return records


def _build_container(records: list[bytes]) -> bytes:
    table = bytearray()
    body = bytearray()
    off = SECTOR
    for rec in records:
        length = len(rec)
        table += struct.pack(
            '<4H',
            off >> 11,
            off & 0x7FF,
            ((off + length) >> 11) - (off >> 11),
            length & 0xFFFF,
        )
        body += rec
        # 打包器每条记录至少补 1 个对齐单元（长度正好为 8 的倍数时补满 8 字节）
        body += b'\xFF' * (8 - (length & 7))
        off = SECTOR + len(body)
    table += b'\xFF' * (SECTOR - len(table))
    return bytes(table) + bytes(body)


# ---------- 文件与命名 ----------

def _save_png(tiles_u32: np.ndarray, w: int, h: int, path: str) -> None:
    Image.fromarray(_deblock(tiles_u32, w, h), 'RGBA').save(path)


def _safe_name(name: str) -> str:
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '_')
    return name.strip().strip('.')


def _make_keys(count: int, names: list[str] | None) -> list[str]:
    keys: list[str] = []
    used: set[str] = set()
    counts = Counter(n for n in names if n) if names else Counter()
    for i in range(count):
        name = _safe_name(names[i]) if names and i < len(names) else ''
        key = name if name and counts[names[i]] == 1 else f'{i:04d}'
        if key in used:
            key = f'{i:04d}'
        used.add(key)
        keys.append(key)
    return keys


def _scan_pngs(root: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for dirpath, _, files in os.walk(root):
        for fname in files:
            if not fname.lower().endswith('.png'):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root).replace('\\', '/')
            found[rel[:-4]] = full
    return found


def _find_png(paths: dict[str, str], prefix: str, key: str, idx: int) -> str | None:
    cands = [prefix + key, f'{prefix}{idx:04d}']
    if prefix == '':
        cands += [f'{idx:07d}', f'${idx:07d}']
    for c in cands:
        if c in paths:
            return paths[c]
    return None


def _open_source(src: str) -> tuple[bool, list[bytes], list[str], bytes | None]:
    with open(src, 'rb') as f:
        data = f.read()
    hed = hed_path(src)
    has_hed = os.path.exists(hed)
    if has_hed:
        entries = read_hed_entries(hed)
        items = [data[off : off + size] for off, size in entries]
        names: list[str] | None = None
        nam_raw: bytes | None = None
        nam = nam_path(src)
        if os.path.exists(nam):
            with open(nam, 'rb') as f:
                nam_raw = f.read()
            names = parse_nam(nam_raw, len(entries))
        keys = _make_keys(len(items), names)
    else:
        if not _is_container(data):
            raise SystemExit(f'ERROR: {src} is neither HED+MRG nor a self-contained package')
        items = _split_container(data)
        keys = [f'{i:04d}' for i in range(len(items))]
        nam_raw = None
    return has_hed, items, keys, nam_raw


# ---------- unpack ----------

def _base_index(
    infos: list[tuple[int, int, int, int] | None], i: int, w: int, h: int
) -> int | None:
    for j in (i - 1, i + 1):
        if 0 <= j < len(infos):
            info = infos[j]
            if info and info[3] == 0x41 and info[1] == w and info[2] == h:
                return j
    for j, info in enumerate(infos):
        if j != i and info and info[3] == 0x41 and info[1] == w and info[2] == h:
            return j
    return None


def _unpack_items(items: list[bytes], keys: list[str], outdir: str, compose: bool) -> int:
    os.makedirs(outdir, exist_ok=True)
    infos = [_img_header(b) if _is_image(b) else None for b in items]

    # 预先统计被 0x43 引用的 0x41 底图，仅对这些底图做按需缓存，其余解码后立即写出并释放
    base_of: dict[int, int] = {}
    base_refs: Counter[int] = Counter()
    if compose:
        for i, info in enumerate(infos):
            if info and info[3] == 0x43:
                b = _base_index(infos, i, info[1], info[2])
                if b is not None:
                    base_of[i] = b
                    base_refs[b] += 1

    base_cache: dict[int, np.ndarray | None] = {}
    count = 0

    for i, blob in enumerate(items):
        if infos[i] is None:
            if _is_container(blob):
                sub = _split_container(blob)
                sub_keys = [f'{j:04d}' for j in range(len(sub))]
                count += _unpack_items(sub, sub_keys, os.path.join(outdir, keys[i]), compose)
            else:
                print(f'  WARN: {keys[i]}: unknown record, skipped')
            continue

        _, w, h, fmt = infos[i]
        try:
            if i in base_cache:
                tiles = base_cache[i]
                if tiles is None:
                    continue
            else:
                _, _, _, tiles = _decode_image(blob)
        except Exception as e:
            print(f'  WARN: {keys[i]}: {e}')
            if base_refs.get(i, 0) > 0:
                base_cache[i] = None
            continue

        if base_refs.get(i, 0) > 0 and i not in base_cache:
            base_cache[i] = tiles

        if fmt == 0x43 and compose:
            b = base_of.get(i)
            if b is not None:
                if b not in base_cache:
                    try:
                        _, _, _, base_cache[b] = _decode_image(items[b])
                    except Exception:
                        base_cache[b] = None
                base_tiles = base_cache[b]
                if base_tiles is not None:
                    tiles = _compose43(base_tiles, tiles)
                else:
                    print(f'  WARN: {keys[i]}: 0x43 without 0x41 base, exported raw')
                base_refs[b] -= 1
                if base_refs[b] <= 0:
                    base_cache.pop(b, None)
            else:
                print(f'  WARN: {keys[i]}: 0x43 without 0x41 base, exported raw')

        _save_png(tiles, w, h, os.path.join(outdir, keys[i] + '.png'))
        count += 1
        if count % 200 == 0:
            print(f'  ... {count}')

    return count


def unpack(src: str, outdir: str, compose: bool = True) -> None:
    _, items, keys, _ = _open_source(src)
    print(f'{len(items)} records -> {outdir}')
    count = _unpack_items(items, keys, outdir, compose)
    print(f'done: {count} images')


# ---------- replace ----------

def _replace_items(
    items: list[bytes],
    keys: list[str],
    paths: dict[str, str],
    prefix: str,
    dry_run: bool,
    stats: dict[str, int],
) -> tuple[list[bytes], bool]:
    infos = [_img_header(b) if _is_image(b) else None for b in items]
    new_items = list(items)
    derived: dict[int, bytes] = {}

    for i, blob in enumerate(items):
        key = keys[i]
        if infos[i] is None:
            if _is_container(blob):
                sub = _split_container(blob)
                sub_keys = [f'{j:04d}' for j in range(len(sub))]
                new_sub, sub_changed = _replace_items(
                    sub, sub_keys, paths, prefix + key + '/', dry_run, stats
                )
                if sub_changed:
                    new_items[i] = _build_container(new_sub)
            continue

        png = _find_png(paths, prefix, key, i)
        if png is None:
            continue

        w_buf, w, h, fmt = infos[i]
        if dry_run:
            print(f'  [{prefix}{key}] fmt=0x{fmt:02x}({FMT_NAMES[fmt]}) {w}x{h} <- {png}')
            stats['matched'] += 1
            continue

        img = Image.open(png)
        if img.size != (w, h):
            raise SystemExit(f'ERROR: {png} size {img.size} != required {(w, h)}')

        has_alpha = img.mode in ('RGBA', 'LA', 'PA') or (
            img.mode == 'P' and 'transparency' in img.info
        )
        rgba = np.asarray(img.convert('RGBA'), dtype=np.uint8)
        if fmt in (0x01, 0x02):
            if has_alpha and rgba[..., 3].min() < 0xFF:
                print(f'    WARN: {png}: alpha discarded (RGB565)')
        elif not has_alpha:
            print(f'    WARN: {png}: RGB -> ARGB, alpha filled 0xFF')

        tiles = w_buf // 64
        if fmt == 0x43:
            b = _base_index(infos, i, w, h)
            if b is None:
                raise SystemExit(f'ERROR: {png}: 0x43 record has no 0x41 base')
            base, ext = _split43(rgba)
            unreachable = int(np.any((rgba[..., :3] & 0xF) == 8, axis=-1).sum())
            if unreachable:
                print(
                    f'    WARN: {png}: {unreachable} px low nibble=8 '
                    f'not representable, snapped to nearest (7/9)'
                )
            new_items[i] = _encode_image(ext, w, h, 0x43, _trailer(blob, tiles))
            bw_buf, bw, bh, bfmt = infos[b]
            derived[b] = _encode_image(
                base, bw, bh, bfmt, _trailer(items[b], bw_buf // 64)
            )
            print(f'  [{prefix}{key}] 0x43 composed -> also rewrites [{prefix}{keys[b]}]')
        else:
            new_items[i] = _encode_image(rgba, w, h, fmt, _trailer(blob, tiles))
            print(f'  [{prefix}{key}] fmt=0x{fmt:02x} ({FMT_NAMES[fmt]}) {w}x{h} <- {png}')
        stats['matched'] += 1

    for idx, rec in derived.items():
        new_items[idx] = rec
    changed = new_items != items
    return new_items, changed


def _sector_padding(offset: int, size: int) -> bytes:
    buf = bytearray(size)
    pos = (-offset) % 16
    while pos < size:
        buf[pos] = 0x0C
        pos += 16
    return bytes(buf)


def _write_hed_mrg(records: list[bytes], out_mrg: str) -> None:
    hed = bytearray()
    offset = 0
    with open(out_mrg, 'wb') as f:
        for rec in records:
            hed += encode_hed_entry(offset, len(rec))
            f.write(rec)
            offset += len(rec)
            # 与自包含容器一致：至少留 1 个扇区（长度正好是扇区倍数时再多整扇区）
            pad = SECTOR - (len(rec) % SECTOR)
            f.write(_sector_padding(offset, pad))
            offset += pad
    hed += b'\xFF' * 16
    with open(hed_path(out_mrg), 'wb') as f:
        f.write(hed)


def replace(src: str, replace_dir: str, out_mrg: str, dry_run: bool = False) -> None:
    has_hed, items, keys, nam_raw = _open_source(src)
    paths = _scan_pngs(replace_dir)
    stats = {'matched': 0}
    print(f'{len(items)} records, {len(paths)} PNG files')
    new_items, _ = _replace_items(items, keys, paths, '', dry_run, stats)
    if dry_run:
        print(f'DRY RUN: {stats["matched"]} records would be replaced')
        return
    if has_hed:
        _write_hed_mrg(new_items, out_mrg)
        if nam_raw is not None:
            with open(nam_path(out_mrg), 'wb') as f:
                f.write(nam_raw)
    else:
        with open(out_mrg, 'wb') as f:
            f.write(_build_container(new_items))
    print(f'{stats["matched"]} replaced, {len(new_items)} records -> {out_mrg}')
