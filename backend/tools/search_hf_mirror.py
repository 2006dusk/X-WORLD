r"""在 HF 镜像上按关键词找候选模型，并列出其 GGUF 文件。

用法：
    python backend\tools\search_hf_mirror.py <关键词> [更多关键词...]

每个关键词取前若干个仓库，打印仓库名、点赞数与 GGUF 文件名。
输出同时写到 docs/实践情况/data/sweep/hf_search.txt。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
MIRROR = "https://hf-mirror.com"
OUT = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "hf_search.txt"

QUERIES = (
    "Qwen2-7B-Multilingual-RP",
    "RPMax",
    "Aura-v2-7B",
    "MiniFantasy",
    "heretic GGUF",
)


def _get(client: httpx.Client, url: str, params: dict | None = None) -> list:
    """取 JSON，失败返回空列表。"""
    try:
        resp = client.get(url, params=params, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 - 检索失败只记录
        return []


def main() -> int:
    """检索并列出文件。"""
    queries = sys.argv[1:] or list(QUERIES)
    lines: list[str] = []
    with httpx.Client(follow_redirects=True) as client:
        for query in queries:
            limit = int(os.environ.get("XW_SEARCH_LIMIT", "30"))
            rows = _get(client, MIRROR + "/api/models", {"search": query, "limit": limit})
            lines.append("=== 查询 %s：%d 个仓库 ===" % (query, len(rows)))
            for row in rows:
                repo = str(row.get("modelId") or row.get("id") or "")
                if not repo:
                    continue
                likes = row.get("likes")
                lines.append("  %s  点赞 %s" % (repo, likes))
                tree = _get(
                    client,
                    MIRROR + "/api/models/" + repo + "/tree/main",
                    {"recursive": "1"},
                )
                ggufs = [
                    str(node.get("path"))
                    for node in tree
                    if str(node.get("path", "")).lower().endswith(".gguf")
                ]
                for path in ggufs[:14]:
                    lines.append("      " + path)
                if len(ggufs) > 14:
                    lines.append("      ... 共 %d 个 GGUF" % len(ggufs))
            lines.append("")
    text = "\n".join(lines)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"写入 {OUT} 行数 {len(lines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
