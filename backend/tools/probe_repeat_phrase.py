r"""标定：相邻两轮对白共享的六字片段有多少。

用途：给「换个措辞重复同一种意思」找一个确定性阈值。
判据：新一轮对白里出现了上一轮对白的整段六字以上片段，就是同义复读。
先看真实会话里正常轮次的分布，再定阈值。

跑法：python backend/tools/probe_repeat_phrase.py
只读，不落盘，不写会话流水。
"""

from __future__ import annotations

import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from app import store  # noqa: E402

SESSION = "s_c_card_01"
CHARACTER = "c_card_01"


def shared_runs(left: str, right: str, size: int = 6) -> list[str]:
    """两边共有的、长度不小于 size 的片段。

    用后缀公共前后缀逐位扩展：左到右扫，取最长匹配。
    """
    runs: list[str] = []
    used = [False] * len(right)
    i = 0
    while i < len(left):
        best = ""
        for j in range(len(right)):
            if used[j]:
                continue
            k = 0
            while (
                i + k < len(left)
                and j + k < len(right)
                and left[i + k] == right[j + k]
            ):
                k += 1
            if k > len(best):
                best = left[i:i + k]
        if len(best) >= size:
            runs.append(best)
            for j in range(len(right)):
                if right.startswith(best, j):
                    used[j] = True
            i += len(best)
        else:
            i += 1
    return runs


def main() -> int:
    """入口。"""
    rows = store.session_path(SESSION, CHARACTER)
    dialogues: list[str] = []
    for row in rows:
        if str(row.get("role")) != "assistant":
            continue
        blocks = row.get("blocks")
        if not isinstance(blocks, dict):
            continue
        text = str(blocks.get("dialogue") or "").strip()
        if text:
            dialogues.append(text)
    counts: list[int] = []
    for index in range(1, len(dialogues)):
        runs = shared_runs(dialogues[index - 1], dialogues[index], 6)
        counts.append(len(runs))
        print(json.dumps({
            "turn": index + 1,
            "runs": len(runs),
            "longest": max((len(item) for item in runs), default=0),
            "samples": [item for item in runs if len(item) >= 8][:3],
        }, ensure_ascii=True))
    print(json.dumps({
        "turns": len(dialogues),
        "avg_runs": round(sum(counts) / len(counts), 2) if counts else 0,
        "max_runs": max(counts) if counts else 0,
        "over_2": sum(1 for item in counts if item >= 2),
        "over_3": sum(1 for item in counts if item >= 3),
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
