"""画图组 1：控制向量注入的正确性、强度扫描与多轮行为。每张图独立成文件。

数据源：docs/理论支持/data/cvec/ 下的 cvec_verify.json、persona_probe.json、
rp_persona_probe.json、cvec_multiturn.json。输出到 docs/figures/。

各图口径：
  fig1a 注入正确性：各向量档的补全长度，右轴为 3-gram 多样性，重复退化时该值塌到 0 附近。
  fig1b 强度扫描：通用助手探针各档输出的可读字符占比。
  fig1c 虚构出处：角色探针各档是否给出作品名。
  fig1d 多轮生成量：两臂每轮的补全 token 数。
  fig1e 多轮语言保持：两臂每轮输出里的中文字符占比。
  fig1f 前缀复用：两臂每轮的缓存命中比例。
"""
from pathlib import Path
import json
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fig_style import C, apply_style, render

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "docs" / "理论支持" / "data" / "cvec"
OUT = REPO / "docs" / "figures"


def load(name: str):
    """读取 cvec 目录下的 JSON。"""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def readable_ratio(text: str) -> float:
    """可读字符占比：可打印且不是替换字符的字符数除以总字符数。乱码输出接近 0。

    换行与制表符算正常字符，替换字符 U+FFFD 算不可读。
    """
    if not text:
        return 0.0
    good = sum(1 for ch in text if (ch.isprintable() or ch in "\n\r\t") and ch != "\ufffd")
    return good / len(text)


def distinct_3gram(text: str) -> float:
    """不同 3-gram 占全部 3-gram 的比例。复读式退化的输出该值接近 0。"""
    compact = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    if len(compact) < 3:
        return 1.0 if compact else 0.0
    grams = [compact[i:i + 3] for i in range(len(compact) - 2)]
    return len(set(grams)) / len(grams)


def has_work_title(text: str) -> int:
    """是否出现书名号包裹的作品名。用于判断输出是否虚构了出处。"""
    return 1 if "\u300a" in text else 0


def cjk_ratio(text: str) -> float:
    """中文字符占全部字符的比例。用来衡量输出是否还保持中文。"""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(text)


def fig1a(ax):
    """注入正确性对照：零向量与基线应一致，越界向量输出退化成重复片段。"""
    rows = load("cvec_verify.json")
    names = [r["name"] for r in rows]
    toks = [r.get("usage", {}).get("completion_tokens", 0) for r in rows]
    div = [distinct_3gram(r.get("text", "")) for r in rows]
    colors = []
    for r, d in zip(rows, div):
        t = r.get("usage", {}).get("completion_tokens", 0)
        if t == 0:
            colors.append(C["grey"])
        elif d < 0.35:
            colors.append(C["vermillion"])
        else:
            colors.append(C["sky"])
    x = np.arange(len(names))
    ax.bar(x, toks, width=0.62, color=colors, edgecolor="white", linewidth=0.4)
    for xi, t in zip(x, toks):
        ax.text(xi, t + 1.0, str(t), ha="center", va="bottom", fontsize=7)
    ax.axhline(toks[0], color=C["black"], lw=0.7, ls="--")
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=30, ha="right")
    ax.set_ylabel("Completion tokens")
    ax.set_title("Injection fidelity", pad=5)
    ax.set_ylim(0, max(toks) * 1.3)
    ax2 = ax.twinx()
    ax2.plot(x, div, "o", color=C["black"], markersize=3.5, zorder=3)
    ax2.set_ylim(0, 1.15)
    ax2.set_ylabel("Distinct 3-gram fraction")
    ax2.spines["top"].set_visible(False)


