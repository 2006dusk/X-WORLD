"""GGUF 元数据远程核实。

不下载权重，用 Hugging Face 模型接口取架构、训练上下文、内置对话模板与各量化档体积。
Range 请求只取文件头解析 GGUF 的方式留作备用，词表体积会超过头部长度，不作为主路径。

字段来源：
- 许可、语言标签、下载量、点赞：模型信息接口的 tags 与 cardData
- 架构、训练上下文、内置模板、仓库总体积：expand=gguf 的扩展字段
- 各量化档文件体积：blobs 接口
- 基座仓库：cardData.base_model 或 base_model 标签，量化镜像仓库的语种标签常缺失，
  因此另查基座仓库的语种与许可，镜像仓库的标注一并保留

用法：
    python backend\tools\gguf_meta.py <owner/repo> [...]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

MIRROR = "https://hf-mirror.com"
LANG_CODES = ("zh", "en", "ja", "ko", "ru", "fr", "de", "es")


def fetch_json(client: httpx.Client, url: str, attempts: int = 4) -> dict[str, Any]:
    """取 JSON，失败重试，仍失败返回空字典。"""
    for index in range(attempts):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            if index < attempts - 1:
                time.sleep(1.5 * (index + 1))
    return {}


def lang_codes(tags: list[str], card: dict[str, Any]) -> list[str]:
    """从标签与 cardData.language 提取语种代码。"""
    card_langs = card.get("language") or []
    if not isinstance(card_langs, list):
        card_langs = [card_langs]
    codes = {
        str(code).lower()
        for code in list(card_langs) + tags
        if str(code).lower() in LANG_CODES
    }
    return sorted(codes)


def describe(client: httpx.Client, repo: str) -> dict[str, Any]:
    """取单个仓库的元数据。"""
    info = fetch_json(client, f"{MIRROR}/api/models/{repo}")
    meta = fetch_json(client, f"{MIRROR}/api/models/{repo}?expand[]=gguf")
    gguf = meta.get("gguf") or {}
    blobs = fetch_json(client, f"{MIRROR}/api/models/{repo}?blobs=true")

    files = []
    for sibling in blobs.get("siblings") or []:
        name = str(sibling.get("rfilename") or "")
        if not name.lower().endswith(".gguf"):
            continue
        size = sibling.get("size")
        files.append(
            {
                "file": name,
                "mb": round(int(size) / 1048576) if isinstance(size, int) else None,
            }
        )
    files.sort(key=lambda item: item["mb"] or 0)

    tags = [str(t) for t in (info.get("tags") or [])]
    card = info.get("cardData") or {}
    license_tag = next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), "")
    if not license_tag:
        license_tag = str(card.get("license") or "")

    base_model = str(card.get("base_model") or "")
    if not base_model:
        base_model = next(
            (t.split(":", 1)[1] for t in tags if t.startswith("base_model:")), ""
        )
    base_languages: list[str] = []
    base_license = ""
    if base_model:
        base_info = fetch_json(client, f"{MIRROR}/api/models/{base_model}")
        base_tags = [str(t) for t in (base_info.get("tags") or [])]
        base_card = base_info.get("cardData") or {}
        base_languages = lang_codes(base_tags, base_card)
        base_license = next(
            (t.split(":", 1)[1] for t in base_tags if t.startswith("license:")), ""
        ) or str(base_card.get("license") or "")

    return {
        "repo": repo,
        "architecture": gguf.get("architecture"),
        "context_length": gguf.get("context_length"),
        "has_chat_template": bool(gguf.get("chat_template")),
        "repo_total_gb": round(float(gguf.get("total") or 0) / 1073741824, 2),
        "license": license_tag,
        "languages": lang_codes(tags, card),
        "base_model": base_model,
        "base_license": base_license,
        "base_languages": base_languages,
        "downloads": info.get("downloads"),
        "likes": info.get("likes"),
        "gguf_files": files,
    }


def main() -> int:
    repos = sys.argv[1:]
    if not repos:
        print("用法：gguf_meta.py <owner/repo> [...]")
        return 1
    results = []
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        for repo in repos:
            item = describe(client, repo)
            results.append(item)
            print(
                f"{repo}: arch={item['architecture']} ctx={item['context_length']} "
                f"lic={item['license'] or '未标注'} langs={item['languages']} "
                f"base={item['base_model'] or '-'} "
                f"base_lic={item['base_license'] or '未标注'} base_langs={item['base_languages']} "
                f"dl={item['downloads']} likes={item['likes']}",
                flush=True,
            )
    out = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "docs"
        / "实践情况"
        / "data"
        / "gguf_meta.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
