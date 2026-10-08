import argparse, os, re
from collections import defaultdict
from mrg import xor_crypt, read_hed_entries


def _find_mrg(directory, name, ext):
    parent = os.path.dirname(directory) if not os.path.isdir(directory) else directory
    path = os.path.join(parent, f'{name}.{ext}')
    if not os.path.exists(path):
        raise SystemExit(f'ERROR: {name}.{ext} not found at {path}')
    return path


def rebuild(in_path, out_path):
    in_dir = os.path.dirname(in_path)
    out_dir = os.path.dirname(out_path)

    orig_mrg = _find_mrg(in_dir, 'SCR', 'MRG')
    orig_hed = _find_mrg(in_dir, 'SCR', 'HED')
    new_mrg = _find_mrg(out_dir, 'SCR', 'MRG')
    new_hed = _find_mrg(out_dir, 'SCR', 'HED')

    with open(in_path, 'rb') as f:
        scradr_data = bytearray(f.read())

    count = scradr_data[6] | (scradr_data[7] << 8)
    text_start = 8 + count * 8

    orig_text = scradr_data[text_start:]
    line_starts = []
    pos = 0
    while pos < len(orig_text):
        line_starts.append(pos)
        nl = orig_text.find(b'\n', pos)
        if nl < 0:
            break
        pos = nl + 1

    orig_entries = []
    for line_off in line_starts:
        line = orig_text[line_off:]
        nl = line.find(b'\n')
        if nl >= 0:
            line = line[:nl].rstrip(b'\r')
        if not line.strip():
            continue
        comma = line.rfind(b',')
        if comma < 0:
            continue
        space = line.rfind(b' ', 0, comma)
        if space < 0:
            continue
        label = bytes(line[:space].rstrip().lstrip(b'\xff'))
        try:
            mod = int(line[space + 1:comma], 16)
            off = int(line[comma + 1:], 16)
            orig_entries.append((label, mod, off, line_off))
        except ValueError:
            continue

    print(f'SCRADR: {len(orig_entries)} labels across {count} entries')

    def build_label_map(hed_path, mrg_path):
        m = {}
        hed_entries = read_hed_entries(hed_path)
        with open(mrg_path, 'rb') as f:
            mrg = f.read()
        for ei, (eoff, esz) in enumerate(hed_entries):
            data = xor_crypt(mrg[eoff:eoff + esz])
            for match in re.finditer(rb'_ZZ([0-9a-fA-F]{5})\(([^)]+)\)', data):
                label = match.group(2)
                mod = int(match.group(1)[:3], 16)
                off = match.start() + 2
                m[(label, mod, off)] = ei
        return m

    orig_idx = build_label_map(orig_hed, orig_mrg)

    new_map = defaultdict(dict)
    new_hed_entries = read_hed_entries(new_hed)
    with open(new_mrg, 'rb') as f:
        new_mrg_data = f.read()
    for ei, (eoff, esz) in enumerate(new_hed_entries):
        data = xor_crypt(new_mrg_data[eoff:eoff + esz])
        for match in re.finditer(rb'_ZZ([0-9a-fA-F]{5})\(([^)]+)\)', data):
            label = match.group(2)
            mod = int(match.group(1)[:3], 16)
            off = match.start() + 2
            new_map[ei][(label, mod)] = off

    found = 0
    not_found = 0
    changed = 0
    changes = []

    for label, mod, old_off, line_off in orig_entries:
        key = (label, mod, old_off)
        ei = orig_idx.get(key)
        if ei is not None and (label, mod) in new_map[ei]:
            new_off = new_map[ei][(label, mod)]
            found += 1
            if new_off != old_off:
                changed += 1
                changes.append((line_off, old_off, new_off))
        else:
            not_found += 1

    print(f'Found: {found}, Changed: {changed}, Not found: {not_found}')

    for line_off, old_off, new_off in changes:
        old_hex = f'{old_off:05x}'.encode()
        new_hex = f'{new_off:05x}'.encode()
        line_start = text_start + line_off
        line_end = scradr_data.find(b'\n', line_start)
        if line_end < 0:
            line_end = len(scradr_data)
        for check in range(line_end - 5, line_start - 1, -1):
            if scradr_data[check:check + 5] == old_hex:
                scradr_data[check:check + 5] = new_hex
                break

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(scradr_data)
    print(f'Written: {out_path} ({len(scradr_data)} bytes, {changed} offsets updated)')


def main():
    p = argparse.ArgumentParser(description='SCRADR tool')
    sub = p.add_subparsers(dest='command', required=True)

    p_rebuild = sub.add_parser('rebuild', help='Rebuild SCRADR.MRG with updated label offsets')
    p_rebuild.add_argument('-i', '--input', required=True, help='Input SCRADR.MRG')
    p_rebuild.add_argument('-o', '--output', required=True, help='Output SCRADR.MRG')

    args = p.parse_args()

    match args.command:
        case 'rebuild':
            rebuild(args.input, args.output)
        case _:
            p.print_help()


if __name__ == '__main__':
    main()
