"""画图组 3：自写推理服务端与原生 llama-server 的提示复用与多轮时延对照。每张图独立成文件。

数据源：docs/理论支持/data/wwc/ 下的 wwc_reuse_final.json、native_reuse2.json、
wwc_mt_final.json、native_mt_final.json。输出到 docs/figures/。

各图口径：
  fig3a 提示前缀复用：三种请求条件下命中的缓存 token 数。
  fig3b 预填耗时：同样三种条件下的预填耗时。
  fig3c 提示增长：每一轮的提示 token 数。
  fig3d 多轮预填：每一轮的预填耗时。
"""
from pathlib import Path
import json
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fig_style import C, apply_style, render

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "docs" / "理论支持" / "data" / "wwc"
OUT = REPO / "docs" / "figures"

CONDITIONS = ("cold", "same_prompt", "same_system_other_tail")
LABELS = ("Cold start", "Same prompt", "Same prefix")


def load(name: str):
    """读取 wwc 目录下的 JSON。"""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def reused(d: dict, cond: str) -> int:
    """该条件下命中的缓存 token 数。自写端记在 timings，原生端在 usage 里。"""
    block = d.get(cond, {})
    timings = block.get("timings", {})
    usage = block.get("usage", {})
    if "n_prompt_reused" in timings:
        return int(timings["n_prompt_reused"])
    details = usage.get("prompt_tokens_details") or {}
    if "cached_tokens" in details:
        return int(details["cached_tokens"])
    return int(timings.get("cache_n", 0))


def prompt_ms(d: dict, cond: str) -> float:
    """该条件下的预填耗时，单位毫秒。"""
    return float(d.get(cond, {}).get("timings", {}).get("prompt_ms", 0.0))


def fig3a(ax):
    """三种请求条件下的前缀复用 token 数。"""
    wwc = load("wwc_reuse_final.json")
    native = load("native_reuse2.json")
    x = np.arange(len(CONDITIONS))
    w = [reused(wwc, c) for c in CONDITIONS]
    n = [reused(native, c) for c in CONDITIONS]
    width = 0.36
    ax.bar(x - width / 2, w, width, color=C["blue"], edgecolor="white", linewidth=0.4, label="world-work-cpp")
    ax.bar(x + width / 2, n, width, color=C["orange"], edgecolor="white", linewidth=0.4, label="llama-server")
    for xi, val in zip(x - width / 2, w):
        ax.text(xi, val + 1.5, str(val), ha="center", va="bottom", fontsize=7)
    for xi, val in zip(x + width / 2, n):
        ax.text(xi, val + 1.5, str(val), ha="center", va="bottom", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels(LABELS)
    ax.set_ylabel("Cached prompt tokens")
    ax.set_ylim(0, max(w + n) * 1.3)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("Prefix reuse", pad=5)


def fig3b(ax):
    """三种请求条件下的预填耗时。"""
    wwc = load("wwc_reuse_final.json")
    native = load("native_reuse2.json")
    x = np.arange(len(CONDITIONS))
    w = [prompt_ms(wwc, c) for c in CONDITIONS]
    n = [prompt_ms(native, c) for c in CONDITIONS]
    width = 0.36
    ax.bar(x - width / 2, w, width, color=C["blue"], edgecolor="white", linewidth=0.4, label="world-work-cpp")
    ax.bar(x + width / 2, n, width, color=C["orange"], edgecolor="white", linewidth=0.4, label="llama-server")
    for xi, val in zip(x - width / 2, w):
        ax.text(xi, val * 1.3, f"{val:.0f}", ha="center", va="bottom", fontsize=7)
    for xi, val in zip(x + width / 2, n):
        ax.text(xi, val * 1.3, f"{val:.0f}", ha="center", va="bottom", fontsize=7)
    ax.set_yscale("log")
    ax.set_ylim(2, max(w + n) * 3)
    ax.set_xticks(x)
    ax.set_xticklabels(LABELS)
    ax.set_ylabel("Prompt processing time (ms)")
    ax.legend(frameon=False, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.02))
    ax.set_title("Prefill latency", pad=16)


def fig3c(ax):
    """多轮对话中每一轮的提示 token 数。"""
    for fname, color, marker, label in (("wwc_mt_final.json", C["blue"], "o", "world-work-cpp"),
                                         ("native_mt_final.json", C["orange"], "s", "llama-server")):
        d = load(fname)
        turns = d.get("turns", [])
        ax.plot([t["turn"] for t in turns], [t.get("prompt_tokens", 0) for t in turns],
                marker=marker, color=color, label=label)
    ax.set_xlabel("Turn")
    ax.set_ylabel("Prompt tokens")
    ax.set_xticks([1, 2, 3, 4])
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("Prompt growth across turns", pad=5)


def fig3d(ax):
    """多轮对话中每一轮的预填耗时。"""
    for fname, color, marker, label in (("wwc_mt_final.json", C["blue"], "o", "world-work-cpp"),
                                         ("native_mt_final.json", C["orange"], "s", "llama-server")):
        d = load(fname)
        turns = d.get("turns", [])
        ax.plot([t["turn"] for t in turns], [t.get("prompt_ms", 0) for t in turns],
                marker=marker, color=color, label=label)
    ax.set_xlabel("Turn")
    ax.set_ylabel("Prompt processing time (ms)")
    ax.set_xticks([1, 2, 3, 4])
    ax.legend(frameon=False, loc="upper right")
    ax.set_title("Prefill latency across turns", pad=5)


FIGURES = [
    (fig3a, "fig3a_prefix_reuse"),
    (fig3b, "fig3b_prefill_latency"),
    (fig3c, "fig3c_prompt_growth"),
    (fig3d, "fig3d_prefill_across_turns"),
]


def main() -> None:
    """逐张生成图 3 组的图，输出 PNG 与 PDF。"""
    apply_style()
    for fn, stem in FIGURES:
        for p in render(fn, OUT, stem, figsize=(3.8, 3.0)):
            print("wrote", p)


if __name__ == "__main__":
    main()
