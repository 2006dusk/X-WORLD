r"""用 GitHub API 抓取仓库文件并落盘（自动解 base64）。

用法：
    python backend\tools\fetch_github_file.py <repo> <path> <ref> <out>

例：
    python backend\tools\fetch_github_file.py SillyTavern/SillyTavern \
        default/content/presets/openai/Default.json release st_openai_default.json
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT.parent / "docs" / "理论支持" / "_src"


def fetch(repo: str, path: str, ref: str) -> str:
    """取一个仓库文件的文本内容。"""
    api = (
        "https://api.github.com/repos/" + repo + "/contents/"
        + urllib.parse.quote(path, safe="/") + "?ref=" + ref
    )
    request = urllib.request.Request(api, headers={"User-Agent": "xworld-research"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload.get("content"), str):
        return base64.b64decode(payload["content"]).decode("utf-8", "replace")
    raise RuntimeError("unexpected payload: " + json.dumps(payload)[:200])


def main() -> int:
    """抓取并落盘。"""
    repo, path, ref, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    try:
        text = fetch(repo, path, ref)
    except Exception as exc:  # noqa: BLE001 - 抓取失败只需记录
        print(f"FAIL {path}: {exc}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / out
    target.write_text(text, encoding="utf-8")
    print(f"ok {path} -> {target} chars={len(text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
