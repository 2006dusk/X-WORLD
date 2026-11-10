"""按参数量段检索 RP 候选。

候选清单里没有五到六档的模型，用户提出该档位可能更好。本机实测是唯一口径，
因此先按参数量段把这一档的候选捞出来，再逐个下载实测。

镜像的模型列表接口不带 safetensors 参数字段，逐仓查参数会触发限流，
因此这里只用列表接口：标签族、中文检索词、按规格词检索与按作者检索，
再用仓库名与标签里的规格标记筛出候选仓。参数量由仓库元数据另行核对。

用法：
    python backend/tools/search_roleplay.py --min-b 4.5 --max-b 7.0
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

MIRROR = "https://hf-mirror.com"
OUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "实践情况" / "data"

# 列表接口的标签族。
TAG_QUERIES = ("roleplay", "rp", "story", "roleplaying", "chinese-roleplay", "nsfw", "creative")
# 中文检索词，与 zh 语种过滤组合。
TEXT_QUERIES = ("roleplay", "RP", "角色扮演", "剧情", "对话")
# 按规格词检索，再与 RP 标签取交集。
SIZE_QUERIES = ("5B", "6B", "5b", "6b")
# 已知中文 RP 较多的作者。
AUTHOR_QUERIES = ("TouchNight", "IceWhiskey", "ArityFlow", "ClosedCharacter", "Lunzima", "gctian")

# 仓库名或标签里的参数规模标记。
SIZE_PATTERN = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*[bB](?![0-9a-zA-Z])")
# RP 相关标签，用于和规格词检索取交集。
RP_TAGS = ("roleplay", "rp", "story", "nsfw", "creative", "character", "角色扮演", "对话")


def fetch_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None,
               attempts: int = 4) -> Any:
    """取 JSON，失败重试，仍失败返回空列表。"""
    for index in range(attempts):
        try:
            resp = client.get(url, params=params, timeout=30.0)
            resp.raise_for_status()
            return resp.json()
        except Exception:  # noqa: BLE001
            if index < attempts - 1:
                time.sleep(2.5 * (index + 1))
    return []


def collect(client: httpx.Client, limit: int) -> dict[str, dict[str, Any]]:
    """多路列表接口收候选仓库。"""
    repos: dict[str, dict[str, Any]] = {}

    def absorb(data: Any, source: str) -> None:
        for model in data if isinstance(data, list) else []:
            repo = str(model.get("modelId") or "")
            if not repo:
                continue
            entry = repos.setdefault(repo, {"repo": repo, "sources": []})
            entry["sources"].append(source)
            entry["downloads"] = model.get("downloads")
            entry["likes"] = model.get("likes")
            entry["tags"] = [str(t) for t in (model.get("tags") or [])]

    for tag in TAG_QUERIES:
        absorb(fetch_json(client, f"{MIRROR}/api/models",
                          {"filter": tag, "sort": "downloads", "direction": -1, "limit": limit}),
               f"tag:{tag}")

    for text in TEXT_QUERIES:
        absorb(fetch_json(client, f"{MIRROR}/api/models",
                          {"search": text, "filter": "zh", "sort": "downloads", "direction": -1,
                           "limit": limit}),
               f"zh:{text}")

    for size in SIZE_QUERIES:
        absorb(fetch_json(client, f"{MIRROR}/api/models",
                          {"search": size, "filter": "zh", "sort": "downloads", "direction": -1,
                           "limit": limit}),
               f"size:{size}")

    for author in AUTHOR_QUERIES:
        absorb(fetch_json(client, f"{MIRROR}/api/models",
                          {"author": author, "sort": "downloads", "direction": -1, "limit": limit}),
               f"author:{author}")

    return repos


def guess_params(repo: str, tags: list[str]) -> float:
    """从仓库名与标签推断参数量（十亿），推断不出返回 0。"""
    matches = SIZE_PATTERN.findall(repo)
    for tag in tags:
        matches += SIZE_PATTERN.findall(tag)
    values = [float(item) for item in matches if 0.5 <= float(item) <= 80.0]
    return max(values) if values else 0.0


def main() -> int:
    """检索并输出落在指定参数量段的候选。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-b", type=float, default=4.5)
    parser.add_argument("--max-b", type=float, default=7.0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--out", default="search_roleplay.json")
    args = parser.parse_args()

    with httpx.Client(follow_redirects=True) as client:
        repos = collect(client, args.limit)
    print(f"候选仓库 {len(repos)} 个")

    rows: list[dict[str, Any]] = []
    for repo, entry in repos.items():
        tags = entry.get("tags") or []
        params_b = guess_params(repo, tags)
        if not params_b or not (args.min_b <= params_b <= args.max_b):
            continue
        rp_hit = any(tag.lower() in RP_TAGS for tag in tags)
        rows.append({
            "repo": repo,
            "params_b": params_b,
            "downloads": entry.get("downloads"),
            "likes": entry.get("likes"),
            "license": next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), ""),
            "has_gguf_tag": "gguf" in tags,
            "langs": sorted({t for t in tags if t in ("zh", "en", "ja", "ko", "ru")}),
            "rp_tag_hit": rp_hit,
            "sources": entry.get("sources"),
        })

    rows.sort(key=lambda item: (-int(item.get("downloads") or 0), item["repo"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / args.out
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    for row in rows:
        print(
            f"{row['params_b']:>5}B dl={row['downloads']:<9} like={row['likes']:<5} "
            f"gguf={'Y' if row['has_gguf_tag'] else 'n'} rp={'Y' if row['rp_tag_hit'] else 'n'} "
            f"langs={','.join(row['langs']) or '-':<6} {row['repo']}"
        )
    print(f"total={len(rows)} out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
