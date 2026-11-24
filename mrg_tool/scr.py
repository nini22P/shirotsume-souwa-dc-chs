import os
from .common import (
    SECTOR,
    build_nam,
    encode_hed_entry,
    hed_path,
    nam_path,
    parse_nam,
    read_hed_entries,
    xor_crypt,
)


def unpack(mrg_path: str, out_dir: str) -> None:
    entries = read_hed_entries(hed_path(mrg_path))
    with open(mrg_path, 'rb') as f:
        mrg = f.read()

    names = None
    nam = nam_path(mrg_path)
    if os.path.exists(nam):
        with open(nam, 'rb') as f:
            names = parse_nam(f.read(), len(entries))

    os.makedirs(out_dir, exist_ok=True)
    for i, (offset, size) in enumerate(entries):
        data = xor_crypt(mrg[offset : offset + size])
        name = names[i] if names and names[i] else f'file_{i:04d}'
        safe = name.replace('/', '_').replace('\\', '_')
        fpath = os.path.join(out_dir, safe) + '.txt'
        with open(fpath, 'wb') as out:
            out.write(data)
        print(f'  [{i:4d}] off={offset:9d}({offset // SECTOR:5d}s)  sz={size:7d}  -> {os.path.basename(fpath)}')

    meta = [
        f'{i}|{names[i] if names and names[i] else f"file_{i:04d}"}\n'
        for i in range(len(entries))
    ]
    with open(os.path.join(out_dir, '_meta.txt'), 'w', encoding='utf-8') as f:
        f.writelines(meta)
    print(f'\n{len(entries)} files extracted to {out_dir}/')


def repack(in_dir: str, mrg_path: str) -> None:
    hed = hed_path(mrg_path)
    meta_path = os.path.join(in_dir, '_meta.txt')
    if not os.path.exists(meta_path):
        print('ERROR: _meta.txt not found in input directory')
        return

    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = [line.strip() for line in f if line.strip()]

    entries: list[tuple[int, str]] = []
    for line in meta:
        idx_name = line.split('|', 1)
        idx = int(idx_name[0])
        name = idx_name[1] if len(idx_name) > 1 else f'file_{idx:04d}'
        entries.append((idx, name))

    files: list[bytes] = []
    names: list[str] = []
    for _, name in entries:
        safe = name.replace('/', '_').replace('\\', '_')
        fpath = os.path.join(in_dir, safe)
        if not os.path.exists(fpath):
            fpath_txt = fpath + '.txt'
            if os.path.exists(fpath_txt):
                fpath = fpath_txt
            else:
                print(f'WARNING: {safe} not found, using empty')
                files.append(b'')
                names.append(name)
                continue
        with open(fpath, 'rb') as fh:
            data = fh.read()
        files.append(xor_crypt(data))
        names.append(name)

    offset = 0
    hed_buf = bytearray()
    with open(mrg_path, 'wb') as mrg:
        for data in files:
            hed_buf += encode_hed_entry(offset, len(data))
            mrg.write(data)
            nsize = (len(data) + SECTOR - 1) // SECTOR * SECTOR
            if nsize > len(data):
                mrg.write(b'\x00' * (nsize - len(data)))
            print(f'  off={offset:9d}({offset // SECTOR:5d}s)  sz={len(data):7d}')
            offset += nsize

    hed_buf += b'\xFF' * 16
    with open(hed, 'wb') as f:
        f.write(hed_buf)
    with open(nam_path(mrg_path), 'wb') as f:
        f.write(build_nam(names))
    print(f'\n{len(files)} files repacked -> {mrg_path} + {hed}')
