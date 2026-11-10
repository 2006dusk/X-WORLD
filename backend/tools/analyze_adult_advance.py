"""把成人向五回合的推进与复述分析写成 JSON，供报告脚本读取。

用法：
    python tools\analyze_adult_advance.py [--json]
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADULT = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "adult.json"
OUT = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "adult_advance.json"
GRAM = 8

EXPECT = {
    1: ["腰", "后颈", "累"],
    2: ["吻", "手腕", "调音台"],
    3: ["呼吸", "发根", "别停"],
    4: ["沙发", "想要", "问"],
    5: ["心口", "想要", "吻"],
}


def grams(text: str) -> set[str]:
    return {text[i : i + GRAM] for i in range(0, max(len(text) - GRAM, 0))}


def overlap(text: str, history: list[str]) -> float:
    if not history:
        return 0.0
    mine = grams(text)
    if not mine:
        return 0.0
    seen: set[str] = set()
    for item in history:
        seen |= grams(item)
    return round(len(mine & seen) / len(mine), 4)


def main() -> int:
    rows = json.loads(ADULT.read_text(encoding="utf-8"))
    out = []
    for row in rows:
        turns = row.get("turns") or []
        if not turns:
            continue
        history: list[str] = []
        overlaps: list[float] = []
        responded: list[bool] = []
        detail = []
        for turn in turns:
            text = str(turn.get("content") or "")
            ov = overlap(text, history)
            keys = EXPECT.get(int(turn.get("turn") or 0)) or []
            hit = bool(keys) and any(k in text for k in keys)
            responded.append(hit)
            overlaps.append(ov)
            detail.append({"turn": turn.get("turn"), "overlap": ov, "responded": hit})
            history.append(text)
        out.append({
            "name": row.get("name"),
            "responded_count": sum(1 for r in responded if r),
            "turn_count": len(responded),
            "max_overlap": max(overlaps) if overlaps else 0.0,
            "verbatim_repeat": any(o >= 0.9 for o in overlaps),
            "detail": detail,
        })
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入 " + str(OUT))
    for item in out:
        print("  {} 回应 {}/{} 最大重合 {:.1%} 复述 {}".format(
            item["name"], item["responded_count"], item["turn_count"],
            item["max_overlap"], "有" if item["verbatim_repeat"] else "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
