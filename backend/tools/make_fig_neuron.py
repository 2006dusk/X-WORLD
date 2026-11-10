"""画图组 2：神经元级人格转向的逐层分离度、强度扫描与探针行为。每张图独立成文件。

数据源：docs/理论支持/data/neuron/ 下的 modern_neuron_cvec.f32.json、
style_cvec_k4.f32.json、neuron_sweep.json、style_probe.json。输出到 docs/figures/。

各图口径：
  fig2a 逐层分离强度：两种构造方式下每层选中向量的范数。
  fig2b 逐层选中神经元的响应差热图（层 x 排名）。
  fig2c 强度扫描：算术探针是否仍给出正确答案。
  fig2d 强度扫描：输出退化（复读或模板泄漏）发生率。
  fig2e 角色探针：虚构出处率与输出长度随强度变化。
  fig2f 声线探针：三档强度下五个探针的输出长度热图。
"""
from pathlib import Path
import json
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fig_style import C, apply_style, render

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "docs" / "理论支持" / "data" / "neuron"
OUT = REPO / "docs" / "figures"


def load(name: str):
    """读取 neuron 目录下的 JSON。"""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def distinct_3gram(text: str) -> float:
    """不同 3-gram 占全部 3-gram 的比例。复读式退化的输出该值接近 0。"""
    compact = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    if len(compact) < 3:
        return 1.0 if compact else 0.0
    grams = [compact[i:i + 3] for i in range(len(compact) - 2)]
    return len(set(grams)) / len(grams)


def is_degenerate(text: str) -> int:
    """输出是否退化：3-gram 多样性过低，或泄漏了对话模板标记。"""
    if "<|im" in text:
        return 1
    return 1 if distinct_3gram(text) < 0.35 else 0


def layer_table(name: str):
    """返回按层号排序的 (层号, norm, diff 列表)。"""
    d = load(name)
    rows = []
    for key in sorted(d, key=lambda k: int(k)):
        rows.append((int(key), d[key]["norm"], d[key]["diff"]))
    return rows


def fig2a(ax):
    """逐层分离强度：两种构造方式下每层范数。"""
    modern = layer_table("modern_neuron_cvec.f32.json")
    style = layer_table("style_cvec_k4.f32.json")
    xs = [r[0] for r in modern]
    ax.plot(xs, [r[1] for r in modern], marker="o", ms=3.5, color=C["blue"], label="dense, top-4 by column")
    ax.plot(xs, [r[1] for r in style], marker="s", ms=3.5, color=C["vermillion"], label="style, top-4 by rank")
    peak = max(modern, key=lambda r: r[1])
    ax.plot([peak[0]], [peak[1]], "o", ms=8, mfc="none", mec=C["black"], mew=0.8)
    ax.annotate("L%d" % peak[0], (peak[0], peak[1]), textcoords="offset points",
                xytext=(-8, -2), fontsize=7, ha="right")
    ax.set_xlabel("Layer")
    ax.set_ylabel("Vector norm")
    ax.set_xticks([1, 5, 10, 15, 20, 25, 30])
    ax.legend(frameon=False, loc="center left", bbox_to_anchor=(0.02, 0.45))
    ax.set_title("Per-layer separation", pad=5)


def fig2b(ax):
    """逐层 top-4 神经元的响应差热图，两种构造方式并排。"""
    modern = layer_table("modern_neuron_cvec.f32.json")
    style = layer_table("style_cvec_k4.f32.json")
    mats = [np.array([r[2] for r in rows], dtype=float) for rows in (modern, style)]
    vmax = float(max(np.abs(m).max() for m in mats))
    norm = Normalize(vmin=-vmax, vmax=vmax)
    ax.set_axis_off()
    for i, (mat, title) in enumerate(zip(mats, ("dense", "style"))):
        inner = ax.inset_axes([0.03 + i * 0.48, 0.08, 0.37, 0.76])
        im = inner.imshow(mat, aspect="auto", cmap="RdBu_r", norm=norm,
                          extent=[0.5, 4.5, len(mat) + 0.5, 0.5])
        inner.set_xticks([1, 2, 3, 4])
        inner.set_yticks([1, 5, 10, 15, 20, 25, 30])
        inner.tick_params(labelsize=6.5, length=2)
        inner.set_title(title, fontsize=7.5, pad=2)
        for s in ("top", "right"):
            inner.spines[s].set_visible(False)
    cb = ax.inset_axes([0.93, 0.08, 0.022, 0.76])
    cbar = ax.figure.colorbar(im, cax=cb)
    cbar.ax.tick_params(labelsize=6.5)
    cbar.set_label("Response difference", fontsize=7)
    ax.set_title("Per-layer response difference", pad=5)


