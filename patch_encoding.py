import argparse, csv, struct, sys
from pathlib import Path

LOAD_BASE = 0x8C010000
MAIN_TABLE_FILE = 0x8FE00
MAIN_TABLE_VA = 0x8C09FE00
MAIN_PTR_SITE = 0x12FD18
ORIG_LIMIT = 0x071C


def main():
    parser = argparse.ArgumentParser(description='Patch encoding table in 1ST_READ.BIN')
    parser.add_argument('-b', '--bin', required=True, help='Input 1ST_READ.BIN file')
    parser.add_argument('-c', '--csv', required=True, help='Input mapping CSV (mapping.csv)')
    parser.add_argument('-o', '--output', default=None, help='Output binary file (omit for in-place patching)')
    args = parser.parse_args()

    bin_path = Path(args.bin)
    csv_path = Path(args.csv)
    out_path = Path(args.output) if args.output else bin_path

    if not bin_path.exists():
        print(f'ERROR: {bin_path} not found', file=sys.stderr)
        return 1
    if not csv_path.exists():
        print(f'ERROR: {csv_path} not found', file=sys.stderr)
        return 1

    data = bytearray(bin_path.read_bytes())

    ptr = struct.unpack_from('<I', data, MAIN_PTR_SITE)[0]
    if ptr == MAIN_TABLE_VA:
        table_off = MAIN_TABLE_FILE
        print(f'Encoding table at original location: 0x{table_off:05X}')
    else:
        table_off = ptr - LOAD_BASE
        print(f'Encoding table at relocated location: 0x{table_off:05X} (ptr 0x{ptr:08X})')

    total_entries = (len(data) - table_off) // 2
    print(f'Table capacity: {total_entries} entries')

    with csv_path.open('r', encoding='utf-8') as f:
        mapping = list(csv.DictReader(f))

    patched_count = 0
    for entry in mapping:
        idx = int(entry['enc_idx'])
        hi = int(entry['sjis_hi'])
        lo = int(entry['sjis_lo'])

        off = table_off + idx * 2
        if off + 2 > len(data):
            print(f'WARNING: idx={idx} beyond file size, skipping')
            continue

        old_hi, old_lo = data[off], data[off + 1]
        new_val = (hi << 8) | lo
        old_val = (old_hi << 8) | old_lo

        if old_val == new_val:
            continue

        data[off], data[off + 1] = hi, lo
        patched_count += 1
        print(f'  idx={idx:4d}: {old_hi:02X}{old_lo:02X} -> {hi:02X}{lo:02X} ({entry["char"]})')

    if patched_count == 0:
        print('No encoding table entries need patching')
    else:
        out_path.write_bytes(bytes(data))
        print(f'Patched {patched_count} entries -> {out_path}')


if __name__ == '__main__':
    main()
