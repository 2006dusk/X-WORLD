"""按基座名找 GGUF 镜像并列出量化档。

候选清单里有些条目只有基座仓库，没有 GGUF 权重。这个脚本按名称检索镜像仓库，
再用 gguf_meta 的远程核实取架构、训练上下文、许可与各量化档体积，供下载前定档。

用法：
    F:/python/python.exe backend/tools/find_gguf_mirrors.py Chun121/Qwen3-4B-RPG-Roleplay-V2
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

from gguf_meta import MIRROR, describe, fetch_json  # noqa: E402

OUT_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data"
QUANT_KEYS = ("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "IQ4")


def mirrors(client: httpx.Client, name: str) -> list[str]:
    """按名称检索仓库，返回可能带 GGUF 的仓库标识。"""
    query = name.split("/")[-1]
    url = (
        f"{MIRROR}/api/models?search={query}"
        "&sort=downloads&direction=-1&limit=30"
    )
    data = fetch_json(client, url)
    found: list[str] = []
    items = data.get("models") if isinstance(data, dict) else []
    for item in items:
        repo = str(item.get("modelId") or "")
        if repo and "gguf" in repo.lower() and repo not in found:
            found.append(repo)
    return found


def main() -> int:
    """逐个目标找镜像并打印量化档。"""
    targets = sys.argv[1:]
    rows: list[dict[str, object]] = []
    with httpx.Client(follow_redirects=True) as client:
        for target in targets:
            print("=== " + target + " ===", flush=True)
            for repo in mirrors(client, target):
                info = describe(client, repo)
                quants = [
                    item
                    for item in (info.get("gguf_files") or [])
                    if item.get("mb") and any(key in item.get("file", "") for key in QUANT_KEYS)
                ]
                head = "  %s arch=%s ctx=%s lic=%s dl=%s likes=%s" % (
                    repo,
                    info.get("architecture"),
                    info.get("context_length"),
                    info.get("license"),
                    info.get("downloads"),
                    info.get("likes"),
                )
                print(head)
                for item in quants[:6]:
                    print("      %s  %s MB" % (item.get("file"), item.get("mb")))
                rows.append({"target": target, "repo": repo, "quants": quants[:8]})
                break
            time.sleep(1.0)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "gguf_mirrors.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
