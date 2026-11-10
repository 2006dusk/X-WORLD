r"""算十轮对话的上下文预算，判断 ctx 4096 是否够用。

用法：
    python tools\analyze_context_budget.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
MEM = DATA / "memory_10turn.json"


def main() -> int:
    rows = json.loads(MEM.read_text(encoding="utf-8"))
    print("== 十轮对话的上下文占用与预算 ==")
    for row in rows:
        turns = row.get("turns") or []
        if not turns:
            continue
        prompts = [int((t.get("metrics") or {}).get("prompt_tokens") or 0) for t in turns]
        comps = [int((t.get("metrics") or {}).get("completion_tokens") or 0) for t in turns]
        if not prompts:
            continue
        last = prompts[-1]
        print("==", row.get("name"))
        print("  提示 token 逐轮 {}".format(prompts))
        print("  生成 token 逐轮 {}".format(comps))
        print("  第十轮提示占 ctx 4096 的 {:.0%}，剩余 {}".format(
            last / 4096, 4096 - last - comps[-1] if comps else 0))
        # 每轮平均增量
        if len(prompts) > 1:
            step = (prompts[-1] - prompts[0]) / (len(prompts) - 1)
            print("  每轮上下文增量约 {:.0f} tok；按此推算，第 {} 轮占满 4096".format(
                step, int((4096 - prompts[0]) / step) + 1 if step else 0))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
