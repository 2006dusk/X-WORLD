"""读取 GGUF 元数据键（内存安全的截断版），用于确认底模与对话模板。

用法：
    python tools\peek_gguf_meta2.py <gguf 路径> [键名 ...]
"""

import struct
import sys
from pathlib import Path

T_UINT8, T_INT8, T_UINT16, T_INT16, T_UINT32, T_INT32 = 0, 1, 2, 3, 4, 5
T_FLOAT32, T_BOOL, T_STRING, T_ARRAY = 6, 7, 8, 9
T_UINT64, T_INT64, T_FLOAT64 = 10, 11, 12


def _read(fh, vtype, limit=4096):
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
        if slen > limit:
            fh.seek(slen, 1)
            return f'<string len {slen}>'
        return fh.read(slen).decode('utf-8', 'replace')
    if vtype == T_ARRAY:
        etype, = struct.unpack('<I', fh.read(4))
        count, = struct.unpack('<Q', fh.read(8))
        if etype == T_STRING:
            items = []
            for _ in range(min(count, 16)):
                items.append(_read(fh, T_STRING, limit))
            if count > 16:
                fh.seek(0, 1)
            return items
        if etype in (T_UINT8, T_INT8):
            fh.seek(count, 1)
            return f'<array u8 len {count}>'
        if etype == T_ARRAY:
            fh.seek(count * 24, 1)
            return f'<array arr len {count}>'
        fh.seek(count * 8, 1)
        return f'<array type {etype} len {count}>'
    if vtype == T_UINT64:
        return struct.unpack('<Q', fh.read(8))[0]
    if vtype == T_INT64:
        return struct.unpack('<q', fh.read(8))[0]
    if vtype == T_FLOAT64:
        return struct.unpack('<d', fh.read(8))[0]
    raise ValueError(f'unknown vtype {vtype}')


def read_meta(path: Path, wanted):
    out = {}
    with path.open('rb') as fh:
        if fh.read(4) != b'GGUF':
            return out
        version, = struct.unpack('<I', fh.read(4))
        n_tensors, n_kv = struct.unpack('<QQ', fh.read(16))
        out['gguf_version'] = version
        out['n_tensors'] = n_tensors
        out['n_kv'] = n_kv
        for _ in range(n_kv):
            klen, = struct.unpack('<Q', fh.read(8))
            if klen > 4096:
                break
            key = fh.read(klen).decode('utf-8', 'replace')
            vtype, = struct.unpack('<I', fh.read(4))
            if key in wanted:
                try:
                    out[key] = _read(fh, vtype)
                except Exception as exc:
                    out[key] = f'<error {exc}>'
                    break
            else:
                try:
                    _read(fh, vtype)
                except Exception:
                    break
    return out


if __name__ == '__main__':
    keys = ('general.name', 'general.basename', 'general.architecture', 'general.size_label',
            'general.file_type', 'general.quantization_version', 'general.license',
            'general.source.huggingface.repo', 'general.author', 'general.description',
            'general.base_model.0.name', 'general.base_model.1.name',
            'general.finetune', 'general.base_model.0.url',
            'tokenizer.chat_template')
    wanted = set(keys)
    for arg in sys.argv[1:]:
        p = Path(arg)
        print('==', p.name, f'{p.stat().st_size / 1048576:.1f} MB')
        meta = read_meta(p, wanted)
        for k in keys:
            v = meta.get(k)
            if v in (None, ''):
                continue
            s = str(v)
            if len(s) > 900:
                s = s[:900] + ' ...'
            print('  ', k, '=', s)
