import argparse, os
from mrg import SECTOR, xor_crypt, encode_hed_entry, read_hed_entries, parse_nam, build_nam


def _hed_path(mrg_path):
    root, _ = os.path.splitext(mrg_path)
    return root + '.HED'


def _nam_path(mrg_path):
    root, _ = os.path.splitext(mrg_path)
    return root + '.NAM'


def unpack(mrg_path, out_dir):
    hed_path = _hed_path(mrg_path)
    entries = read_hed_entries(hed_path)
    with open(mrg_path, 'rb') as f:
        mrg = f.read()

    names = None
    nam_path = _nam_path(mrg_path)
    if os.path.exists(nam_path):
        with open(nam_path, 'rb') as f:
            names = parse_nam(f.read(), len(entries))

    os.makedirs(out_dir, exist_ok=True)
    for i, (offset, size) in enumerate(entries):
        data = xor_crypt(mrg[offset:offset + size])
        name = names[i] if names and names[i] else f'file_{i:04d}'
        safe = name.replace('/', '_').replace('\\', '_')
        fpath = os.path.join(out_dir, safe) + '.txt'
        with open(fpath, 'wb') as out:
            out.write(data)
        print(f'  [{i:4d}] off={offset:9d}({offset//SECTOR:5d}s)  sz={size:7d}  -> {os.path.basename(fpath)}')

    meta = []
    for i, (offset, size) in enumerate(entries):
        name = names[i] if names and names[i] else f'file_{i:04d}'
        meta.append(f'{i}|{name}\n')
    with open(os.path.join(out_dir, '_meta.txt'), 'w', encoding='utf-8') as f:
        f.writelines(meta)
    print(f'\n{len(entries)} files extracted to {out_dir}/')


def repack(in_dir, mrg_path):
    hed_path = _hed_path(mrg_path)
    meta_path = os.path.join(in_dir, '_meta.txt')
    if not os.path.exists(meta_path):
        print('ERROR: _meta.txt not found in input directory')
        return

    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = [line.strip() for line in f if line.strip()]

    entries = []
    for line in meta:
        idx_name = line.split('|', 1)
        idx = int(idx_name[0])
        name = idx_name[1] if len(idx_name) > 1 else f'file_{idx:04d}'
        entries.append((idx, name))

    files = []
    names = []
    for idx, name in entries:
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
            print(f'  off={offset:9d}({offset//SECTOR:5d}s)  sz={len(data):7d}')
            offset += nsize

    hed_buf += b'\xFF' * 16
    with open(hed_path, 'wb') as f:
        f.write(hed_buf)
    with open(_nam_path(mrg_path), 'wb') as f:
        f.write(build_nam(names))
    print(f'\n{len(files)} files repacked -> {mrg_path} + {hed_path}')


def main():
    p = argparse.ArgumentParser(description='SCR MRG tool')
    sub = p.add_subparsers(dest='command', required=True)

    p_unpack = sub.add_parser('unpack', help='Extract scripts from SCR.MRG')
    p_unpack.add_argument('-i', '--input', required=True, help='Input .mrg file')
    p_unpack.add_argument('-o', '--output', default='_unpacked', help='Output script directory')

    p_repack = sub.add_parser('repack', help='Repack scripts to SCR.MRG')
    p_repack.add_argument('-i', '--input', default='_unpacked', help='Input script directory')
    p_repack.add_argument('-o', '--output', required=True, help='Output .mrg file')


    args = p.parse_args()
    
    match args.command:
        case 'unpack':
            unpack(args.input, args.output)
        case 'repack':
            repack(args.input, args.output)
        case _:
            p.print_help()


if __name__ == '__main__':
    main()
