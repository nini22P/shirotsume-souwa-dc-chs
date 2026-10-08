import argparse, csv, sys, unicodedata
from pathlib import Path
from sbox import decrypt_text, encrypt_text


def list_script_files(scripts_dir):
    meta = Path(scripts_dir) / '_meta.txt'
    names = []
    with open(meta, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('|')
            if len(parts) >= 2 and parts[1]:
                names.append(parts[1] + '.txt')
    return [Path(scripts_dir) / n for n in names if (Path(scripts_dir) / n).exists()]


def load_mapping(mapping_path):
    mapping = {}
    with open(mapping_path, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            ch = row['char']
            if ch:
                mapping[ch] = (int(row['sjis_hi']), int(row['sjis_lo']))
    return mapping


def encode_by_mapping(text, mapping):
    out = bytearray()
    for ch in text:
        if ch in mapping:
            out += bytes(mapping[ch])
        elif unicodedata.east_asian_width(ch) in ('Na', 'H'):
            out += ch.encode('cp932')
        else:
            try:
                out += ch.encode('cp932')
            except UnicodeEncodeError:
                raise ValueError(
                    f"字符 {ch} U+{ord(ch):04X} 未在 mapping 中且无法 cp932 编码"
                )
    return bytes(out)


def is_sjis_lead(b):
    return 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC

def split_statements(data):
    parts = []
    start = 0
    i = 0
    while i < len(data):
        if data[i:i+1] == b'"':
            i += 1
            while i < len(data):
                if is_sjis_lead(data[i]) and i + 1 < len(data):
                    i += 2
                    continue
                if data[i:i+1] == b'\\' and i + 1 < len(data):
                    i += 2
                    continue
                if data[i:i+1] == b'"':
                    i += 1
                    break
                i += 1
            continue
        if data[i:i+1] == b';':
            parts.append(data[start:i])
            start = i + 1
        i += 1
    if start < len(data):
        parts.append(data[start:])
    elif data.endswith(b';'):
        parts.append(b'')
    return parts

def iter_strings(data):
    i = 0
    while i < len(data):
        if data[i:i+1] != b'"':
            i += 1
            continue
        start = i
        i += 1
        while i < len(data):
            if is_sjis_lead(data[i]) and i + 1 < len(data):
                i += 2
                continue
            if data[i:i+1] == b'\\' and i + 1 < len(data):
                i += 2
                continue
            if data[i:i+1] == b'"':
                yield (start, i, data[start+1:i])
                i += 1
                break
            i += 1
        else:
            break


def _unescape(data: bytes) -> bytes:
    return data.replace(b'\\"', b'"')


def _escape(s):
    return s.replace('"', '\\"')


def do_extract(scripts_dir, out_csv):
    txt_files = [f for f in list_script_files(scripts_dir) if f.name != '_meta.txt']
    rows = []

    for fpath in txt_files:
        fname = fpath.name
        raw = fpath.read_bytes()
        stmts = split_statements(raw)

        for si, stmt in enumerate(stmts):
            if not stmt.strip():
                continue
            paren = stmt.find(b'(')
            if paren < 0:
                continue
            try:
                func_name = stmt[:paren].decode('ascii')
            except:
                continue
            if func_name not in ('_CBll', '_CBlX', '_CTxt'):
                continue

            strs = list(iter_strings(stmt))
            if not strs:
                continue

            _, _, ctx_raw = strs[0]
            context = _unescape(ctx_raw).decode('cp932', errors='replace')

            if func_name == '_CBlX':
                texts = []
                for _, _, raw_str in strs:
                    try:
                        texts.append(raw_str.decode('cp932'))
                    except:
                        texts.append('')
                if len(texts) >= 3 and texts[2] == 'invalid':
                    idx = 3 if len(strs) > 3 else -1
                else:
                    idx = 2 if len(strs) > 2 else -1
                if idx == -1 or idx >= len(strs):
                    text_encrypted = b''
                else:
                    _, _, text_encrypted = strs[idx]
                    text_encrypted = _unescape(text_encrypted)
            else:
                _, _, text_encrypted = strs[-1] if len(strs) >= 2 else (0, 0, b'')
                text_encrypted = _unescape(text_encrypted)

            if text_encrypted:
                try:
                    text_decoded = decrypt_text(text_encrypted).decode('cp932')
                except Exception:
                    text_decoded = text_encrypted.decode('cp932', errors='replace')
            else:
                text_decoded = ''

            if text_decoded:
                rows.append([fname, si, context, text_decoded, ''])

    header = ['file', 'stmt', 'context', 'text', 'translation']
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    print(f'Extracted {len(rows)} strings to {out_csv}')


def build_translation_map(csv_path):
    tmap = {}
    with open(csv_path, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            trans = row.get('translation', '').strip()
            if not trans:
                continue
            key = (row['file'], int(row['stmt']))
            tmap[key] = trans
    return tmap


def apply_cbll(stmt, translation, mapping):
    strs = list(iter_strings(stmt))
    if not strs:
        return None
    ss, se, old_text_enc = strs[-1]
    new_text_enc = encrypt_text(encode_by_mapping(_escape(translation), mapping))
    return stmt[:ss+1] + new_text_enc + stmt[se:]

def apply_cblx(stmt, translation, mapping):
    strs = list(iter_strings(stmt))
    if len(strs) < 3:
        return None
    texts = []
    for ss, se, raw in strs:
        try:
            texts.append(raw.decode('cp932'))
        except:
            texts.append('')
    if len(texts) >= 3 and texts[2] == 'invalid':
        idx = 3
    else:
        idx = 2
    if idx >= len(strs):
        return None
    ss, se, _ = strs[idx]
    new_text_enc = encrypt_text(encode_by_mapping(_escape(translation), mapping))
    return stmt[:ss+1] + new_text_enc + stmt[se:]

def do_import(scripts_dir, csv_path, mapping):
    tmap = build_translation_map(csv_path)
    if not tmap:
        print('No translations found in CSV (translation column empty)')
        return

    stats = {'processed': 0, 'skipped': 0}
    for fpath in list_script_files(scripts_dir):
        fname = fpath.name

        with open(fpath, 'rb') as f:
            raw = f.read()

        stmts = split_statements(raw)
        modified = False

        for si, stmt in enumerate(stmts):
            if not stmt.strip():
                stats['skipped'] += 1
                continue

            paren = stmt.find(b'(')
            if paren < 0:
                stats['skipped'] += 1
                continue
            func = stmt[:paren]
            try:
                func_name = func.decode('ascii')
            except:
                stats['skipped'] += 1
                continue

            key = (fname, si)
            trans = tmap.get(key)
            if trans is None:
                stats['skipped'] += 1
                continue

            try:
                if func_name in ('_CBll', '_CTxt'):
                    new_stmt = apply_cbll(stmt, trans, mapping)
                elif func_name == '_CBlX':
                    new_stmt = apply_cblx(stmt, trans, mapping)
                else:
                    stats['skipped'] += 1
                    continue
            except Exception as e:
                old_text = stmt.decode('cp932', errors='replace')
                print(f'ERROR: {fname}:{si} {func_name} {type(e).__name__}: {e}')
                print(f'  old: {old_text[:120]}')
                print(f'  new: {trans[:120]}')
                sys.stdout.flush()
                raise

            if new_stmt is not None and new_stmt != stmt:
                stmts[si] = new_stmt
                modified = True
                stats['processed'] += 1
            else:
                stats['skipped'] += 1

        if modified:
            new_raw = b';'.join(stmts)
            with open(fpath, 'wb') as f:
                f.write(new_raw)
            print(f'  Updated: {fname}')

    print(f'Done: {stats["processed"]} translated, {stats["skipped"]} skipped')


def do_decrypt(scripts_dir):
    from sbox import decrypt_text
    fcount = scount = 0
    for fpath in list_script_files(scripts_dir):
        with open(fpath, 'rb') as f:
            raw = f.read()
        stmts = split_statements(raw)
        modified = False
        for si, stmt in enumerate(stmts):
            if not stmt.strip():
                continue
            paren = stmt.find(b'(')
            if paren < 0:
                continue
            try:
                func_name = stmt[:paren].decode('ascii')
            except:
                continue
            if func_name not in ('_CBll', '_CBlX', '_CTxt'):
                continue
            strs = list(iter_strings(stmt))
            if not strs:
                continue

            if func_name == '_CBlX':
                texts = []
                for _, _, raw_str in strs:
                    try:
                        texts.append(raw_str.decode('cp932'))
                    except:
                        texts.append('')
                if len(texts) >= 3 and texts[2] == 'invalid':
                    idx = 3 if len(strs) > 3 else -1
                else:
                    idx = 2 if len(strs) > 2 else -1
                if idx == -1 or idx >= len(strs):
                    continue
            else:
                idx = -1
                if len(strs) == 0:
                    continue

            ss, se, old = strs[idx]
            try:
                decrypted = decrypt_text(old)
            except Exception:
                continue
            if decrypted == old:
                continue
            stmts[si] = stmt[:ss + 1] + decrypted + stmt[se:]
            modified = True
            scount += 1
        if modified:
            with open(fpath, 'wb') as f:
                f.write(b';'.join(stmts))
            fcount += 1
            print(f'  Updated: {fpath.name}')
    print(f'Done: {fcount} files, {scount} strings decrypted')


def main():
    p = argparse.ArgumentParser(description='Script tool')
    sub = p.add_subparsers(dest='command', required=True)

    p_extract = sub.add_parser('extract', help='Extract translatable strings to CSV')
    p_extract.add_argument('-i', '--input', required=True, help='Input scripts directory')
    p_extract.add_argument('-o', '--output', required=True, help='Output CSV path')

    p_import = sub.add_parser('import', help='Import translations from CSV into scripts')
    p_import.add_argument('-i', '--input', required=True, help='Input scripts directory')
    p_import.add_argument('-c', '--csv', required=True, help='Input CSV file with translations')
    p_import.add_argument('-m', '--mapping', required=True, help='Mapping CSV path for per-char encoding')

    p_decrypt = sub.add_parser('decrypt', help='Decrypt extended script text in-place')
    p_decrypt.add_argument('-i', '--input', required=True, help='Input scripts directory')

    args = p.parse_args()
    
    match args.command:
        case 'extract':
            do_extract(args.input, args.output)
        case 'import':
            mapping = load_mapping(args.mapping) if args.mapping else {}
            do_import(args.input, args.csv, mapping)
        case 'decrypt':
            do_decrypt(args.input)
        case _:
            p.print_help()
            


if __name__ == '__main__':
    main()
