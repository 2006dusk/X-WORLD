"""学术风格绘图共用样式。

配色取瑰红与青绿主色调的扩展色板；坐标轴只留左下框线；每张图独立成文件。
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# 瑰红与青绿主色调的扩展色板。
# 玫瑰族（vermillion / orange）在明度上拉开，瑰红与青绿在色相与明度上同时分离，
# 不构成纯红绿配对；青绿族（blue / sky / green）按明度分三档；浅色系在白底上保留对比。
C = {
    "black": "#1A1A1A",
    "orange": "#701E3C",
    "sky": "#5CB0A6",
    "green": "#2A7391",
    "yellow": "#E0C58F",
    "blue": "#31968A",
    "vermillion": "#C43B57",
    "purple": "#D8A13E",
    "grey": "#7A7A7A",
    "lightgrey": "#C9C9C9",
}


def apply_style() -> None:
    """设置全局绘图样式：无衬线字体、细轴线、无顶端框线。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    family = "Arial" if "Arial" in available else "DejaVu Sans"
    plt.rcParams.update({
        "font.family": family,
        "font.size": 8,
        "axes.labelsize": 8.5,
        "axes.titlesize": 9,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "legend.fontsize": 7.5,
        "axes.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.major.size": 3.0,
        "ytick.major.size": 3.0,
        "lines.linewidth": 1.1,
        "lines.markersize": 4.0,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "figure.dpi": 150,
    })


def panel_label(ax, letter: str, dx: float = -0.16, dy: float = 1.08) -> None:
    """在面板左上角加粗体小写面板标号。组图时使用，单图不用。"""
    ax.text(dx, dy, letter, transform=ax.transAxes, fontsize=10, fontweight="bold",
            ha="left", va="top", color="black")


def save(fig, out_dir: Path, stem: str) -> list:
    """输出 300 dpi PNG 与矢量 PDF，返回生成路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / (stem + ".png")
    pdf = out_dir / (stem + ".pdf")
    fig.savefig(png, dpi=300)
    fig.savefig(pdf)
    plt.close(fig)
    return [png, pdf]


def render(panel_fn, out_dir: Path, stem: str, figsize=(3.6, 3.0), pad=1.0) -> list:
    """把一个面板函数画成独立图片，输出 PNG 与 PDF。"""
    fig, ax = plt.subplots(figsize=figsize)
    panel_fn(ax)
    fig.tight_layout(pad=pad)
    return save(fig, out_dir, stem)
