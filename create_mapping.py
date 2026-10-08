import argparse, csv, re, sys, unicodedata
from pathlib import Path
from sbox import load_sjis_table, sjis_to_char

TAG_RE = re.compile(r'<[^>]*>')

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

    needed = sorted(
    c for c in trans_chars
    if (
        c in face_chars
        or c not in dc_chars
    )
    and unicodedata.east_asian_width(c) not in ('Na', 'H')
    )
    needed.sort(key=lambda c: (0 if c in face_chars else 1, c))
    already_covered = len(trans_chars) - len(needed)
    print(f'Total translation chars: {len(trans_chars)}')
    print(f'Already covered (in encoding table): {already_covered}')
    print(f'Need mapping (not in encoding table): {len(needed)}')

    SLOT_START = 0
    safe_slots: list[int] = []
    used_sjis: set[int] = set()
    for idx in range(SLOT_START, len(sjis_table)):
        sj = sjis_table[idx]
        val = (sj[0] << 8) | sj[1]
        if val in used_sjis:
            continue
        ch = dc_chars[idx]
        if ch in untranslated_chars:
            continue
        if ch in trans_chars and ch not in face_chars:
            continue
        used_sjis.add(val)
        safe_slots.append(idx)

    extended_slots: list[int] = []
    for idx in range(SLOT_START, len(sjis_table)):
        if idx in safe_slots:
            continue
        sj = sjis_table[idx]
        val = (sj[0] << 8) | sj[1]
        if val not in used_sjis:
            continue
        ch = dc_chars[idx]
        if ch in untranslated_chars:
            continue
        if ch in trans_chars and ch not in face_chars:
            continue
        extended_slots.append(idx)

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
    custom_codes = [next(custom_gen) for _ in range(len(extended_slots))]

    total_available = len(safe_slots) + len(extended_slots)
    print(f'Available empty slots: {total_available}')
    if len(needed) > total_available:
        print(f'ERROR: Need {len(needed)} slots but only {total_available} available, aborting')
        return 1
    print(f'Can map: {len(needed)}')

    slot_to_char: dict[int, str] = {}
    slot_to_custom: dict[int, int] = {}
    for i, cn_char in enumerate(needed):
        if i < len(safe_slots):
            idx = safe_slots[i]
        else:
            idx = extended_slots[i - len(safe_slots)]
            slot_to_custom[idx] = custom_codes[i - len(safe_slots)]
        slot_to_char[idx] = cn_char

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['enc_idx', 'char', 'unicode', 'target_jp', 'sjis_hi', 'sjis_lo'])
        for idx in range(len(sjis_table)):
            cn_char = slot_to_char.get(idx, '')
            target_jp = dc_chars[idx]
            if idx in slot_to_custom:
                code = slot_to_custom[idx]
                sj_bytes = (code >> 8, code & 0xFF)
            else:
                sj_bytes = sjis_table[idx]
            w.writerow([
                idx,
                cn_char,
                f'U+{ord(cn_char):04X}' if cn_char else '',
                target_jp,
                sj_bytes[0],
                sj_bytes[1],
            ])
    print(f'Mapping: {out_path} ({len(sjis_table)} slots, {len(slot_to_char)} mapped)')
    return 0

if __name__ == '__main__':
    sys.exit(main())
