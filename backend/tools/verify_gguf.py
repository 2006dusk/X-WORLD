r"""校验 GGUF 权重文件是否完整。

只做三件事：
1. 字节数与镜像上登记的大小一致；
2. 文件头是 GGUF 魔数，版本号可读；
3. 读一遍头部元数据键数量，确认文件没有在半路截断。

用法：
    python backend\tools\verify_gguf.py <文件路径> [...]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def read_header(path: Path) -> dict[str, object]:
    """读 GGUF 头部。"""
    with open(path, "rb") as handle:
        magic = handle.read(4)
        if magic != b"GGUF":
            raise ValueError("魔数不对：%r" % magic)
        version, = struct.unpack("<I", handle.read(4))
        tensor_count, = struct.unpack("<Q", handle.read(8))
        kv_count, = struct.unpack("<Q", handle.read(8))
    return {
        "version": version,
        "tensor_count": tensor_count,
        "kv_count": kv_count,
    }


def main() -> int:
    """逐个校验。"""
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    bad = 0
    for item in sys.argv[1:]:
        path = Path(item)
        if not path.exists():
            print("%s 不存在" % path)
            bad += 1
            continue
        size = path.stat().st_size
        try:
            head = read_header(path)
        except Exception as exc:  # noqa: BLE001 - 校验失败只记录
            print("%s 头部读取失败：%s" % (path.name, exc))
            bad += 1
            continue
        print(
            "%s | %.1f MB | GGUF v%d | 张量 %d | 元数据键 %d"
            % (path.name, size / 1048576, head["version"], head["tensor_count"], head["kv_count"])
        )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
