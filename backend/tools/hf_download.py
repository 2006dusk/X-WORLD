r"""从 HF 镜像下载 GGUF 权重，逐文件校验字节数。

背景：hf-mirror 部分仓库不遵守 Range 头，断点续传会写出超过 Content-Length
的损坏文件。下载后必须按字节数核对，不一致就删掉重下。

镜像顺序：先 hf-mirror（国内快，有概率 429 限流），再回落 huggingface.co 直连。

用法：
    python backend\tools\hf_download.py <仓库> <文件名> [目标目录]

例：
    python backend\tools\hf_download.py \
        mradermacher/Qwen2-7B-Multilingual-RP-GGUF \
        Qwen2-7B-Multilingual-RP.Q6_K.gguf

不带文件名时只列出仓库里的 GGUF 与字节数。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

MIRRORS = ["https://hf-mirror.com", "https://huggingface.co"]
DEFAULT_DIR = Path(r"E:\X-WORLD\models\hf")
CHUNK = 1 << 20


def _tree(client: httpx.Client, base: str, repo: str) -> list[dict]:
    """取仓库文件树。镜像会限流，失败要退避重试。"""
    url = base + "/api/models/" + repo + "/tree/main"
    last = ""
    for attempt in range(1, 6):
        try:
            resp = client.get(url, params={"recursive": "1"}, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001 - 限流退避后重试
            last = str(exc)
            print("    取文件树第 %d 次失败：%s" % (attempt, last), flush=True)
            time.sleep(10 * attempt)
    raise RuntimeError("取文件树失败：" + last)


def _tree_any(client: httpx.Client, repo: str) -> tuple[list[dict], str]:
    """依次尝试各镜像，返回文件树与用到的镜像地址。"""
    last_error = ""
    for base in MIRRORS:
        try:
            tree = _tree(client, base, repo)
        except Exception as exc:  # noqa: BLE001 - 换下一个镜像
            last_error = str(exc)
            print("    镜像 %s 不可用：%s" % (base, last_error), flush=True)
            continue
        if tree:
            print("    镜像 %s 可用，文件 %d 个" % (base, len(tree)), flush=True)
            return tree, base
    raise RuntimeError("所有镜像都取不到文件树：" + last_error)


def _download(client: httpx.Client, base: str, repo: str, filename: str, size: int, dest: Path) -> bool:
    """下载一个文件，按字节数核对。"""
    url = base + "/" + repo + "/resolve/main/" + filename
    temp = dest.with_suffix(dest.suffix + ".part")
    got = 0
    try:
        with client.stream("GET", url, timeout=300.0, follow_redirects=True) as resp:
            resp.raise_for_status()
            mode = "ab"
            headers = {}
            if temp.exists() and temp.stat().st_size < size:
                headers["Range"] = "bytes=%d-" % temp.stat().st_size
                mode = "ab"
            elif temp.exists():
                temp.unlink()
            with open(temp, mode) as handle:
                if mode == "ab":
                    got = temp.stat().st_size
                for chunk in resp.iter_bytes(CHUNK):
                    handle.write(chunk)
                    got += len(chunk)
                    if got % (64 * CHUNK) < CHUNK:
                        print("      已下 %.2f GB / %.2f GB" % (got / 1024 ** 3, size / 1024 ** 3), flush=True)
    except Exception as exc:  # noqa: BLE001 - 下载失败只记录
        print("    下载中断：%s" % exc, flush=True)
        return False
    actual = temp.stat().st_size
    if actual != size:
        print(
            "    字节数不符：期望 %d 实得 %d，删除重下" % (size, actual),
            flush=True,
        )
        temp.unlink()
        return False
    temp.replace(dest)
    return True


def main() -> int:
    """列目录或下载。"""
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    repo = sys.argv[1]
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_DIR
    with httpx.Client(follow_redirects=True, headers={"User-Agent": "xworld-downloader/1.0"}) as client:
        try:
            tree, base = _tree_any(client, repo)
        except Exception as exc:  # noqa: BLE001
            print("列仓库失败 %s：%s" % (repo, exc))
            return 1
        files = [
            node
            for node in tree
            if str(node.get("path", "")).lower().endswith(".gguf")
        ]
        if len(sys.argv) < 3:
            for node in files:
                print("%12d  %s" % (int(node.get("size") or 0), node.get("path")))
            return 0
        filename = sys.argv[2]
        target = next((n for n in files if n.get("path") == filename), None)
        if target is None:
            print("仓库里没有 %s" % filename)
            for node in files:
                print("%12d  %s" % (int(node.get("size") or 0), node.get("path")))
            return 1
        size = int(target.get("size") or 0)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / filename
        if dest.exists() and dest.stat().st_size == size:
            print("已存在且字节数一致：%s" % dest)
            return 0
        print("下载 %s（%.2f GB）→ %s" % (filename, size / 1024 ** 3, dest), flush=True)
        for attempt in range(1, 4):
            print("  第 %d 次尝试" % attempt, flush=True)
            for base_try in MIRRORS:
                if _download(client, base_try, repo, filename, size, dest):
                    print("  完成：%s（%d 字节）" % (dest, size), flush=True)
                    return 0
                print("    镜像 %s 本轮失败，换下一个" % base_try, flush=True)
            time.sleep(15 * attempt)
        print("  三次均失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
