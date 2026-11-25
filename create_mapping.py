import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path
from sbox import MAIN_FONT_SLOTS, load_sjis_table, sjis_to_char

TAG_RE = re.compile(r'<[^>]*>')
FACE_MAX_SLOTS = 462  # 18x22_face (14..16, 3 * 154) 与 20x24_face (33..36, 4 * 120 = 480) 的公共上限


def chars_in_text(text: str) -> set[str]:
    text = TAG_RE.sub('', text)
    return set(text)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open('r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def collect_chars(rows: list[dict[str, str]]) -> tuple[set[str], set[str]]:
    untranslated: set[str] = set()
    translated: set[str] = set()
    for r in rows:
        text = r.get('text', '')
        trans = r.get('translation', '').strip()
        if not trans:
            untranslated.update(chars_in_text(text))
        translated.update(chars_in_text(trans))
    return untranslated, translated


def collect_face_chars(rows: list[dict[str, str]]) -> set[str]:
    face_chars: set[str] = set()
    for r in rows:
        trans = r.get('translation', '').strip()
        if 'FONT' in trans and 'face=' in trans:
            clean = TAG_RE.sub('', trans)
            face_chars.update(clean)
    return face_chars


def main() -> int:
    parser = argparse.ArgumentParser(description='Create font mapping from CSVs')
    parser.add_argument('-s', '--scr-csv', required=True, help='Input scr CSV')
    parser.add_argument('-b', '--bin-csv', required=True, help='Input bin CSV')
    parser.add_argument('-o', '--output', required=True, help='Output mapping CSV')
    args = parser.parse_args()

    scr_csv_path = Path(args.scr_csv)
    bin_csv_path = Path(args.bin_csv)
    out_path = Path(args.output)

    sjis_table = load_sjis_table()
    dc_chars = [sjis_to_char(sj) for sj in sjis_table]
    print(f'DC encoding table: {len(sjis_table)} slots')

    existing_codes: set[int] = {(s[0] << 8) | s[1] for s in sjis_table}

    csv_rows = read_csv(scr_csv_path)
    bin_csv_rows = read_csv(bin_csv_path) if bin_csv_path.exists() else []
    print(f'{scr_csv_path.name}: {len(csv_rows)} rows, {bin_csv_path.name}: {len(bin_csv_rows)} rows')

    untranslated_chars, trans_chars = collect_chars(csv_rows)
    if bin_csv_rows:
        pu, pt = collect_chars(bin_csv_rows)
        untranslated_chars |= pu
        trans_chars |= pt

    face_chars = collect_face_chars(csv_rows)
    if bin_csv_rows:
        face_chars.update(collect_face_chars(bin_csv_rows))

    protected_slots: set[int] = {idx for idx, ch in enumerate(dc_chars) if ch in trans_chars and ch not in untranslated_chars}
    kept_slots: set[int] = set(protected_slots)
    kept_chars: set[str] = {dc_chars[idx] for idx in kept_slots}

    face_kept_slots: set[int] = set()
    for idx in range(min(FACE_MAX_SLOTS, len(dc_chars))):
        if idx in kept_slots:
            continue
        ch = dc_chars[idx]
        if ch in untranslated_chars or (ch in face_chars and ch in trans_chars and ch not in kept_chars):
            kept_slots.add(idx)
            kept_chars.add(ch)
            if ch in face_chars and ch in trans_chars:
                face_kept_slots.add(idx)

    needed_face = sorted(
        c for c in face_chars
        if c in trans_chars
        and c not in kept_chars
        and unicodedata.east_asian_width(c) not in ('Na', 'H')
    )

    free_in_face = [i for i in range(FACE_MAX_SLOTS) if i not in kept_slots and (i >= len(dc_chars) or dc_chars[i] not in trans_chars)]
    if len(free_in_face) < len(needed_face):
        deficit = len(needed_face) - len(free_in_face)
        evictable = [
            i for i in range(FACE_MAX_SLOTS - 1, -1, -1)
            if i not in kept_slots and i < len(dc_chars) and dc_chars[i] in trans_chars
        ]
        evict_set = set(evictable[:deficit])
    else:
        evict_set = set()

    for idx in range(min(FACE_MAX_SLOTS, len(dc_chars))):
        if idx in kept_slots or idx in evict_set:
            continue
        ch = dc_chars[idx]
        if ch in trans_chars and ch not in kept_chars:
            kept_slots.add(idx)
            kept_chars.add(ch)

    for idx in range(FACE_MAX_SLOTS, len(dc_chars)):
        if idx in kept_slots:
            continue
        ch = dc_chars[idx]
        if ch in untranslated_chars or (ch in trans_chars and ch not in face_chars and ch not in kept_chars):
            kept_slots.add(idx)
            kept_chars.add(ch)

    face_alloc_slots = [i for i in range(FACE_MAX_SLOTS) if i not in kept_slots]
    if len(needed_face) > len(face_alloc_slots):
        print(f'ERROR: Need {len(needed_face)} face slots in 0..{FACE_MAX_SLOTS - 1}, only {len(face_alloc_slots)} available')
        return 1
    face_target_slots = face_alloc_slots[: len(needed_face)]
    face_target_set = set(face_target_slots)

    needed_normal = sorted(
        c for c in trans_chars
        if c not in kept_chars
        and c not in needed_face
        and unicodedata.east_asian_width(c) not in ('Na', 'H')
    )
    needed = needed_face + needed_normal

    wide_trans_chars = {c for c in trans_chars if unicodedata.east_asian_width(c) not in ('Na', 'H')}
    wide_face_chars = {c for c in face_chars if c in wide_trans_chars}
    already_covered = len(wide_trans_chars) - len(needed)
    print(f'Total translation chars (full-width): {len(wide_trans_chars)}')
    print(f'Face chars: {len(wide_face_chars)}/{FACE_MAX_SLOTS}')
    print(f'Already covered (in encoding table): {already_covered}')
    print(f'Need mapping (not in encoding table): {len(needed)}')

    used_sjis: set[int] = set()
    for idx in kept_slots:
        if idx < len(sjis_table):
            sj = sjis_table[idx]
            used_sjis.add((sj[0] << 8) | sj[1])

    def iter_unused_sjis():
        for lead in range(0x81, 0xA0):
            for trail in list(range(0x40, 0x7F)) + list(range(0x80, 0xFD)):
                v = (lead << 8) | trail
                if v not in existing_codes:
                    yield v
        for lead in range(0xE0, 0xFD):
            for trail in list(range(0x40, 0x7F)) + list(range(0x80, 0xFD)):
                v = (lead << 8) | trail
                if v not in existing_codes:
                    yield v

    custom_gen = iter_unused_sjis()

    normal_alloc_slots = [
        i for i in range(MAIN_FONT_SLOTS)
        if i not in kept_slots and i not in face_target_set
    ]

    if len(needed_normal) > len(normal_alloc_slots):
        print(f'ERROR: Need {len(needed_normal)} normal slots but only {len(normal_alloc_slots)} available, aborting')
        return 1

    alloc_order = face_target_slots + normal_alloc_slots[: len(needed_normal)]
    print(f'Available empty slots: {len(face_alloc_slots) + len(normal_alloc_slots)}')
    print(f'Can map: {len(needed)}')

    slot_to_char: dict[int, str] = {}
    slot_to_custom: dict[int, int] = {}
    for idx, cn_char in zip(alloc_order, needed):
        slot_to_char[idx] = cn_char
        if idx < len(sjis_table):
            sj = sjis_table[idx]
            val = (sj[0] << 8) | sj[1]
            if val not in used_sjis:
                used_sjis.add(val)
                continue
        slot_to_custom[idx] = next(custom_gen)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['enc_idx', 'char', 'unicode', 'target_jp', 'sjis_hi', 'sjis_lo', 'is_face'])
        for idx in range(MAIN_FONT_SLOTS):
            cn_char = slot_to_char.get(idx, '')
            target_jp = dc_chars[idx] if idx < len(dc_chars) else ''
            if idx in slot_to_custom:
                code = slot_to_custom[idx]
                sj_bytes = (code >> 8, code & 0xFF)
            else:
                sj_bytes = sjis_table[idx] if idx < len(sjis_table) else (0, 0)
            is_face = (
                (cn_char and cn_char in face_chars)
                or (idx in face_kept_slots)
                or (idx in protected_slots and target_jp in face_chars)
            )
            w.writerow([
                idx,
                cn_char,
                f'U+{ord(cn_char):04X}' if cn_char else '',
                target_jp,
                sj_bytes[0],
                sj_bytes[1],
                '1' if is_face else '0',
            ])
    print(f'Mapping: {out_path} ({MAIN_FONT_SLOTS} slots, {len(slot_to_char)} mapped)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
