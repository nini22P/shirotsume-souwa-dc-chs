import struct

SECTOR = 2048


def xor_crypt(data):
    res = bytearray(len(data))
    key = 0x6C
    for i, b in enumerate(data):
        res[i] = b ^ (key & 0xFF)
        key = (key * 0x4D + 0x35) & 0xFFFFFFFF
    return bytes(res)


def parse_hed_entry(entry):
    off_low, off_high, sz_low, sz_high = struct.unpack_from('<HHHH', entry, 0)
    if off_low == 0xFFFF and off_high == 0xFFFF:
        return None
    if (off_high << 4) & 0xFFFF:
        return None
    offset = ((off_low + (off_high << 4)) & 0xFFFFFFFF) << 11
    size = ((sz_low - 1) << 11) | (sz_high & 0x7FF) if sz_low else 0
    return offset, size


def encode_hed_entry(offset, size):
    sector = offset >> 11
    off_low = sector & 0xFFFF
    off_high = (sector - off_low) >> 4
    sz_low = (size >> 11) + 1
    sz_high = size & 0xFFFF
    return struct.pack('<HHHH', off_low, off_high, sz_low, sz_high)


def read_hed_entries(path):
    with open(path, 'rb') as f:
        hed = f.read()
    entries = []
    for i in range(0, len(hed), 8):
        e = parse_hed_entry(hed[i:i + 8])
        if e is None:
            continue
        entries.append(e)
    return entries


def parse_nam(data, count):
    names = []
    esize = len(data) // count if count else 32
    for i in range(count):
        chunk = data[i * esize:(i + 1) * esize]
        null = chunk.find(b'\x00')
        name = chunk[:null].decode('cp932', errors='replace') if null >= 0 else ''
        names.append(name)
    return names


def build_nam(names):
    buf = bytearray()
    for name in names:
        enc = name.encode('cp932', errors='replace') + b'\x00'
        if len(enc) > 32:
            enc = enc[:31] + b'\x00'
        buf += enc.ljust(32, b'\x00')
    return bytes(buf)