def fig2c(ax):
    """算术探针在强度扫描下的正确答案保留率。"""
    rows = load("neuron_sweep.json")
    xs = np.arange(len(rows))
    ok = []
    for r in rows:
        ok.append(1.0 if "16" in r["answers"].get("math", "") else 0.0)
    ax.plot(xs, ok, marker="o", color=C["blue"])
    ax.set_xticks(xs)
    ax.set_xticklabels([str(r["scale"]) for r in rows])
    ax.set_ylim(-0.15, 1.15)
    ax.set_xlabel("Control-vector scale")
    ax.set_ylabel("Correct answer retained")
    ax.set_title("Arithmetic probe", pad=5)


def fig2d(ax):
    """输出退化率随强度变化。"""
    rows = load("neuron_sweep.json")
    xs = np.arange(len(rows))
    rates = []
    for r in rows:
        vals = [is_degenerate(v) for v in r["answers"].values()]
        rates.append(float(np.mean(vals)) if vals else 0.0)
    ax.plot(xs, rates, marker="s", color=C["vermillion"])
    ax.set_xticks(xs)
    ax.set_xticklabels([str(r["scale"]) for r in rows])
    ax.set_ylim(-0.15, 1.15)
    ax.set_xlabel("Control-vector scale")
    ax.set_ylabel("Degenerate output rate")
    ax.set_title("Output collapse", pad=5)


def fig2e(ax):
    """角色探针：虚构出处率（左轴）与输出长度（右轴）。"""
    rows = load("neuron_sweep.json")
    xs = np.arange(len(rows))
    fab = []
    lens = []
    for r in rows:
        text = r["answers"].get("persona", "")
        fab.append(1.0 if "\u300a" in text else 0.0)
        lens.append(len(text))
    ax.plot(xs, fab, marker="o", color=C["vermillion"], label="Fabricated attribution")
    ax.set_xticks(xs)
    ax.set_xticklabels([str(r["scale"]) for r in rows])
    ax.set_ylim(-0.15, 1.15)
    ax.set_xlabel("Control-vector scale")
    ax.set_ylabel("Fabricated attribution")
    ax2 = ax.twinx()
    ax2.plot(xs, lens, marker="s", color=C["blue"], label="Reply length")
    ax2.set_ylabel("Reply length (characters)", labelpad=1)
    ax2.spines["top"].set_visible(False)
    ax.set_title("Role probe", pad=5)


def fig2f(ax):
    """声线探针：三档强度下五个探针的输出长度热图。"""
    rows = load("style_probe.json")
    probes = list(rows[0]["answers"].keys())
    mat = np.zeros((len(rows), len(probes)))
    for i, r in enumerate(rows):
        for j, p in enumerate(probes):
            mat[i, j] = len(r["answers"].get(p, ""))
    im = ax.imshow(mat, aspect="auto", cmap="viridis")
    ax.set_xticks(range(len(probes)))
    ax.set_xticklabels(probes, rotation=30, ha="right")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([str(r["scale"]) for r in rows])
    ax.set_ylabel("Control-vector scale")
    for i in range(len(rows)):
        for j in range(len(probes)):
            ax.text(j, i, "%d" % mat[i, j], ha="center", va="center", fontsize=6.5, color="white")
    cb = ax.inset_axes([1.04, 0.08, 0.03, 0.76])
    cbar = ax.figure.colorbar(im, cax=cb)
    cbar.ax.tick_params(labelsize=6.5)
    cbar.set_label("Reply length (characters)", fontsize=7)
    ax.set_title("Voice probe, reply length", pad=5)


FIGURES = [
    (fig2a, "fig2a_per_layer_separation"),
    (fig2b, "fig2b_per_layer_response_difference"),
    (fig2c, "fig2c_arithmetic_probe"),
    (fig2d, "fig2d_output_collapse"),
    (fig2e, "fig2e_role_probe"),
    (fig2f, "fig2f_voice_probe_length"),
]


def main() -> None:
    """逐张生成图 2 组的图，输出 PNG 与 PDF。"""
    apply_style()
    for fn, stem in FIGURES:
        for p in render(fn, OUT, stem, figsize=(3.8, 3.2)):
            print("wrote", p)


if __name__ == "__main__":
    main()
