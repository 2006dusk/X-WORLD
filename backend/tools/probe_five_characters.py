r"""真机探针：五个角色、五个地址的内容与逻辑交互。

跑法：python backend/tools/probe_five_characters.py

链路：
1. 逻辑段：清点卡片、会话、地区绑定、状态隔离、世界归属、装配注入、会话复用。
2. 内容段：五个角色各在同一句话下说一轮，核对回复落在自己地址、状态写回本人、
   别人的会话条数与状态不受影响。
3. 观察项：正文长度分布（模型自主长度，预设不写死上下限）。

数据层不落新的持久化表，结果写 JSON 文件。
输出写 UTF-8 文件，终端不回显中文。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8760"
OUT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "实践情况"
    / "data"
    / "sweep"
    / "five_characters_probe.json"
)

PRESET_ID = "p_builtin_rp"

# 五个角色、五个地址。地址写在 region_id 上，地点标签由后端按世界书名拼。
ROSTER: list[dict[str, str]] = [
    {
        "character_id": "c_demo",
        "name": "角色K",
        "region_id": "e_recording_studio",
        "region_name": "地址U",
        "book_id": "w_demo",
        "book_name": "地址N",
    },
    {
        "character_id": "c_card_05",
        "name": "角色L",
        "region_id": "e_3ff9fcafa681",
        "region_name": "夜班便利店",
        "book_id": "w_demo",
        "book_name": "地址N",
    },
    {
        "character_id": "c_0c49cf77304a",
        "name": "角色M",
        "region_id": "e_f717cbb056af",
        "region_name": "街角修理铺",
        "book_id": "w_demo",
        "book_name": "地址N",
    },
    {
        "character_id": "c_c9cf2f3c9783",
        "name": "角色N",
        "region_id": "e_70d44253ce6e",
        "region_name": "巷尾旧书店",
        "book_id": "w_demo",
        "book_name": "地址N",
    },
    {
        "character_id": "c_ffa73c631674",
        "name": "角色O",
        "region_id": "e_f111df4cfc76",
        "region_name": "白塔天桥",
        "book_id": "w_bde4893cfacd",
        "book_name": "白塔新区",
    },
]

# 每个地址的标志陈设。用于判定回复落在哪个地址。
REGION_MARKS: dict[str, list[str]] = {
    "e_recording_studio": ["控制台", "调音台", "推子", "旋钮", "地址U"],
    "e_3ff9fcafa681": ["冰柜", "收银台", "营业时间表", "便利店"],
    "e_f717cbb056af": ["卷帘门", "车胎", "油桶", "收音机", "机油", "修理铺"],
    "e_70d44253ce6e": ["老花镜", "书脊", "旧书", "纸箱", "纸页"],
    "e_f111df4cfc76": ["栏杆", "三轮车", "烤红薯", "外卖箱", "写字楼", "桥面"],
}

# 五个角色用同一句话，避免提示本身偏向某个地址。
USER_PROMPT = "我来了。你在做什么？把你眼前看到的和听到的说给我听。"


def req(method: str, path: str, payload: Any = None, timeout: int = 120) -> tuple[int, Any]:
    """发一个 JSON 请求，返回状态码与解析后的正文。"""
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def body_of(message: dict[str, Any]) -> str:
    """回合正文：场景块加对白块，状态块是元数据不计入。"""
    blocks = message.get("blocks")
    if isinstance(blocks, dict):
        parts = [str(blocks.get(name) or "") for name in ("scene", "dialogue")]
        if any(parts):
            return "".join(parts)
    return str(message.get("content") or "")


def session_counts() -> dict[str, int]:
    """所有会话的条数，按会话 id 归拢。"""
    status, listing = req("GET", "/v1/sessions/")
    items = listing.get("items", []) if isinstance(listing, dict) else []
    return {str(item.get("id")): int(item.get("message_count") or 0) for item in items}


def state_of(character_id: str) -> dict[str, Any]:
    """读一个角色的世界状态。"""
    status, state = req("GET", "/v1/state/?character_id=" + character_id)
    return state if isinstance(state, dict) else {}


def main() -> int:
    """跑完整条链路并落盘结果。"""
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url": BASE,
        "preset_id": PRESET_ID,
        "user_prompt": USER_PROMPT,
        "roster": ROSTER,
        "logic": {"checks": [], "passed": 0, "failed": 0},
        "content": {"checks": [], "passed": 0, "failed": 0, "turns": []},
        "observation": {},
    }

    def check(bucket: str, cid: str, name: str, ok: bool, detail: Any) -> None:
        """记一条断言。"""
        report[bucket]["checks"].append(
            {"id": cid, "name": name, "ok": bool(ok), "detail": detail}
        )
        key = "passed" if ok else "failed"
        report[bucket][key] += 1

    # ---------- 逻辑段 ----------
    status, cards = req("GET", "/v1/cards/")
    card_items = cards.get("items", []) if isinstance(cards, dict) else []
    card_map = {str(item.get("id")): item for item in card_items}
    check(
        "logic",
        "L1",
        "卡片库只有五个角色",
        len(card_items) == 5,
        {"count": len(card_items), "names": [item.get("name") for item in card_items]},
    )
    for person in ROSTER:
        card = card_map.get(person["character_id"])
        check(
            "logic",
            "L2",
            f"{person['name']} 的卡片在库里且绑定了 {person['region_name']}",
            bool(card)
            and str(card.get("region_id") or "") == person["region_id"],
            {
                "present": bool(card),
                "name": (card or {}).get("name"),
                "region_id": (card or {}).get("region_id"),
                "expect": person["region_id"],
            },
        )

    status, listing = req("GET", "/v1/sessions/")
    session_items = listing.get("items", []) if isinstance(listing, dict) else []
    session_map = {str(item.get("id")): item for item in session_items}
    check(
        "logic",
        "L3",
        "会话列表恰好五条，一个角色一条",
        len(session_items) == 5
        and len({str(item.get("character_id")) for item in session_items}) == 5,
        {
            "count": len(session_items),
            "items": [
                {
                    "id": item.get("id"),
                    "character_id": item.get("character_id"),
                    "message_count": item.get("message_count"),
                }
                for item in session_items
            ],
        },
    )
    for person in ROSTER:
        expected_id = "s_" + person["character_id"]
        item = session_map.get(expected_id)
        check(
            "logic",
            "L4",
            f"{person['name']} 的会话 id 由角色决定",
            bool(item) and str(item.get("character_id")) == person["character_id"],
            {"expect_id": expected_id, "found": item},
        )

    states: dict[str, dict[str, Any]] = {}
    for person in ROSTER:
        state = state_of(person["character_id"])
        states[person["character_id"]] = state
        expect_label = f"{person['book_name']}_{person['region_name']}"
        check(
            "logic",
            "L5",
            f"{person['name']} 绑定在 {person['region_name']}",
            str(state.get("region_id") or "") == person["region_id"]
            and str(state.get("location") or "") == expect_label,
            {
                "region_id": state.get("region_id"),
                "location": state.get("location"),
                "expect_region": person["region_id"],
                "expect_location": expect_label,
            },
        )

    locations = [str(states[p["character_id"]].get("location") or "") for p in ROSTER]
    check(
        "logic",
        "L6",
        "五个人的地点互不相同",
        len(set(locations)) == len(locations) and all(locations),
        {"locations": locations},
    )
    item_sets = {
        p["name"]: set(states[p["character_id"]].get("items") or []) for p in ROSTER
    }
    overlaps = []
    names = [p["name"] for p in ROSTER]
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            shared = item_sets[left] & item_sets[right]
            if shared:
                overlaps.append({left, right, sorted(shared)})
    check(
        "logic",
        "L7",
        "随身物品不串人",
        not overlaps,
        {"items": {k: sorted(v) for k, v in item_sets.items()}, "overlaps": overlaps},
    )

    for person in ROSTER:
        item = session_map.get("s_" + person["character_id"]) or {}
        check(
            "logic",
            "L8",
            f"{person['name']} 的对话在 {person['book_name']}",
            list(item.get("world_book_ids") or []) == [person["book_id"]],
            {
                "world_book_ids": item.get("world_book_ids"),
                "expect": [person["book_id"]],
            },
        )

    for person in ROSTER:
        query = urllib.parse.urlencode(
            {
                "session": "s_" + person["character_id"],
                "character_id": person["character_id"],
                "preset_id": PRESET_ID,
                "world_book_ids": person["book_id"],
                "region_id": person["region_id"],
                "user_input": USER_PROMPT,
            }
        )
        status, preview = req("GET", "/v1/prompt-preview?" + query)
        entries: list[str] = []
        for segment in (preview.get("segments") or []) if isinstance(preview, dict) else []:
            if segment.get("name") == "world_info":
                entries = list(segment.get("entries") or [])
        check(
            "logic",
            "L9",
            f"{person['name']} 的装配注入 {person['region_name']}",
            person["region_id"] in entries,
            {"region_id": person["region_id"], "world_info_entries": entries},
        )

    before_counts = session_counts()
    status, again = req(
        "POST",
        "/v1/sessions/",
        {
            "character_id": "c_card_05",
            "world_book_ids": ["w_demo"],
            "region_id": "e_3ff9fcafa681",
        },
    )
    after_counts = session_counts()
    check(
        "logic",
        "L10",
        "同一个角色再次进入回到同一条对话，不新建第二条",
        str(again.get("id") or "") == "s_c_card_05"
        and len(after_counts) == len(before_counts)
        and after_counts.get("s_c_card_05")
        == before_counts.get("s_c_card_05"),
        {
            "session_id": again.get("id"),
            "before": before_counts,
            "after": after_counts,
        },
    )

    # ---------- 内容段 ----------
    for person in ROSTER:
        cid = person["character_id"]
        session_id = "s_" + cid
        counts_before = session_counts()
        states_before = {p["character_id"]: state_of(p["character_id"]) for p in ROSTER}

        started = time.perf_counter()
        status, result = req(
            "POST",
            "/v1/chat/completions",
            {
                "model": "xw-local-default",
                "messages": [{"role": "user", "content": USER_PROMPT}],
                "session": session_id,
                "stream": False,
                "xw": {
                    "character_id": cid,
                    "preset_id": PRESET_ID,
                    "world_book_ids": [person["book_id"]],
                    "region_id": person["region_id"],
                    "memory_mode": "hybrid",
                    "max_context_tokens": 16384,
                },
            },
            timeout=600,
        )
        elapsed = time.perf_counter() - started

        message: dict[str, Any] = {}
        if isinstance(result, dict):
            choices = result.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
        text = body_of(message)
        blocks = message.get("blocks") if isinstance(message.get("blocks"), dict) else {}
        scene = str(blocks.get("scene") or "")
        dialogue = str(blocks.get("dialogue") or "")

        own_marks = [mark for mark in REGION_MARKS[person["region_id"]] if mark in text]
        foreign: dict[str, list[str]] = {}
        for region_id, marks in REGION_MARKS.items():
            if region_id == person["region_id"]:
                continue
            hits = [mark for mark in marks if mark in text]
            if hits:
                foreign[region_id] = hits
        leak_regions = [rid for rid, hits in foreign.items() if len(hits) >= 2]

        turn = {
            "character_id": cid,
            "name": person["name"],
            "region_id": person["region_id"],
            "region_name": person["region_name"],
            "book_id": person["book_id"],
            "session_id": session_id,
            "status": status,
            "elapsed_s": round(elapsed, 2),
            "body_chars": len(text),
            "scene": scene,
            "dialogue": dialogue,
            "state_block": str(blocks.get("state") or ""),
            "own_marks": own_marks,
            "foreign_marks": foreign,
            "reply": text,
        }
        report["content"]["turns"].append(turn)

        check(
            "content",
            "C1",
            f"{person['name']} 这一轮出字了",
            status == 200 and len(text.strip()) > 0,
            {"status": status, "chars": len(text)},
        )
        check(
            "content",
            "C2",
            f"{person['name']} 的回复落在 {person['region_name']}",
            len(own_marks) >= 1,
            {"own_marks": own_marks, "reply": text[:200]},
        )
        check(
            "content",
            "C3",
            f"{person['name']} 的回复没有别的地址的陈设",
            not leak_regions,
            {"foreign_marks": foreign, "leak_regions": leak_regions},
        )
        check(
            "content",
            "C4",
            f"{person['name']} 有对白",
            len(dialogue.strip()) >= 4,
            {"dialogue": dialogue[:200]},
        )

        xw_state = (result.get("xw") or {}).get("state") if isinstance(result, dict) else {}
        xw_state = xw_state if isinstance(xw_state, dict) else {}
        # 状态落在角色自己的文件里：读回来与本轮回执一致，且地区没变。
        own_state = state_of(cid)
        check(
            "content",
            "C5",
            f"{person['name']} 的状态写回本人",
            str(xw_state.get("region_id") or "") == person["region_id"]
            and str(own_state.get("region_id") or "") == person["region_id"]
            and str(own_state.get("location") or "") == str(xw_state.get("location") or "")
            and str(own_state.get("position") or "") == str(xw_state.get("position") or ""),
            {"returned": xw_state, "stored": own_state},
        )

        counts_after = session_counts()
        own_delta = counts_after.get(session_id, 0) - counts_before.get(session_id, 0)
        others_delta = {
            key: counts_after.get(key, 0) - counts_before.get(key, 0)
            for key in counts_before
            if key != session_id and counts_after.get(key, 0) != counts_before.get(key, 0)
        }
        check(
            "content",
            "C6",
            f"{person['name']} 这一轮只涨自己那条对话",
            own_delta > 0 and not others_delta,
            {"own_delta": own_delta, "others_delta": others_delta},
        )

        touched = []
        for other in ROSTER:
            if other["character_id"] == cid:
                continue
            after = state_of(other["character_id"])
            before = states_before[other["character_id"]]
            if after != before:
                touched.append(
                    {
                        "name": other["name"],
                        "before": before,
                        "after": after,
                    }
                )
        check(
            "content",
            "C7",
            f"{person['name']} 这一轮没动别人的状态",
            not touched,
            {"touched": touched},
        )

    lengths = [int(turn["body_chars"]) for turn in report["content"]["turns"]]
    in_band = [value for value in lengths if 150 <= value <= 300]
    report["observation"]["body_chars"] = lengths
    report["observation"]["in_target_band_150_300"] = len(in_band)
    report["observation"]["target_band_total"] = len(lengths)

    report["summary"] = {
        "logic_passed": report["logic"]["passed"],
        "logic_failed": report["logic"]["failed"],
        "content_passed": report["content"]["passed"],
        "content_failed": report["content"]["failed"],
        "total_failed": report["logic"]["failed"] + report["content"]["failed"],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("probe written:", OUT)
    print("summary:", json.dumps(report["summary"], ensure_ascii=False))
    print("lengths:", lengths)
    return 0 if report["summary"]["total_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
