"""界面截图与布局自检。

按给定宽度渲染页面并存图，同时把输入区那一行的控件位置量出来：
同一行的控件 y 坐标应该一致（说明没换行），控件总宽不该超出输入框，
页面也不该出现横向滚动。

用法：
    python backend/tools/capture_ui.py --widths 360,412,1280
    python backend/tools/capture_ui.py --url http://127.0.0.1:8760/ --out tmp

只用系统已装的 Edge，不另外下载浏览器。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 量输入区这一行：在浏览器里执行，返回控件位置。
# 参数 sel 是要额外量计算样式的选择器（点击后的高亮之类）。
PROBE_JS = """
(sel) => {
  const row = document.querySelector('.composer-row');
  const box = document.querySelector('.composer-box');
  if (!row || !box) return { ok: false, reason: 'no composer' };
  const rb = row.getBoundingClientRect();
  const bb = box.getBoundingClientRect();
  const items = Array.from(row.children).map((el) => {
    const r = el.getBoundingClientRect();
    return {
      cls: String(el.className),
      x: Math.round(r.x),
      y: Math.round(r.y),
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  });
    const target = sel ? document.querySelector(sel) : null;
  let style = null;
  if (target) {
    const cs = getComputedStyle(target);
    style = {
      background: cs.backgroundColor,
      border: cs.borderTopWidth + ' ' + cs.borderTopStyle + ' ' + cs.borderTopColor,
      outline: cs.outline,
      boxShadow: cs.boxShadow,
    };
  }
  const ys = items.map((item) => item.y);
  const lines = new Set(ys).size;
  const right = Math.max(...items.map((item) => item.x + item.w));
  return {
    ok: true,
    viewportW: window.innerWidth,
    rowLeft: Math.round(rb.x),
    rowWidth: Math.round(rb.width),
    boxLeft: Math.round(bb.x),
    boxRight: Math.round(bb.right),
    itemsRight: right,
    overflowRight: Math.round(right - bb.right),
    lines,
    items,
    docScrollW: document.documentElement.scrollWidth,
    horizontalScroll: document.documentElement.scrollWidth > window.innerWidth,
    targetStyle: style,
  };
}
"""


def capture(
    url: str,
    widths: list[int],
    out_dir: Path,
    height: int,
    click: str = "",
) -> list[dict[str, Any]]:
    """按宽度渲染、截图并量布局。"""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        try:
            for width in widths:
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=2,
                )
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(1200)
                if click:
                    page.click(click)
                    page.wait_for_timeout(500)
                shot = out_dir / f"xw-{width}.png"
                page.screenshot(path=str(shot))
                probe = page.evaluate(PROBE_JS, click)
                probe["width"] = width
                probe["shot"] = str(shot)
                results.append(probe)
                page.close()
        finally:
            browser.close()
    return results


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="X-WORLD 界面截图与布局自检")
    parser.add_argument("--url", default="http://127.0.0.1:8760/", help="页面地址")
    parser.add_argument("--widths", default="360,412,768,1280", help="视口宽度，逗号分隔")
    parser.add_argument("--height", type=int, default=844, help="视口高度")
    parser.add_argument("--out", default="", help="截图输出目录，默认放到工程根的 tmp/ui")
    parser.add_argument("--click", default="", help="截图前点一下这个选择器，并量它的计算样式")
    args = parser.parse_args()

    widths = [int(item) for item in args.widths.split(",") if item.strip()]
    root = Path(__file__).resolve().parents[2]
    out_dir = Path(args.out) if args.out else root / "tmp" / "ui"

    results = capture(args.url, widths, out_dir, args.height, args.click)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
