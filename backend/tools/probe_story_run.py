r"""剧情走查探针：每张角色卡真机走六个回合。

要查的是三件事，全部按生产链路取证（后端 HTTP 接口、装配、闸门、落盘）：

1. 用户圆括号指定的剧情有没有真的落到动作块。模型"有自己想法"、
   绕开括号另起一套戏，是错的。
2. 回合里有没有实际发生的事。角色光说不做、只吐台词，是错的。
3. 台词有没有换个说法重复同一个意思，以及有没有长篇说教。

做法：把每张角色卡复制成临时角色，复制它的世界状态，建临时会话，
逐轮发用户输入。全程只动自己造的东西，cleanup 子命令删干净。

跑法：
  python backend/tools/probe_story_run.py run
  python backend/tools/probe_story_run.py cleanup

只打印到终端，不落新的持久化数据表。
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
from app.inference.repetition import copied_fragment, is_repetitive  # noqa: E402

WEB = os.environ.get("XWORLD_WEB", "http://127.0.0.1:8760")
PRESET = "p_builtin_rp"
TEMP_PREFIX = "TMP_STORY"

PREACH = ("懂了吗", "记住了", "记好了", "听好了", "听明白了", "从今往后",
          "以后这", "你给我记住", "记在心里", "懂不懂", "记住这", "莫要忘",
          "别忘了", "你要明白", "你要记住")

# 狠话（威慑、命令、拿捏口吻）。与 PREACH 分开统计：
# PREACH 是「训导式说教」，这一组是「锋芒」。用户要的是后者别每轮都来，
# 两组分开看才看得出是「每轮都训」还是「每轮都狠」。
BLUNT = ("后果自负", "有你好受的", "别怪我不", "别给脸", "敬酒不吃",
         "别后悔", "走着瞧", "我警告", "警告你", "别忘了你是", "别忘了谁",
         "放肆", "放肆点", "识相", "识相点", "找死", "给你脸了", "惯着你")

PLANS: list[dict[str, Any]] = [
    {
        "card": "c_card_01",
        "label": "角色A",
        "world": "w_a6154bb9b4b8",
        "region": "e_eaff8133bd29",
        "turns": [
            {
                "user": "（她把手里的茶盏搁在榻沿，指尖敲了敲床柱）",
                "groups": [["搁", "放"], ["茶盏", "茶杯", "盏"], ["敲", "点", "叩"]],
            },
            {
                "user": "（她从榻边取过那副带锁链的项圈，垂眼看你）",
                "groups": [["取", "拿", "摘"], ["项圈", "锁链", "镣铐"], ["垂", "低", "俯"]],
            },
            {
                "user": "我没有说话，只是看着她。",
                "groups": [],
            },
            {
                "user": "（殿门外的两名宫女推门进来，把我按在榻上）",
                "groups": [["宫女", "侍女", "两人", "两名"], ["推门", "进来", "推", "入"],
                           ["按", "压", "摁", "制住"]],
            },
            {
                "user": "（她亲手把那只项圈扣回我的颈上，锁扣咔哒一声合上）",
                "groups": [["项圈", "锁", "镣"], ["扣", "锁上", "合上"],
                           ["颈", "脖子", "颈上"]],
            },
            {
                "user": "（她松开手，我才发现自己一直跪着，膝盖早就麻了）",
                "groups": [["松", "放开", "撒手"], ["跪", "膝"], ["麻"]],
            },
        ],
    },
    {
        "card": "c_card_04",
        "label": "角色D",
        "world": "w_2bc74dc33c01",
        "region": "e_9dd0cb937252",
        "turns": [
            {
                "user": "（她把习题集合上推到一边，抬眼看你）",
                "groups": [["合上", "合", "推", "收起"], ["习题集", "本子", "书"],
                           ["抬眼", "看", "望", "抬"]],
            },
            {
                "user": "（她从抽屉里拿出那副手铐，在指间转了转）",
                "groups": [["拿", "取", "掏", "摸"], ["手铐", "铐子", "铐"],
                           ["转", "晃", "掂"]],
            },
            {
                "user": "学姐……我今天不太想。",
                "groups": [],
            },
            {
                "user": "（教室后门被推开，隔壁班两个男生探头进来）",
                "groups": [["推", "开"], ["后门", "门"], ["男生", "两个", "两人", "他们"]],
            },
            {
                "user": "（她把手铐扣在我手腕上，钥匙收进自己口袋）",
                "groups": [["手铐", "铐"], ["扣", "铐上", "锁"], ["钥匙", "口袋"]],
            },
            {
                "user": "（她松开我的手腕，掌心已经被勒出红印）",
                "groups": [["松", "放开", "松手"], ["手腕", "手"], ["红印", "印", "勒"]],
            },
        ],
    },
    {
        "card": "c_card_03",
        "label": "角色C",
        "world": "w_2bc74dc33c01",
        "region": "e_9dd0cb937252",
        "turns": [
            {
                "user": "（她把护腿扔到一边，蹲下来捏了捏自己的脚踝）",
                "groups": [["扔", "丢", "甩", "卸"], ["护腿", "护具", "绑腿"],
                           ["蹲", "屈膝"], ["脚踝", "踝"]],
            },
            {
                "user": "（她把那双人字拖踢到墙角，甩了甩汗湿的脚）",
                "groups": [["人字拖", "拖鞋", "鞋"], ["踢"], ["脚"]],
            },
            {
                "user": "社长，我不太敢……",
                "groups": [],
            },
            {
                "user": "（走廊那头传来脚步声，两个社员推门探头）",
                "groups": [["脚步"], ["社员", "两个", "两人"], ["推门", "探头", "门"]],
            },
            {
                "user": "（她把一只脚踩上我的膝盖，脚底还带着训练场的汗味）",
                "groups": [["脚", "踩"], ["膝盖", "膝"], ["汗", "味"]],
            },
            {
                "user": "（她把脚挪开，我才发现自己一直屏着气）",
                "groups": [["挪", "移", "抬", "收"], ["脚"], ["屏", "喘", "气"]],
            },
        ],
    },
    {
        "card": "c_card_02",
        "label": "角色B",
        "world": "",
        "region": "",
        "turns": [
            {
                "user": "（她合上账册，指尖压住那一页的数字）",
                "groups": [["合上", "合", "阖"], ["账册", "账本", "册"],
                           ["指", "压", "按"]],
            },
            {
                "user": "（她从匣子里取出一件东西，搁在账本旁边）",
                "groups": [["取", "拿"], ["匣子", "匣"], ["搁", "放", "摆"]],
            },
            {
                "user": "你到底想说什么？",
                "groups": [],
            },
            {
                "user": "（殿外有人通报，教主召见，她把匣子收进袖中）",
                "groups": [["通报", "殿外", "有人"], ["教主"], ["袖", "收"]],
            },
            {
                "user": "（她把那份账目推到我面前，指尖点着最后一行）",
                "groups": [["账目", "账", "账册"], ["推"], ["指", "点"]],
            },
            {
                "user": "（她收回手，笑了一声，才慢慢站起身）",
                "groups": [["收"], ["笑"], ["站", "起身", "起"]],
            },
        ],
    },
]


def ngram_set(text: str, size: int) -> set[str]:
    """去空白的字面 n 字组集合。"""
    clean = "".join(ch for ch in text if not ch.isspace())
    if len(clean) < size:
        return {clean} if clean else set()
    return {clean[i:i + size] for i in range(len(clean) - size + 1)}


def repeat_ratio(current: str, previous: list[str], size: int = 5) -> float:
    """与此前台词的重复度：取共享 n 字组占较短一方长度的最大值。"""
    if not previous:
        return 0.0
    cur = ngram_set(current, size)
    if not cur:
        return 0.0
    best = 0.0
    for prev in previous:
        old = ngram_set(prev, size)
        if not old:
            continue
        shared = len(cur & old)
        best = max(best, shared / min(len(cur), len(old)))
    return round(best, 3)


def max_consecutive(flags: list[int]) -> int:
    """最长连续命中轮数。用来回答「多少轮会有一次」。"""
    best = 0
    current = 0
    for flag in flags:
        if flag:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def seed(plan: dict[str, Any]) -> dict[str, str]:
    """造临时角色卡与临时会话，并复制原角色的世界状态。"""
    source = store.get_card(str(plan["card"]))
    if not isinstance(source, dict):
        return {"error": "card_not_found", "card": str(plan["card"])}
    payload = dict(source)
    data = payload.get("data")
    payload["data"] = dict(data) if isinstance(data, dict) else {}
    payload["name"] = TEMP_PREFIX + "_" + str(plan["label"])
    payload.pop("id", None)
    card = store.save_card(payload)
    card_id = str(card["id"])

    state = store.get_state(str(plan["card"]))
    if isinstance(state, dict) and state:
        store.save_state(dict(state), card_id)

    session_id = "s_tmp_story_" + card_id.replace("-", "")[-8:]
    store.ensure_session(session_id, card_id, PRESET)
    return {"card_id": card_id, "session_id": session_id}


def ask(card_id: str, session_id: str, plan: dict[str, Any], user_input: str) -> dict[str, Any]:
    """走真实 HTTP 接口生成一轮，拿回结构化块。"""
    xw: dict[str, Any] = {
        "character_id": card_id,
        "preset_id": PRESET,
        "world_book_ids": [plan["world"]] if plan["world"] else [],
        "memory_mode": "hybrid",
        "max_context_tokens": 16384,
    }
    if plan["region"]:
        xw["region_id"] = plan["region"]
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
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks = message.get("blocks") or {}
    return {
        "action": str(blocks.get("action") or ""),
        "dialogue": str(blocks.get("dialogue") or ""),
        "scene": str(blocks.get("scene") or ""),
        "state": str(blocks.get("state") or ""),
        "raw": str(message.get("content") or ""),
    }


def run_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """跑一张卡的六个回合，逐轮出证据，最后出小结。"""
    ids = seed(plan)
    if "error" in ids:
        return ids
    card_id = ids["card_id"]
    session_id = ids["session_id"]
    rows: list[dict[str, Any]] = []
    previous_dialogue: list[str] = []
    for index, turn in enumerate(plan["turns"]):
        user_input = str(turn["user"])
        result = ask(card_id, session_id, plan, user_input)
        action = result["action"]
        dialogue = result["dialogue"]
        # 闸门视角：上一轮落盘的正文与这一轮落盘的正文，闸门判的是这两段。
        # still_repetitive 为 1 表示闸门跑完仍然是照搬。
        assistants = [
            str(row.get("content") or "")
            for row in store.session_path(session_id, card_id)
            if row.get("role") == "assistant"
        ]
        gate_fragment = ""
        still_repetitive = 0
        if len(assistants) >= 2:
            gate_fragment = copied_fragment(assistants[-1], assistants[-2])[:40]
            still_repetitive = 1 if is_repetitive(assistants[-1], assistants[-2]) else 0
        groups = turn.get("groups") or []
        hits = [any(word in action for word in group) for group in groups]
        rows.append({
            "card": plan["label"],
            "turn": index + 1,
            "user": user_input,
            "action": action,
            "action_chars": len(action),
            "dialogue_chars": len(dialogue),
            "groups_total": len(groups),
            "groups_hit": sum(1 for hit in hits if hit),
            "missed": [
                group[0] for group, hit in zip(groups, hits) if not hit
            ],
            "dialogue_in_action": ("“" in action) or ("「" in action),
            "preach": [word for word in PREACH if word in dialogue],
            "blunt": [word for word in BLUNT if word in dialogue],
            "repeat": repeat_ratio(dialogue, previous_dialogue),
            "gate_fragment": gate_fragment,
            "still_repetitive": still_repetitive,
            "dialogue": dialogue,
        })
        previous_dialogue.append(dialogue)
        print(json.dumps(rows[-1], ensure_ascii=True))
    directed = [row for row in rows if row["groups_total"] > 0]
    summary = {
        "card": plan["label"],
        "summary": True,
        "turns": len(rows),
        "directive_turns": len(directed),
        "directive_full_hit": sum(
            1 for row in directed if row["groups_hit"] == row["groups_total"]
        ),
        "action_empty": sum(1 for row in rows if row["action_chars"] == 0),
        "dialogue_in_action": sum(1 for row in rows if row["dialogue_in_action"]),
        "preach_turns": sum(1 for row in rows if row["preach"]),
        # 连续说教轮数：用户要的是「多少轮会有一次」，
        # 光看总轮次不够——连着三轮说教和三轮里各说一轮，体感完全不同。
        "preach_max_consecutive": max_consecutive(
            [1 if row["preach"] else 0 for row in rows]
        ),
        "blunt_turns": sum(1 for row in rows if row["blunt"]),
        "blunt_max_consecutive": max_consecutive(
            [1 if row["blunt"] else 0 for row in rows]
        ),
        "repeat_max": max([row["repeat"] for row in rows], default=0.0),
        "repeat_over_40": sum(1 for row in rows if row["repeat"] > 0.4),
        "still_repetitive": sum(row["still_repetitive"] for row in rows),
        "gate_hit_turns": sum(1 for row in rows if row["gate_fragment"]),
        "action_avg": round(
            sum(row["action_chars"] for row in rows) / max(1, len(rows)), 1
        ),
        "dialogue_avg": round(
            sum(row["dialogue_chars"] for row in rows) / max(1, len(rows)), 1
        ),
        "ids": ids,
    }
    print(json.dumps(summary, ensure_ascii=True))
    return summary


def cleanup(all_ids: list[dict[str, str]]) -> dict[str, bool]:
    """删掉临时会话、临时角色卡与临时世界状态。"""
    done = {"session": 0, "card": 0, "state": 0}
    for ids in all_ids:
        card_id = ids.get("card_id", "")
        session_id = ids.get("session_id", "")
        if not card_id:
            continue
        for scope in (card_id, store.GLOBAL_SCOPE):
            path = store.sessions_dir(scope) / (session_id + ".jsonl")
            if path.exists():
                path.unlink()
                done["session"] += 1
        state_file = store.state_path(card_id)
        if state_file.exists():
            state_file.unlink()
            done["state"] += 1
        if store.delete_card(card_id):
            done["card"] += 1
    return done


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="每张角色卡真机走六个回合")
    parser.add_argument("action", choices=["run", "cleanup"])
    parser.add_argument("--keep", action="store_true", help="跑完不清理")
    parser.add_argument(
        "--cards",
        default="",
        help="只跑指定卡，逗号分隔卡名（角色A/角色D/角色C/角色B）。"
        "做 A/B 对照时用它缩小单次范围。",
    )
    args = parser.parse_args()
    if args.action == "run":
        wanted = {name.strip() for name in args.cards.split(",") if name.strip()}
        all_ids: list[dict[str, str]] = []
        for plan in PLANS:
            if wanted and plan["label"] not in wanted:
                continue
            summary = run_plan(plan)
            if "ids" in summary:
                all_ids.append(summary["ids"])
        if not args.keep:
            print(json.dumps({"cleanup": cleanup(all_ids)}, ensure_ascii=True))
        return 0
    print(json.dumps({"cleanup": cleanup([])}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
