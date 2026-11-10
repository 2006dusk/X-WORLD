r"""场景转移与物品获取的对话能力探针。

两组场景，都走真实后端 /v1/chat/completions（内置预设、角色卡、世界书、
历史落盘、复读闸门、长度闸门全在链路上）。

场景转移组：角色从地址U换到别处，测模型是否真的换地方，
以及旧场景的道具（控制台、调音台、推子）是否还黏在正文里。
物品获取组：用户把物品交给角色，测模型是否接住动作、
是否提到该物品，以及下一轮追问时是否仍认领这件物品。

指标全部是确定性关键词判定，不依赖模型评分。

用法：
    python backend\tools\probe_scene_and_item.py
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app.inference.repetition import copied_sentences  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SCENE_SESSION = "s_scene_probe"
ITEM_SESSION = "s_item_probe"

# 地址U里的道具。场景转移后还出现这些，说明模型没离开旧场景。
STALE_PROPS = ("控制台", "调音台", "推子", "台灯", "音量旋钮")

SCENE_TURNS = [
    {
        "prompt": "我们走吧，别在棚里待着了。",
        "fresh": ("外面", "巷", "街", "雨", "门口", "外头"),
        "note": "离开地址U",
    },
    {
        "prompt": "去街对面那家地址X坐坐吧。",
        "fresh": ("地址X", "店", "柜台", "屋檐", "老板", "货架"),
        "note": "换到街对面的店",
    },
    {
        "prompt": "雨小点了，我们沿巷子走一段。",
        "fresh": ("巷", "路", "脚步", "走", "地上", "积水"),
        "note": "沿巷子移动",
    },
    {
        "prompt": "你在这儿等我，我进去买包烟。",
        "fresh": ("门口", "店", "雨", "巷", "伞", "檐"),
        "note": "角色留在门外",
    },
    {
        "prompt": "行了，回棚里吧，外头太冷。",
        "fresh": ("棚", "回", "控制台", "灯"),
        "note": "返回地址U，旧场景道具回来是预期",
        "stale_ok": True,
    },
]

ITEM_TURNS = [
    {
        "prompt": "这盘带子给你，你替我收着。",
        "item": ("带子", "磁带"),
        "action": ("接", "收", "拿", "揣", "握", "放", "装", "递"),
        "note": "交接磁带",
    },
    {
        "prompt": "录音笔也带上，晚上要用。",
        "item": ("录音笔",),
        "action": ("拿", "带", "收", "装", "揣", "插", "挂", "拎", "抓"),
        "note": "追加一件工具",
    },
    {
        "prompt": "钥匙在桌上，你拿着，待会儿门要用。",
        "item": ("钥匙",),
        "action": ("拿", "揣", "握", "收", "接", "放", "抓"),
        "note": "再交一件",
    },
    {
        "prompt": "外套给你，路上冷。",
        "item": ("外套", "大衣"),
        "action": ("接", "拿", "穿", "披", "搭", "收", "递", "裹"),
        "note": "交衣服",
    },
]

ITEM_FOLLOWUP = {
    "prompt": "刚才那盘带子还在你那儿吗？",
    "hold": ("在", "带着", "收着", "揣", "拿着", "收到", "留着"),
    "deny": ("不知道", "什么带子", "不见了", "忘了", "不记得", "没有"),
}


def ratio(left: str, right: str) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def run_turn(client, session: str, prompt: str) -> dict:
    """跑一轮。replace 事件后清空已累积文本，与前端语义一致。"""
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": "p_builtin_rp",
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    parts: list[str] = []
    events: list[str] = []
    started = time.time()
    with client.stream("POST", BASE + "/v1/chat/completions", json=request) as stream:
        for line in stream.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
                events.append(name)
                if name == "replace":
                    parts.clear()
                continue
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                content = payload.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
    return {
        "text": "".join(parts),
        "events": events,
        "elapsed": time.time() - started,
    }


def hit(text: str, words) -> list[str]:
    """命中的关键词列表。"""
    return [word for word in words if word in text]


def run_group(
    client,
    session: str,
    turns: list[dict],
    kind: str,
    cold: bool = False,
) -> dict:
    """跑一组场景，返回逐轮记录与汇总。

    cold 为真时每轮用独立会话，不带历史，用来区分「当轮接不住」
    与「历史把当轮挤掉」。
    """
    rows: list[dict] = []
    texts: list[str] = []
    for index, turn in enumerate(turns):
        turn_session = session + "_t" + str(index + 1) if cold else session
        result = run_turn(client, turn_session, turn["prompt"])
        text = result["text"]
        best = 0.0
        identical = False
        for previous in texts:
            best = max(best, ratio(text, previous))
            if text == previous:
                identical = True
        copied = copied_sentences(text, texts[-1]) if texts else []

        fresh = hit(text, turn.get("fresh", ()))
        stale = [] if turn.get("stale_ok") else hit(text, STALE_PROPS)
        item = hit(text, turn.get("item", ()))
        action = hit(text, turn.get("action", ()))

        rows.append({
            "turn": index + 1,
            "prompt": turn["prompt"],
            "note": turn.get("note", ""),
            "chars": len(text),
            "in_band": 150 <= len(text) <= 300,
            "quotes": text.count("“"),
            "quote_balanced": text.count("“") == text.count("”"),
            "fresh_hit": fresh,
            "stale_hit": stale,
            "item_hit": item,
            "action_hit": action,
            "max_ratio": round(best, 3),
            "identical": identical,
            "copied_from_prev": len(copied),
            "replace_events": result["events"].count("replace"),
            "elapsed": round(result["elapsed"], 2),
            "text": text,
        })
        texts.append(text)
        print(
            "  T%d %d字 带内=%s 对白=%d 新场景=%s 残留=%s 物品=%s 动作=%s 相似=%.3f"
            % (
                index + 1,
                len(text),
                150 <= len(text) <= 300,
                text.count("“"),
                ",".join(fresh) or "-",
                ",".join(stale) or "-",
                ",".join(item) or "-",
                ",".join(action) or "-",
                best,
            )
        )
        print("     " + text)

    summary = {
        "kind": kind,
        "cold": cold,
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "with_dialogue": sum(1 for row in rows if row["quotes"] >= 1),
        "quote_balanced": sum(1 for row in rows if row["quote_balanced"]),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "turns_copy_prev": sum(1 for row in rows if row["copied_from_prev"]),
        "max_ratio": max(row["max_ratio"] for row in rows),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    if kind == "scene":
        summary["fresh_hit_turns"] = sum(1 for row in rows if row["fresh_hit"])
        summary["stale_prop_turns"] = sum(1 for row in rows if row["stale_hit"])
        summary["stale_props"] = sorted({w for row in rows for w in row["stale_hit"]})
    else:
        summary["item_hit_turns"] = sum(1 for row in rows if row["item_hit"])
        summary["action_hit_turns"] = sum(1 for row in rows if row["action_hit"])
    return {"summary": summary, "rows": rows}


def main() -> int:
    """跑两组场景并落盘。"""
    results: dict = {}
    with httpx.Client(timeout=240.0) as client:
        print("场景转移组：")
        results["scene"] = run_group(client, SCENE_SESSION, SCENE_TURNS, "scene")
        print()
        print("物品获取组：")
        results["item"] = run_group(client, ITEM_SESSION, ITEM_TURNS, "item")
        print()
        print("物品获取组（复跑，换新会话）：")
        results["item_run2"] = run_group(
            client, ITEM_SESSION + "_run2", ITEM_TURNS, "item"
        )
        print()
        print("物品获取组（冷启动，每轮独立会话）：")
        results["item_cold"] = run_group(
            client, ITEM_SESSION + "_cold", ITEM_TURNS, "item", cold=True
        )

        # 物品持有一致性追问
        print("持有一致性追问：")
        follow = run_turn(client, ITEM_SESSION, ITEM_FOLLOWUP["prompt"])
        text = follow["text"]
        hold = hit(text, ITEM_FOLLOWUP["hold"])
        deny = hit(text, ITEM_FOLLOWUP["deny"])
        results["item_followup"] = {
            "prompt": ITEM_FOLLOWUP["prompt"],
            "chars": len(text),
            "hold_hit": hold,
            "deny_hit": deny,
            "in_band": 150 <= len(text) <= 300,
            "text": text,
        }
        print(
            "  认领词=%s 否认词=%s 字数=%d"
            % (",".join(hold) or "-", ",".join(deny) or "-", len(text))
        )
        print("  " + text)

    for name in ("scene", "item", "item_run2", "item_cold"):
        print()
        print(name + " " + json.dumps(results[name]["summary"], ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "scene_and_item.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print()
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
