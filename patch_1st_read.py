import argparse, struct, sys
from pathlib import Path

LOAD_BASE = 0x8C010000

MAIN_TABLE_FILE = 0x8FE00
MAIN_TABLE_VA = 0x8C09FE00
ORIG_LIMIT = 0x071C
PAD_ENTRIES = 32

LIMIT_SITES = [0x5C262, 0x5C5CA, 0x5C86A, 0x5D024, 0x5D602]
MAIN_PTR_SITE = 0x12FD18

LIMIT_DESC = {
    0x5C262: 'func1 string->texture',
    0x5C5CA: 'func2 char->encoding',
    0x5C86A: 'func3 char->encoding var.',
    0x5D024: 'func4 large-page map',
    0x5D602: 'func5 wide-char map',
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def relocate_main_table(data: bytes) -> bytes:
    patched = bytearray(data)
    table_bytes = ORIG_LIMIT * 2
    pad_bytes = PAD_ENTRIES * 2
    new_limit = ORIG_LIMIT + PAD_ENTRIES
    write_off = (len(patched) + 15) & ~15

    require(MAIN_TABLE_FILE + table_bytes <= len(data), 'Original main table is outside 1ST_READ.BIN')

    old_main_ptr = struct.unpack_from('<I', data, MAIN_PTR_SITE)[0]
    require(old_main_ptr == MAIN_TABLE_VA,
            f'Unexpected main-font pointer at 0x{MAIN_PTR_SITE:05X}: 0x{old_main_ptr:08X}')

    for off in LIMIT_SITES:
        old = struct.unpack_from('<H', data, off)[0]
        require(old == ORIG_LIMIT, f'Unexpected limit at 0x{off:05X}: 0x{old:04X}')

    orig_table = data[MAIN_TABLE_FILE:MAIN_TABLE_FILE + table_bytes]
    if len(patched) < write_off:
        patched.extend(b'\x00' * (write_off - len(patched)))
    patched.extend(b'\x00' * pad_bytes)
    patched.extend(orig_table)

    new_main_va = LOAD_BASE + write_off
    struct.pack_into('<I', patched, MAIN_PTR_SITE, new_main_va)

    for off in LIMIT_SITES:
        struct.pack_into('<H', patched, off, new_limit)

    require(bytes(patched[write_off + pad_bytes:write_off + pad_bytes + table_bytes]) == orig_table,
            'Copied main table verification failed')
    require(struct.unpack_from('<I', patched, MAIN_PTR_SITE)[0] == new_main_va,
            'Patched main-font pointer verification failed')
    for off in LIMIT_SITES:
        require(struct.unpack_from('<H', patched, off)[0] == new_limit,
                f'Limit update failed at 0x{off:05X}')
    require(patched[write_off:write_off + pad_bytes] == b'\x00' * pad_bytes,
            'Zero padding verification failed')

    print(f'  main ptr  0x{MAIN_PTR_SITE:05X}: 0x{MAIN_TABLE_VA:08X} -> 0x{new_main_va:08X}')
    for off in LIMIT_SITES:
        print(f'  limit 0x{off:05X} ({LIMIT_DESC.get(off, "?")}): 0x{ORIG_LIMIT:04X} -> 0x{new_limit:04X}')
    print(f'  appended {PAD_ENTRIES} zero entries + main table: 0x{table_bytes:X} bytes @ file 0x{write_off:05X}')
    print(f'  file size: 0x{len(data):X} -> 0x{len(patched):X}')
    return bytes(patched)


def main():
    parser = argparse.ArgumentParser(description='Patch 1ST_READ.BIN font table')
    parser.add_argument('-i', '--input', required=True, help='Input 1ST_READ.BIN file')
    parser.add_argument('-o', '--output', required=True, help='Output 1ST_READ.BIN file')
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.exists():
        print(f'ERROR: {in_path} not found', file=sys.stderr)
        sys.exit(1)

    patched = relocate_main_table(in_path.read_bytes())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(patched)
    print(f'[patch] Wrote {out_path}')


if __name__ == '__main__':
    main()
