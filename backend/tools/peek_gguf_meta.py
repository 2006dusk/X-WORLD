import struct, sys, pathlib

# GGUF v3 value types
T_UINT8, T_INT8, T_UINT16, T_INT16, T_UINT32, T_INT32 = 0, 1, 2, 3, 4, 5
T_FLOAT32, T_BOOL, T_STRING, T_ARRAY = 6, 7, 8, 9
T_UINT64, T_INT64, T_FLOAT64 = 10, 11, 12


def _read(fh, vtype):
    if vtype == T_UINT8:
        return struct.unpack('<B', fh.read(1))[0]
    if vtype == T_INT8:
        return struct.unpack('<b', fh.read(1))[0]
    if vtype == T_UINT16:
        return struct.unpack('<H', fh.read(2))[0]
    if vtype == T_INT16:
        return struct.unpack('<h', fh.read(2))[0]
    if vtype == T_UINT32:
        return struct.unpack('<I', fh.read(4))[0]
    if vtype == T_INT32:
        return struct.unpack('<i', fh.read(4))[0]
    if vtype == T_FLOAT32:
        return struct.unpack('<f', fh.read(4))[0]
    if vtype == T_BOOL:
        return struct.unpack('<?', fh.read(1))[0]
    if vtype == T_STRING:
        slen, = struct.unpack('<Q', fh.read(8))
        return fh.read(slen).decode('utf-8', 'replace')
    if vtype == T_ARRAY:
        etype, = struct.unpack('<I', fh.read(4))
        count, = struct.unpack('<Q', fh.read(8))
        if etype == T_STRING:
            items = []
            for _ in range(min(count, 32)):
                items.append(_read(fh, T_STRING))
            return items
        if etype in (T_UINT8, T_INT8):
            data = fh.read(count)
            return list(data)
        return f'<array type {etype} len {count}>'
    if vtype == T_UINT64:
        return struct.unpack('<Q', fh.read(8))[0]
    if vtype == T_INT64:
        return struct.unpack('<q', fh.read(8))[0]
    if vtype == T_FLOAT64:
        return struct.unpack('<d', fh.read(8))[0]
    raise ValueError(f'unknown vtype {vtype}')


def read_meta(path: pathlib.Path):
    with path.open('rb') as fh:
        if fh.read(4) != b'GGUF':
            return {}
        version, = struct.unpack('<I', fh.read(4))
        n_tensors, n_kv = struct.unpack('<QQ', fh.read(16))
        out = {'gguf_version': version, 'n_tensors': n_tensors, 'n_kv': n_kv}
        for _ in range(n_kv):
            klen, = struct.unpack('<Q', fh.read(8))
            key = fh.read(klen).decode('utf-8', 'replace')
            vtype, = struct.unpack('<I', fh.read(4))
            try:
                out[key] = _read(fh, vtype)
            except Exception as exc:
                out['_error'] = f'{key}: {exc}'
                break
        return out


if __name__ == '__main__':
    keys = ('general.name', 'general.basename', 'general.architecture', 'general.size_label',
            'general.file_type', 'general.quantization_version', 'general.license',
            'general.license_name', 'general.source.huggingface.repo', 'general.author',
            'general.description', 'tokenizer.chat_template', '_error')
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        print('==', p.name, f'{p.stat().st_size / 1024 / 1024:.1f} MB')
        meta = read_meta(p)
        for k in keys:
            v = meta.get(k)
            if v in (None, ''):
                continue
            if isinstance(v, str) and len(v) > 240:
                v = v[:240] + '...'
            print('  ', k, '=', v)
        print('   n_tensors', meta.get('n_tensors'), 'n_kv', meta.get('n_kv'), 'gguf_ver', meta.get('gguf_version'))
