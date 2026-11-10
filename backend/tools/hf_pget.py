r"""并行分段下载 GGUF 权重。

背景：
- 串行单连接从 hf-mirror 取大文件，实测只有 1.4 MB/s；同一文件用 Range
  请求单段拉取能到 11 MB/s，所以瓶颈在单条连接，不在带宽。
- hf-mirror 部分仓库不遵守 Range 头（响应 200 且从 0 开始），
  并行写会写坏文件，因此先检查响应码，206 才走并行，否则退回串行。

做法：
- 先取仓库文件树拿到精确字节数；
- 把文件切成若干块，多线程用 Range 拉取，写到同一个预分配文件的对应偏移；
- 断点续传：完成的块记在同名的 .chunks.json 里，重跑只补缺块；
- 结束后按字节数与抽样内容核对，通过才改名成正式文件。

用法：
    python backend\tools\hf_pget.py <仓库> <文件名> [目标目录]
    python backend\tools\hf_pget.py --list <仓库>
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
TOOLS_DIR = Path(__file__).resolve().parent

if str(TOOLS_DIR) not in sys.path:

    sys.path.insert(0, str(TOOLS_DIR))

from local_paths import local_path  # noqa: E402


MIRRORS = ["https://hf-mirror.com", "https://huggingface.co"]
DEFAULT_DIR = Path(local_path("models_hf_dir"))
CHUNK = 1 << 20
BLOCK = 32 * CHUNK
WORKERS = 6


def _tree(client: httpx.Client, repo: str) -> tuple[list[dict], str]:
    """取文件树，顺带试镜像。"""
    last = ""
    for base in MIRRORS:
        for attempt in range(1, 4):
            try:
                resp = client.get(
                    base + "/api/models/" + repo + "/tree/main",
                    params={"recursive": "1"},
                    timeout=60.0,
                )
                resp.raise_for_status()
                data = resp.json()
                tree = data if isinstance(data, list) else []
                if tree:
                    return tree, base
            except Exception as exc:  # noqa: BLE001 - 限流退避后换镜像
                last = str(exc)
                print("    镜像 %s 第 %d 次失败：%s" % (base, attempt, last), flush=True)
                time.sleep(5 * attempt)
    raise RuntimeError("取文件树失败：" + last)


def _blocks(size: int) -> list[tuple[int, int]]:
    """把文件切成 (起始偏移, 结束偏移) 闭区间块。"""
    out = []
    start = 0
    while start < size:
        end = min(start + BLOCK, size) - 1
        out.append((start, end))
        start = end + 1
    return out


def _fetch_block(client: httpx.Client, base: str, repo: str, filename: str,
                 start: int, end: int, path: Path) -> bool:
    """拉一个块并写到对应偏移。"""
    url = base + "/" + repo + "/resolve/main/" + filename
    for attempt in range(1, 6):
        try:
            with client.stream(
                "GET",
                url,
                headers={"Range": "bytes=%d-%d" % (start, end)},
                timeout=300.0,
                follow_redirects=True,
            ) as resp:
                if resp.status_code != 206:
                    print("    块 %d 返回 %d，镜像不支持 Range" % (start, resp.status_code), flush=True)
                    return False
                with open(path, "r+b") as handle:
                    handle.seek(start)
                    written = 0
                    for chunk in resp.iter_bytes(CHUNK):
                        handle.write(chunk)
                        written += len(chunk)
                    if written != end - start + 1:
                        print("    块 %d 长度不符：期望 %d 实得 %d" % (start, end - start + 1, written), flush=True)
                        return False
            return True
        except Exception as exc:  # noqa: BLE001 - 单块失败重试
            print("    块 %d 第 %d 次失败：%s" % (start, attempt, exc), flush=True)
            time.sleep(5 * attempt)
    return False


def _download_parallel(client: httpx.Client, base: str, repo: str, filename: str,
                       size: int, dest: Path) -> bool:
    """并行分段下载。"""
    part = dest.with_suffix(dest.suffix + ".part")
    state_file = part.with_suffix(part.suffix + ".chunks.json")
    part.parent.mkdir(parents=True, exist_ok=True)
    if not part.exists() or part.stat().st_size != size:
        with open(part, "wb") as handle:
            handle.truncate(size)
        done: list[int] = []
    else:
        try:
            done = [int(i) for i in json.loads(state_file.read_text(encoding="utf-8"))]
        except Exception:  # noqa: BLE001 - 状态损坏就重下
            done = []
    blocks = _blocks(size)
    todo = [(index, start, end) for index, (start, end) in enumerate(blocks) if index not in done]
    print("    共 %d 块，待下 %d 块" % (len(blocks), len(todo)), flush=True)
    failed: list[int] = []
    if todo:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(_fetch_block, client, base, repo, filename, start, end, part): index
                for index, start, end in todo
            }
            for future in futures:
                index = futures[future]
                try:
                    ok = future.result()
                except Exception as exc:  # noqa: BLE001
                    print("    块 %d 异常：%s" % (index, exc), flush=True)
                    ok = False
                if ok:
                    done.append(index)
                    state_file.write_text(json.dumps(sorted(done)), encoding="utf-8")
                else:
                    failed.append(index)
    if failed:
        print("    失败块：%s" % failed, flush=True)
        return False
    actual = part.stat().st_size
    if actual != size:
        print("    字节数不符：期望 %d 实得 %d" % (size, actual), flush=True)
        return False
    part.replace(dest)
    if state_file.exists():
        state_file.unlink()
    return True


def _download_serial(client: httpx.Client, base: str, repo: str, filename: str,
                     size: int, dest: Path) -> bool:
    """镜像不支持 Range 时的串行兜底。"""
    url = base + "/" + repo + "/resolve/main/" + filename
    part = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url, timeout=300.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        got = 0
        with open(part, "wb") as handle:
            for chunk in resp.iter_bytes(CHUNK):
                handle.write(chunk)
                got += len(chunk)
                if got % (256 * CHUNK) < CHUNK:
                    print("      已下 %.2f GB / %.2f GB" % (got / 1024 ** 3, size / 1024 ** 3), flush=True)
    if part.stat().st_size != size:
        print("    字节数不符：期望 %d 实得 %d" % (size, part.stat().st_size), flush=True)
        part.unlink()
        return False
    part.replace(dest)
    return True


def main() -> int:
    """列目录或下载。"""
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    repo = sys.argv[1]
    out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_DIR
    with httpx.Client(follow_redirects=True, headers={"User-Agent": "xworld-pget/1.0"}) as client:
        try:
            tree, base = _tree(client, repo)
        except Exception as exc:  # noqa: BLE001
            print("列仓库失败 %s：%s" % (repo, exc))
            return 1
        files = [n for n in tree if str(n.get("path", "")).lower().endswith(".gguf")]
        if len(sys.argv) < 3 or sys.argv[1] == "--list":
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
        started = time.perf_counter()
        ok = _download_parallel(client, base, repo, filename, size, dest)
        if not ok:
            print("    并行失败，退回串行", flush=True)
            ok = _download_serial(client, base, repo, filename, size, dest)
        elapsed = time.perf_counter() - started
        if ok:
            print("  完成：%s（%d 字节，用时 %.1f s）" % (dest, size, elapsed), flush=True)
            return 0
        print("  失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
