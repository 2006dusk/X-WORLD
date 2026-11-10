r"""读 GGUF 元数据里的聊天模板与基本信息。

用途：模型输出里出现模板标记（INST、<|im_start|> 等）时，
要判断是「模型不会用」还是「服务端套错了模板」，
先看权重里到底存了哪套模板。

用法：
    python backend\tools\read_gguf_meta.py <文件路径> [...]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

VALUE_TYPES = {
    0: "uint8",
    1: "int8",
    2: "uint16",
    3: "int16",
    4: "uint32",
    5: "int32",
    6: "float32",
    7: "bool",
    8: "string",
    9: "array",
    10: "uint64",
    11: "int64",
    12: "float64",
}

SIZES = {
    0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8,
}

INTEREST = (
    "general.name",
    "general.basename",
    "general.architecture",
    "general.size_label",
    "general.finetune",
    "general.quantization_version",
    "tokenizer.chat_template",
    "tokenizer.gguf.tokens",
)


def read_string(handle) -> str:
    length, = struct.unpack("<Q", handle.read(8))
    raw = handle.read(length)
    return raw.decode("utf-8", errors="replace")


def skip_value(handle, vtype: int) -> None:
    """跳过一个值。"""
    if vtype == 8:
        read_string(handle)
        return
    if vtype == 9:
        item_type, = struct.unpack("<I", handle.read(4))
        count, = struct.unpack("<Q", handle.read(8))
        for _ in range(count):
            skip_value(handle, item_type)
        return
    handle.read(SIZES[vtype])


def read_meta(path: Path) -> dict[str, object]:
    """读头部元数据。"""
    out: dict[str, object] = {}
    with open(path, "rb") as handle:
        if handle.read(4) != b"GGUF":
            raise ValueError("不是 GGUF 文件")
        handle.read(4)
        handle.read(8)
        kv_count, = struct.unpack("<Q", handle.read(8))
        for _ in range(kv_count):
            key = read_string(handle)
            vtype, = struct.unpack("<I", handle.read(4))
            if key in INTEREST:
                if vtype == 8:
                    out[key] = read_string(handle)
                elif vtype == 9:
                    item_type, = struct.unpack("<I", handle.read(4))
                    count, = struct.unpack("<Q", handle.read(8))
                    if key.endswith("tokens"):
                        out[key + "_count"] = count
                    for _ in range(count):
                        skip_value(handle, item_type)
                else:
                    raw = handle.read(SIZES[vtype])
                    out[key] = raw
            else:
                skip_value(handle, vtype)
    return out


def main() -> int:
    """逐个打印。"""
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for item in sys.argv[1:]:
        path = Path(item)
        print("=== " + path.name)
        try:
            meta = read_meta(path)
        except Exception as exc:  # noqa: BLE001 - 读取失败只记录
            print("    读取失败：%s" % exc)
            continue
        for key in INTEREST:
            if key not in meta:
                continue
            value = meta[key]
            if key == "tokenizer.chat_template":
                text = str(value)
                marks = []
                for mark in ("<|im_start|>", "<|im_end|>", "[INST]", "[/INST]", "<<SYS>>", "<start_of_turn>"):
                    if mark in text:
                        marks.append(mark)
                print("    %s：%d 字符，模板标记 %s" % (key, len(text), marks or "无"))
                print("      开头：%s" % text[:160].replace("\n", "\\n"))
            elif key.endswith("tokens"):
                print("    %s：%s" % (key, value))
            else:
                print("    %s：%s" % (key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
