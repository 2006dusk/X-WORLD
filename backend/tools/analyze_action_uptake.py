r"""量化五组高强度场景里的动作承接率。

用户在每一轮都给了明确的身体动作。模型必须把这些动作接住并写出来，
否则就是在自说自话。这一项按确定性词表判定：

- 每轮预置一组该轮动作的关键词；
- 回复里命中即视为承接；
- 另计句式复读：与前文重合超过 90% 的轮次。

用法：
    python tools\analyze_action_uptake.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
EXPL = DATA / "explicit_5set.json"

# 场景 id 对应每轮的动作关键词。
EXPECT = {
    "A": [
        ["锁", "门", "调音台", "衬衫", "腰"],
        ["手腕", "金属", "耳垂", "压", "咬"],
        ["喉咙", "领口", "发根", "后颈", "扯"],
        ["抱", "控制台", "推子", "撞", "响"],
    ],
    "B": [
        ["地址T", "门", "镜", "锁"],
        ["背后", "撑", "洗手台", "扣", "腰"],
        ["推", "镜", "扣子", "解"],
        ["吻", "手腕", "掌心", "小腹", "瓷砖"],
    ],
    "C": [
        ["沙发", "跨", "腿", "胸口", "撑"],
        ["后颈", "压", "扣", "衬衫", "解"],
        ["咬", "下唇", "裤腰", "探"],
        ["掐", "胯", "顶", "弹簧", "闷响"],
    ],
    "D": [
        ["地址W", "灯", "衬衫", "楼梯"],
        ["衬衫", "背后", "抱"],
        ["扯", "衬衫", "扔", "床垫"],
        ["床垫", "压", "头发", "拉近"],
    ],
    "E": [
        ["雨", "车", "安全带", "腿"],
        ["腰", "皮带", "扣", "金属", "车厢"],
        ["肩", "暖风", "掌心", "温度"],
        ["俯", "吻", "方向盘", "背", "响"],
    ],
}

GRAM = 8


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
    rows = json.loads(EXPL.read_text(encoding="utf-8"))
    print("== 动作承接与句式复读 ==")
    for row in rows:
        scenes = row.get("scenes") or []
        if not scenes:
            continue
        print("==", row.get("name"), row.get("params"), row.get("quant"))
        total = 0
        hit = 0
        repeats = []
        for scene in scenes:
            sid = str(scene.get("id"))
            keys_list = EXPECT.get(sid) or []
            history: list[str] = []
            for turn in scene.get("turns") or []:
                idx = int(turn.get("turn") or 0) - 1
                text = str(turn.get("content") or "")
                keys = keys_list[idx] if idx < len(keys_list) else []
                ok = bool(keys) and any(k in text for k in keys)
                ov = overlap(text, history)
                total += 1
                if ok:
                    hit += 1
                if ov >= 0.9:
                    repeats.append("{}场景第{}轮".format(sid, turn.get("turn")))
                history.append(text)
        print("  动作承接 {}/{}  句式复读轮次 {}".format(hit, total, "、".join(repeats) if repeats else "无"))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
