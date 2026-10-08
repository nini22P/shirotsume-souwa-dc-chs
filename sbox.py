from pathlib import Path

LOAD_BASE = 0x8C010000
SJIS_TABLE_VA = 0x8C09FE00
SJIS_TABLE_LEN = 0x071C
MAIN_FONT_SLOTS = 1808
CODE_BASE = 0xA8
TRAIL_ORDER = tuple(range(0x41, 0x5B)) + tuple(range(0x61, 0x7B))
DEFAULT_BIN_PATH = Path(__file__).resolve().parent / 'raw' / 'data' / '1ST_READ.BIN'


def _bin_path(bin_path: str | Path | None = None) -> Path:
    if bin_path is not None:
        return Path(bin_path)
    return DEFAULT_BIN_PATH


def load_sjis_table(bin_path: str | Path | None = None, *, limit: int = SJIS_TABLE_LEN,
                    stop_at_zero: bool = True) -> list[bytes]:
    data = _bin_path(bin_path).read_bytes()
    offset = SJIS_TABLE_VA - LOAD_BASE
    end = offset + limit * 2
    if offset < 0 or end > len(data):
        raise ValueError('SJIS table range is outside 1ST_READ.BIN')
    table = []
    for i in range(offset, end, 2):
        sjis = bytes(data[i:i + 2])
        if stop_at_zero and sjis == b'\x00\x00':
            break
        table.append(sjis)
    return table


def sjis_to_char(sjis: bytes | tuple[int, ...]) -> str:
    try:
        return bytes(sjis).decode('cp932')
    except UnicodeDecodeError:
        return '\uFFFD'


def _load_sjis_table() -> list[tuple[int, ...]]:
    return [tuple(sjis) for sjis in load_sjis_table()]


def _code_for_index(index: int) -> tuple[int, int]:
    return CODE_BASE + index // len(TRAIL_ORDER), TRAIL_ORDER[index % len(TRAIL_ORDER)]


def _build_maps() -> tuple[dict[tuple[int, ...], tuple[int, ...]], dict[tuple[int, ...], tuple[int, ...]]]:
    enc: dict[tuple[int, ...], tuple[int, ...]] = {}
    dec: dict[tuple[int, ...], tuple[int, ...]] = {}
    for index, sjis in enumerate(_load_sjis_table()):
        code = _code_for_index(index)
        enc.setdefault(sjis, code)
        dec[code] = sjis
    return enc, dec


enc_map, dec_map = _build_maps()


def encrypt_text(plain_sjis: bytes) -> bytes:
    result = bytearray()
    i = 0
    while i < len(plain_sjis):
        b = plain_sjis[i]
        if b < 0x80:
            result.append(b)
            i += 1
        elif 0xA0 <= b <= 0xDF:
            result.append(enc_map.get((b,), (b,))[0])
            i += 1
        elif 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC:
            if i + 1 >= len(plain_sjis):
                result.append(b)
                i += 1
                continue
            key = (b, plain_sjis[i + 1])
            result.extend(enc_map.get(key, key))
            i += 2
        else:
            result.append(b)
            i += 1
    return bytes(result)


def decrypt_text(encrypted: bytes) -> bytes:
    result = bytearray()
    i = 0
    while i < len(encrypted):
        b = encrypted[i]
        if b < 0x80:
            result.append(b)
            i += 1
            continue
        if i + 1 < len(encrypted):
            key = (b, encrypted[i + 1])
            if key in dec_map:
                result.extend(dec_map[key])
                i += 2
                continue
        result.append(dec_map.get((b,), (b,))[0])
        i += 1
    return bytes(result)


def verify_cp932(data: bytes) -> bool:
    try:
        data.decode('cp932')
        return True
    except UnicodeDecodeError:
        return False