def fig1b(ax):
    """强度扫描：通用助手探针各档输出的可读字符占比。"""
    rows = load("persona_probe.json")
    names = [r["name"] for r in rows]
    ratios = []
    for r in rows:
        vals = [readable_ratio(t) for t in r.get("texts", [])]
        ratios.append(float(np.mean(vals)) if vals else 0.0)
    x = np.arange(len(names))
    colors = [C["sky"] if v > 0.95 else (C["orange"] if v > 0.5 else C["vermillion"]) for v in ratios]
    ax.bar(x, ratios, width=0.62, color=colors, edgecolor="white", linewidth=0.4)
    for xi, v in zip(x, ratios):
        ax.text(xi, v + 0.02, f"{v:.2f}", ha="center", va="bottom", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("Readable character fraction")
    ax.set_ylim(0, 1.15)
    ax.set_title("Strength sweep, assistant probe", pad=5)


def fig1c(ax):
    """事实幻觉：角色探针各档输出是否虚构作品出处。"""
    rows = load("rp_persona_probe.json")
    names = [r["name"] for r in rows]
    hits = [has_work_title(r.get("text", "")) for r in rows]
    x = np.arange(len(names))
    colors = [C["vermillion"] if h else C["green"] for h in hits]
    ax.bar(x, hits, width=0.62, color=colors, edgecolor="white", linewidth=0.4)
    for xi, h in zip(x, hits):
        ax.text(xi, h + 0.05, "fabricated" if h else "none", ha="center", va="bottom", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("Fabricated attribution")
    ax.set_ylim(0, 1.35)
    ax.set_yticks([0, 1])
    ax.set_title("Fabricated attribution, role probe", pad=5)


def fig1d(ax):
    """多轮生成量：两臂每轮的补全 token 数。"""
    rows = load("cvec_multiturn.json")
    for row in rows:
        turns = row.get("turns", [])
        xs = [t["turn"] for t in turns]
        ys = [t.get("completion_tokens", 0) for t in turns]
        color = C["blue"] if row["name"] == "plain" else C["vermillion"]
        marker = "o" if row["name"] == "plain" else "s"
        ax.plot(xs, ys, marker=marker, color=color, label=row["name"])
    ax.axhline(220, color=C["black"], lw=0.7, ls="--")
    ax.text(1.05, 226, "cap 220", fontsize=7, va="bottom", ha="left")
    ax.set_xlabel("Turn")
    ax.set_ylabel("Completion tokens")
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_xlim(0.7, 5.6)
    ax.set_ylim(-12, 255)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("Generation per turn", pad=5)


def fig1e(ax):
    """多轮语言保持：两臂每轮输出里的中文字符占比。"""
    rows = load("cvec_multiturn.json")
    for row in rows:
        turns = row.get("turns", [])
        xs = [t["turn"] for t in turns]
        ys = [cjk_ratio(t["content"]) for t in turns]
        color = C["blue"] if row["name"] == "plain" else C["vermillion"]
        marker = "o" if row["name"] == "plain" else "s"
        ax.plot(xs, ys, marker=marker, color=color, label=row["name"])
    ax.set_xlabel("Turn")
    ax.set_ylabel("CJK character fraction")
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_ylim(-0.05, 1.05)
    ax.legend(frameon=False, loc="center left")
    ax.set_title("Language retention", pad=5)


def fig1f(ax):
    """前缀复用：两臂每轮的缓存命中比例。"""
    rows = load("cvec_multiturn.json")
    for row in rows:
        turns = row.get("turns", [])
        xs = []
        ys = []
        for t in turns:
            pt = t.get("prompt_tokens", 0)
            if pt:
                xs.append(t["turn"])
                ys.append(t.get("cached_tokens", 0) / pt)
        color = C["blue"] if row["name"] == "plain" else C["vermillion"]
        marker = "o" if row["name"] == "plain" else "s"
        ax.plot(xs, ys, marker=marker, color=color, label=row["name"])
    ax.set_xlabel("Turn")
    ax.set_ylabel("Cached prompt fraction")
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_ylim(-0.05, 1.05)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("KV prefix reuse", pad=5)


FIGURES = [
    (fig1a, "fig1a_injection_fidelity"),
    (fig1b, "fig1b_strength_sweep"),
    (fig1c, "fig1c_fabricated_attribution"),
    (fig1d, "fig1d_generation_per_turn"),
    (fig1e, "fig1e_language_retention"),
    (fig1f, "fig1f_kv_prefix_reuse"),
]


def main() -> None:
    """逐张生成图 1 组的图，输出 PNG 与 PDF。"""
    apply_style()
    for fn, stem in FIGURES:
        for p in render(fn, OUT, stem):
            print("wrote", p)


if __name__ == "__main__":
    main()
