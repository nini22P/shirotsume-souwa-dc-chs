import argparse
import struct
import sys
from pathlib import Path

LOAD_BASE = 0x8C010000

MAIN_TABLE_FILE = 0x8FE00
MAIN_TABLE_VA = 0x8C09FE00
ORIG_LIMIT = 0x071C  # 1820

# 1ST_READ.BIN 内部未被引用的全零区域 (0x142084..0x143CF8, 7284 bytes)，避开 0x146084 之后的 .bss 清零区
RELOC_FILE = 0x142084
RELOC_SIZE = 0x143CF8 - RELOC_FILE
NEW_LIMIT = 2880  # 24 页 20x24 (76..99) 的容量上限，5760 bytes

LIMIT_SITES = [0x5C262, 0x5C5CA, 0x5C86A, 0x5D024, 0x5D602]
MAIN_PTR_SITE = 0x12FD18

# 字体描述符表 (0x13073C) 中的起始纹理页号
FONT_PAGE_PATCHES = {
    0x130748: (2, 57, '18x22 page_start (slot A)'),
    0x13074C: (2, 57, '18x22 page_start (slot B)'),
    0x130750: (17, 76, '20x24 page_start'),
}

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
    new_table_bytes = NEW_LIMIT * 2

    require(MAIN_TABLE_FILE + table_bytes <= len(data), 'Original main table is outside 1ST_READ.BIN')
    require(new_table_bytes <= RELOC_SIZE, f'New table ({new_table_bytes}B) exceeds free block ({RELOC_SIZE}B)')
    require(
        all(b == 0 for b in data[RELOC_FILE : RELOC_FILE + new_table_bytes]),
        f'Relocation target 0x{RELOC_FILE:05X} is not zero-filled',
    )

    old_main_ptr = struct.unpack_from('<I', data, MAIN_PTR_SITE)[0]
    require(
        old_main_ptr == MAIN_TABLE_VA,
        f'Unexpected main-font pointer at 0x{MAIN_PTR_SITE:05X}: 0x{old_main_ptr:08X}',
    )

    for off in LIMIT_SITES:
        old = struct.unpack_from('<H', data, off)[0]
        require(old == ORIG_LIMIT, f'Unexpected limit at 0x{off:05X}: 0x{old:04X}')

    for off, (old_page, _, desc) in FONT_PAGE_PATCHES.items():
        cur = struct.unpack_from('<H', data, off)[0]
        require(cur == old_page, f'Unexpected {desc} at 0x{off:05X}: {cur}')

    # 原表直接拷贝到重定位起始地址（不前置零填充，保持 0..1807 索引不变）
    orig_table = data[MAIN_TABLE_FILE : MAIN_TABLE_FILE + table_bytes]
    patched[RELOC_FILE : RELOC_FILE + table_bytes] = orig_table

    new_main_va = LOAD_BASE + RELOC_FILE
    struct.pack_into('<I', patched, MAIN_PTR_SITE, new_main_va)

    for off in LIMIT_SITES:
        struct.pack_into('<H', patched, off, NEW_LIMIT)

    for off, (_, new_page, _) in FONT_PAGE_PATCHES.items():
        struct.pack_into('<H', patched, off, new_page)

    require(
        bytes(patched[RELOC_FILE : RELOC_FILE + table_bytes]) == orig_table,
        'Copied main table verification failed',
    )
    require(
        struct.unpack_from('<I', patched, MAIN_PTR_SITE)[0] == new_main_va,
        'Patched main-font pointer verification failed',
    )
    for off in LIMIT_SITES:
        require(
            struct.unpack_from('<H', patched, off)[0] == NEW_LIMIT,
            f'Limit update failed at 0x{off:05X}',
        )

    print(f'  main ptr  0x{MAIN_PTR_SITE:05X}: 0x{MAIN_TABLE_VA:08X} -> 0x{new_main_va:08X} (file 0x{RELOC_FILE:05X})')
    for off in LIMIT_SITES:
        print(f'  limit 0x{off:05X} ({LIMIT_DESC.get(off, "?")}): 0x{ORIG_LIMIT:04X} -> 0x{NEW_LIMIT:04X} ({NEW_LIMIT})')
    for off, (old_page, new_page, desc) in FONT_PAGE_PATCHES.items():
        print(f'  font  0x{off:05X} ({desc}): {old_page} -> {new_page}')
    return bytes(patched)


def main() -> None:
    parser = argparse.ArgumentParser(description='Patch 1ST_READ.BIN font table and page starts')
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
