"""括号指定动作的落实率探针：同一批硬用例重复多轮，对比措辞版本。

要回答的问题只有一个：用户圆括号里指定的动作，有没有真的写进动作块。
用户反馈「模型还是有自己的想法」——括号里的事被换成角色自己的动作，
或者只被说进对白里。这一层用确定性比对：指定要素是否出现在动作块。

用例挑的是走查里落实最差的几轮（模型最容易绕开的那几条），
每个用例重复若干轮：模型采样有随机性（temperature 0.7），
单轮结果说明不了问题。

用法：
  F:/python/python.exe backend/tools/probe_paren_action.py run --reps 4

只打印到终端，不落新的持久化数据表。跑完自动删掉临时角色卡与临时会话。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402

WEB = os.environ.get("XWORLD_WEB", "http://127.0.0.1:8760")
PRESET = "p_builtin_rp"
TEMP_PREFIX = "TMP_PAREN"

# 每个用例：一张卡、一次用户输入、必须出现在动作块里的要素组。
# 要素组是同义写法的一组，只要动作块命中其中任意一个就算命中。
CASES: list[dict[str, Any]] = [
    {
        "card": "c_card_01",
        "label": "角色A-搁茶盏敲床柱",
        "world": "w_a6154bb9b4b8",
        "region": "e_eaff8133bd29",
        "user": "（她把手里的茶盏搁在榻沿，指尖敲了敲床柱）",
        "groups": [["搁", "放"], ["茶盏", "茶杯", "盏"], ["敲", "点", "叩"]],
    },
    {
        "card": "c_card_01",
        "label": "角色A-取项圈垂眼",
        "world": "w_a6154bb9b4b8",
        "region": "e_eaff8133bd29",
        "user": "（她从榻边取过那副带锁链的项圈，垂眼看你）",
        "groups": [["取", "拿", "摘"], ["项圈", "锁链", "镣铐"], ["垂", "低", "俯"]],
    },
    {
        "card": "c_card_04",
        "label": "角色D-拿手铐转",
        "world": "w_2bc74dc33c01",
        "region": "e_9dd0cb937252",
        "user": "（她从抽屉里拿出那副手铐，在指间转了转）",
        "groups": [["拿", "取", "掏", "摸"], ["手铐", "铐子", "铐"], ["转", "晃", "掂"]],
    },
    {
        "card": "c_card_04",
        "label": "角色D-后门男生探头",
        "world": "w_2bc74dc33c01",
        "region": "e_9dd0cb937252",
        "user": "（教室后门被推开，隔壁班两个男生探头进来）",
        "groups": [["推", "开"], ["后门", "门"], ["男生", "两个", "两人", "他们"]],
    },
    {
        "card": "c_card_03",
        "label": "角色C-扔护腿蹲脚踝",
        "world": "w_2bc74dc33c01",
        "region": "e_9dd0cb937252",
        "user": "（她把护腿扔到一边，蹲下来捏了捏自己的脚踝）",
        "groups": [["扔", "丢", "甩", "卸"], ["护腿", "护具", "绑腿"],
                   ["蹲", "屈膝"], ["脚踝", "踝"]],
    },
    {
        "card": "c_card_02",
        "label": "角色B-取物搁账本",
        "world": "",
        "region": "",
        "user": "（她从匣子里取出一件东西，搁在账本旁边）",
        "groups": [["取", "拿"], ["匣子", "匣"], ["搁", "放", "摆"]],
    },
]


def seed_temp_card(case: dict[str, Any]) -> str:
    """复制一张临时角色卡，返回临时卡 id。"""
    source = store.get_card(str(case["card"]))
    if not isinstance(source, dict):
        raise RuntimeError("card_not_found:" + str(case["card"]))
    payload = dict(source)
    data = payload.get("data")
    payload["data"] = dict(data) if isinstance(data, dict) else {}
    payload["name"] = TEMP_PREFIX + "_" + str(case["label"])
    payload.pop("id", None)
    card = store.save_card(payload)
    return str(card["id"])


def ask(card_id: str, session_id: str, case: dict[str, Any], user_input: str) -> dict[str, str]:
    """走真实 HTTP 接口生成一轮，取回结构化块。"""
    xw: dict[str, Any] = {
        "character_id": card_id,
        "preset_id": PRESET,
        "world_book_ids": [case["world"]] if case["world"] else [],
        "memory_mode": "hybrid",
        "max_context_tokens": 16384,
    }
    if case["region"]:
        xw["region_id"] = case["region"]
    body = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": user_input}],
        "session": session_id,
        "stream": False,
        "xw": xw,
    }
    request = urllib.request.Request(
        WEB + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)
    message = (data.get("choices") or [{}])[0].get("message") or {}
    blocks = message.get("blocks") or {}
    return {
        "action": str(blocks.get("action") or ""),
        "dialogue": str(blocks.get("dialogue") or ""),
    }


def run_case(case: dict[str, Any], reps: int) -> dict[str, Any]:
    """跑一个用例若干轮，统计要素命中率。"""
    card_id = seed_temp_card(case)
    rows: list[dict[str, Any]] = []
    session_ids: list[str] = []
    try:
        for rep in range(reps):
            session_id = "s_tmp_paren_" + card_id.replace("-", "")[-8:] + "_" + str(rep)
            session_ids.append(session_id)
            store.ensure_session(session_id, card_id, PRESET)
            result = ask(card_id, session_id, case, str(case["user"]))
            action = result["action"]
            dialogue = result["dialogue"]
            groups = list(case["groups"])
            hits = [any(word in action for word in group) for group in groups]
            # 说而不做：要素没进动作块，却出现在对白里。
            said = [
                group[0]
                for group, hit in zip(groups, hits)
                if not hit and any(word in dialogue for word in group)
            ]
            rows.append({
                "case": case["label"],
                "rep": rep,
                "groups_total": len(groups),
                "groups_hit": sum(1 for hit in hits if hit),
                "missed": [group[0] for group, hit in zip(groups, hits) if not hit],
                "said_not_done": said,
                "action": action,
            })
            print(json.dumps(rows[-1], ensure_ascii=True), flush=True)
    finally:
        # 每一轮建了各自的会话，逐个删干净。删卡也会带走这个角色的会话，
        # 但先把会话删掉，收尾更明确。
        for scope in (card_id, store.GLOBAL_SCOPE):
            for sid in session_ids:
                path = store.sessions_dir(scope) / (sid + ".jsonl")
                if path.exists():
                    path.unlink()
        state_file = store.state_path(card_id)
        if state_file.exists():
            state_file.unlink()
        store.delete_card(card_id)
    total = sum(row["groups_total"] for row in rows)
    hit = sum(row["groups_hit"] for row in rows)
    return {
        "case": case["label"],
        "reps": len(rows),
        "group_hit": hit,
        "group_total": total,
        "rate": round(hit / total, 3) if total else 0.0,
        "full_hit": sum(
            1 for row in rows
            if row["groups_hit"] == row["groups_total"]
        ),
        "said_not_done": sum(len(row["said_not_done"]) for row in rows),
    }


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="括号指定动作落实率探针")
    parser.add_argument("action", choices=["run"])
    parser.add_argument("--reps", type=int, default=4)
    args = parser.parse_args()
    summaries = []
    for case in CASES:
        summaries.append(run_case(case, max(1, args.reps)))
        print(json.dumps({"summary": summaries[-1]}, ensure_ascii=True), flush=True)
    total_hit = sum(item["group_hit"] for item in summaries)
    total_all = sum(item["group_total"] for item in summaries)
    print(json.dumps({
        "overall": {
            "group_hit": total_hit,
            "group_total": total_all,
            "rate": round(total_hit / total_all, 3) if total_all else 0.0,
            "full_hit": sum(item["full_hit"] for item in summaries),
            "reps": sum(item["reps"] for item in summaries),
            "said_not_done": sum(item["said_not_done"] for item in summaries),
        },
        "cases": summaries,
    }, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
