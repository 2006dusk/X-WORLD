# -*- coding: utf-8 -*-
r"""四个主角色各引入一个新配角，长上下文下量整条链路。

量这些事：
1. 回合末有没有自动落成配角卡（不用人点确认），来源判对没有；
2. AI 补全有没有把三层设定与此刻状态填上，待完善标记有没有摘掉；
3. 关系档位判得对不对（拿会话流水重算一遍，和卡上写的比）；
4. 配角段在「本轮提到名字」时进不进提示词，token 多少；
5. 配角段在「名字落在检索窗口之外」时还在不在（长上下文的关键）；
6. 配角段的位置（在角色段之前）、边界句、类型标签有没有漏进去；
7. 上下文吃紧时被裁剪，配角段会不会跟着没（裁剪顺序）；
8. 模型回话里有没有用上卡里的数据；
9. 收尾零残留：配角卡、临时会话、状态与记忆文件都还原。

顺序上有个要点：窗口内的测量必须在「末尾点名轮」之前做。末尾那轮点了名，
名字就留在最近的历史里，窗口测量会全被顶成「注入」，量不出窗口边界。

所以流程是：介绍 → 点名若干轮 → 填充轮（不点名）→ 窗口测量（装配）→
末尾点名追问（真机跑一轮，看模型有没有用上卡里的数据）→ 收尾。

全程用临时会话与临时配角名，收尾只删自己建的东西，并把探针期间被改动的
数据文件按字节还原。

用法：
    python backend\tools\probe_v2_long_context.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config, store  # noqa: E402
from app.assembly.pipeline import (  # noqa: E402
    CHARACTER_LABEL,
    SUPPORTING_BOUNDARY,
    SUPPORTING_HEADER,
    TAG_CONSTRAINT,
    assemble,
)
from app.cards.auto_intro import _QUOTE, _turn_stats, relation_level  # noqa: E402
from app.contracts import ChatMessage, ChatRequest, XwOptions  # noqa: E402

from probe_supporting_common import (  # noqa: E402
    delete_card,
    find_supporting_card,
    http,
    supporting_ids,
    wait_for_fill,
)

PRESET = "p_builtin_rp"
KEEP_TURNS_LONG = 20
KEEP_TURNS_NEAR = 4
TIGHT_BUDGET = 2200

# 探针会动到的目录。收尾按字节还原。
SNAPSHOT_DIRS = ("cards", "world_books", "state", "sessions", "memories", "entities")

# 四个主角色各带一个新配角。配角名用常见姓开头，避免被词组过滤掉。
# named 的条数不同，认识程度就不同：提得少是相逢，提得多是相熟。
CASES: list[dict[str, Any]] = [
    {
        "cid": "c_card_01",
        "name": "角色A",
        "npc": "角色E",
        "book": "w_a6154bb9b4b8",
        "region": "e_eaff8133bd29",
        # 「求见」式的介绍引不进配角：不是自报写法，名字也只出现一次。
        # 这里用自报写法，保证四组可比；这条结论另记在 intro_note 里。
        "intro": (
            "（殿外有人通传，说有位自称角色E的人求见，说带了要紧东西，"
            "要亲手交到你手上，不肯说替谁来的）你怎么处置？"
        ),
        "intro_note": "求见式介绍（无自报写法、名字只出现一次）建不出配角卡",
        "named": [],
        "filler": [
            "（殿外候着的人还在，你把手里的奏折翻开看）",
            "（你把手边那盏灯挪了挪，重新看那本折子）",
            "（外面传来脚步声，你没有抬头，继续看）",
            "（你把折子合上，问宫人外头还有谁候着）",
            "（你想起殿里还有半盏冷茶，叫人换一盏）",
            "（你站在窗前看了一眼夜色，又回到案前）",
            "（你揉了揉眉心，把笔搁下）",
            "（远处更鼓响了一声，殿里更静）",
            "（你把袖口理平，重新提起笔）",
        ],
    },
    {
        "cid": "c_card_03",
        "name": "角色C",
        "npc": "角色F",
        "book": "w_2bc74dc33c01",
        "region": "e_e50bc1fcb19b",
        "intro": (
            "（馆门口有人喊你的名字，说他叫角色F，等你半天了，"
            "说有话要当面说）你怎么办？"
        ),
        "named": [
            "（你转向角色F）你到底要说什么。",
        ],
        "filler": [
            "（你把护具重新绑紧，踢了两下靶子）",
            "（你坐在垫子边上喝水，看了一眼门口）",
            "（你起身活动了一下肩膀，馆里没人说话）",
            "（你把散开的头发重新扎起来）",
            "（你靠着墙滑坐下来，看手机上的时间）",
            "（你把水壶盖拧上，馆里的灯闪了一下）",
            "（你轻轻跳了几步热身，脚步声在馆里回响）",
            "（你抹了把汗，喘匀了再说）",
            "（你看了眼门口，把鞋带重新系了一遍）",
        ],
    },
    {
        "cid": "c_card_02",
        "name": "角色B",
        "npc": "角色G",
        "book": "w_ab4d5cf8aa14",
        "region": "e_e32566ecaeca",
        "intro": (
            "（账房外有人敲门，说他叫角色G，替人送一笔账来，"
            "却不肯说替谁送的）你怎么应对？"
        ),
        "named": [
            "（你让角色G把账册放下）这笔是谁的。",
            "（你又问角色G一次）你替谁来的，说。",
            "（你把账册翻到最后一页，推到角色G面前）",
        ],
        "filler": [
            "（你把算筹拨正，重新核一遍那几笔数）",
            "（你把砚台挪开，把旧纸压平）",
            "（外头炭火响了一声，你没有抬头）",
            "（你把一笔数划掉，重新算了一遍）",
            "（你把那本册子合上，压在镇纸下）",
            "（你起身把窗推开一条缝透气）",
            "（你在册子空白处写了一行小字）",
            "（你吹了吹墨迹，把笔搁回砚台边）",
            "（你把散着的算筹一根根收拢）",
        ],
    },
    {
        "cid": "c_card_04",
        "name": "角色D",
        "npc": "角色H",
        "book": "w_2bc74dc33c01",
        "region": "e_9dd0cb937252",
        "intro": (
            "（晚自习散了，教室里还坐着一个人，他说他叫角色H，"
            "等你很久了，说有道题想问你）你怎么回答？"
        ),
        "named": [
            "（你看向角色H）哪道题。",
            "（你又问角色H一次）你怎么还没走。",
        ],
        "filler": [
            "（你把书包里的笔盒收好，抬头看了一眼窗外）",
            "（你翻开练习册，把最后一道题看了一遍）",
            "（走廊的灯灭了一盏，教室里更暗了）",
            "（你把椅子往桌边推了推，收拾东西）",
            "（窗外有人在楼下喊你的名字，你应了一声）",
            "（你合上练习册，笔夹在中间）",
            "（你站起来，把校服外套搭在椅背上）",
            "（你回头看了一眼空着的座位）",
            "（你背着书包走到门口，又停了一下）",
        ],
    },
]


def _chat(session: str, case: dict[str, Any], text: str) -> dict[str, Any]:
    """走真实后端跑一轮，返回回复与用量。"""
    payload = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": text}],
        "session": session,
        "stream": False,
        "xw": {
            "character_id": case["cid"],
            "preset_id": PRESET,
            "world_book_ids": [case["book"]],
            "region_id": case["region"],
            "memory_mode": "hybrid",
            "max_context_tokens": 16384,
        },
    }
    body = http("POST", "/v1/chat/completions", payload, timeout=600.0)
    usage = body.get("usage") or {}
    # 非流式返回是 ChatResponse：正文在 choices[0].message.content。
    # 顶层没有 message 键，早先取 body["message"] 恒为空。
    choices = body.get("choices") or []
    content = ""
    if choices:
        content = str((choices[0] or {}).get("message", {}).get("content") or "")
    return {
        "content": content,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
    }


def _probe(session: str, case: dict[str, Any], user_input: str, budget: int) -> dict[str, Any]:
    """进程内跑一次装配，量配角段。

    返回：配角段 token、段序（相对角色段的位置）、边界句、有没有漏进
    类型标签、配角段正文、总 token 与是否被裁剪。
    """
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_input)],
        session=session,
        stream=False,
        xw=XwOptions(
            character_id=case["cid"],
            preset_id=PRESET,
            world_book_ids=[case["book"]],
            region_id=case["region"],
            memory_mode="hybrid",
            max_context_tokens=budget,
        ),
    )
    ctx = assemble(request)
    segments = {str(seg.name): int(seg.tokens) for seg in ctx.report.segments}
    texts = [str(msg.content or "") for msg in ctx.messages]

    # 配角段与角色段被合并进同一条 system 消息：位置要在同一段文本里比。
    combined = ""
    for text in texts:
        if SUPPORTING_HEADER in text or (CHARACTER_LABEL + case["name"]) in text:
            combined = text
            break
    marker = SUPPORTING_HEADER
    char_marker = CHARACTER_LABEL + case["name"]
    sup_at = combined.find(marker)
    char_at = combined.find(char_marker)
    # 配角段单独截出来：标签泄漏只看这段，不看角色段（角色段本来就有标签）。
    if sup_at >= 0:
        end = char_at if char_at > sup_at else len(combined)
        supporting_text = combined[sup_at:end]
    else:
        supporting_text = ""
    return {
        "supporting_tokens": int(segments.get("supporting") or 0),
        "history_tokens": int(segments.get("history") or 0),
        "total_tokens": sum(int(seg.tokens) for seg in ctx.report.segments),
        "truncated": bool(ctx.report.truncated),
        "supporting_at": sup_at,
        "character_at": char_at,
        "before_character": sup_at >= 0 and (char_at < 0 or sup_at < char_at),
        "has_boundary": SUPPORTING_BOUNDARY in supporting_text,
        "has_tag_leak": TAG_CONSTRAINT in supporting_text,
        "names_in_segment": [case["npc"]] if case["npc"] in supporting_text else [],
        "supporting_text": supporting_text,
    }


def _card_terms(data: dict[str, Any]) -> list[str]:
    """卡上最容易被认出来的几样东西：随身物品、所在位置、身份开头。"""
    terms: list[str] = []
    state = data.get("state")
    if isinstance(state, dict):
        items = state.get("items")
        if isinstance(items, list):
            for item in items:
                text = str(item).strip()
                if len(text) >= 3:
                    terms.append(text)
        location = str(state.get("location") or "").strip()
        if location:
            # 位置是一整句，整句命中不现实：取第一小句的前几个字。
            head = location.split("，")[0].strip()
            if len(head) >= 3:
                terms.append(head)
    identity = str(data.get("identity") or "").strip()
    if len(identity) >= 6:
        terms.append(identity[:6])
    return terms


def _hits(terms: list[str], text: str) -> list[str]:
    return [term for term in terms if term and term in text]


def _measure_relation(
    session: str,
    case: dict[str, Any],
    card: dict[str, Any],
) -> dict[str, Any]:
    """拿会话流水重算关系档，和卡上写的比。"""
    messages = store.get_messages(session, case["cid"])
    turns, dialogue = _turn_stats(str(case["npc"]), messages)
    expected = relation_level(turns, dialogue)
    actual = str((card.get("data") or {}).get("relation") or "")
    npc = str(case["npc"])
    # 用户提名轮数就是 turns：_turn_stats 数的就是用户消息里提到他的次数，
    # 这里不再另数一遍。剩下两种口径是模型回复里的提及方式，拿来对照。
    rounds_narration = 0
    rounds_speech = 0
    for item in messages:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role != "assistant" or npc not in content:
            continue
        if any(npc in part for part in _QUOTE.findall(content)):
            rounds_speech += 1
        else:
            rounds_narration += 1
    return {
        "turns": turns,
        "dialogue": dialogue,
        "expected": expected,
        "actual": actual,
        "consistent": expected == actual,
        "rounds_narration": rounds_narration,
        "rounds_speech": rounds_speech,
    }


def _snapshot(root: Path) -> dict[str, bytes]:
    """目录下每个文件的内容。收尾按字节还原。"""
    result: dict[str, bytes] = {}
    for name in SNAPSHOT_DIRS:
        base = root / name
        if not base.exists():
            continue
        for item in sorted(base.rglob("*")):
            if item.is_file():
                key = name + "/" + str(item.relative_to(base)).replace("\\", "/")
                result[key] = item.read_bytes()
    return result


def _restore(root: Path, snapshot: dict[str, bytes]) -> list[str]:
    """把数据目录还原成快照的样子：多出来的删掉，改过的写回去。"""
    changed: list[str] = []
    for name in SNAPSHOT_DIRS:
        base = root / name
        if not base.exists():
            continue
        for item in sorted(base.rglob("*"), reverse=True):
            if item.is_file():
                key = name + "/" + str(item.relative_to(base)).replace("\\", "/")
                if key not in snapshot:
                    item.unlink()
                    changed.append("removed " + key)
                elif item.read_bytes() != snapshot[key]:
                    item.write_bytes(snapshot[key])
                    changed.append("restored " + key)
    for key, blob in snapshot.items():
        target = root / key
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            changed.append("restored " + key)
    return changed


def _run_case(case: dict[str, Any], before_ids: set[str]) -> dict[str, Any]:
    """跑一个角色：介绍、点名、填充轮、窗口测量、末尾点名追问。"""
    session = "s_tmp_v2lc_" + str(case["cid"])
    out: dict[str, Any] = {"name": case["name"], "npc": case["npc"], "turns": []}
    if case.get("intro_note"):
        out["intro_note"] = str(case["intro_note"])
    npc = str(case["npc"])
    replies: list[str] = []
    card_id = ""
    terms: list[str] = []
    try:
        # 介绍轮 + 点名轮
        plan = [case["intro"]] + list(case["named"])
        broke = False
        for index, text in enumerate(plan):
            reply = _chat(session, case, text)
            replies.append(reply["content"])
            row: dict[str, Any] = {
                "index": index,
                "reply_len": len(reply["content"]),
                "prompt_tokens": reply["prompt_tokens"],
                "npc_in_reply": npc in reply["content"],
            }
            if index == 0:
                card = find_supporting_card(npc)
                row["auto_created"] = card is not None
                if card is None:
                    out["turns"].append(row)
                    out["error"] = "没有自动建出配角卡"
                    broke = True
                    break
                card_id = str(card.get("id") or "")
                out["card_id"] = card_id
                filled = wait_for_fill(card_id)
                row["fill_finished"] = filled is not None
                data = (filled or card).get("data") or {}
                row["fill_error"] = str(data.get("fill_error") or "")
                row["pending_after_fill"] = bool(data.get("pending"))
                row["identity_len"] = len(str(data.get("identity") or ""))
                row["behavior_len"] = len(str(data.get("behavior") or ""))
                row["speech_len"] = len(str(data.get("speech") or ""))
                state = data.get("state") or {}
                row["state_location"] = str(state.get("location") or "")
                row["state_items"] = len(state.get("items") or [])
                row["introduced_by"] = str(data.get("introduced_by") or "")
                terms = _card_terms(data)
            out["turns"].append(row)
        if broke:
            return out

        # 填充轮：不点名。跑完名字就落在检索窗口之外了。
        for index, text in enumerate(case["filler"]):
            reply = _chat(session, case, text)
            replies.append(reply["content"])
            out["turns"].append(
                {
                    "index": len(plan) + index,
                    "reply_len": len(reply["content"]),
                    "prompt_tokens": reply["prompt_tokens"],
                    "npc_in_reply": npc in reply["content"],
                    "filler": True,
                }
            )

        card = find_supporting_card(npc)
        if card is not None:
            data = card.get("data") or {}
            out["card_data"] = {
                "role": str(data.get("role") or ""),
                "pending": bool(data.get("pending")),
                "relation": str(data.get("relation") or ""),
                "introduced_by": str(data.get("introduced_by") or ""),
                "identity_len": len(str(data.get("identity") or "")),
                "behavior_len": len(str(data.get("behavior") or "")),
                "speech_len": len(str(data.get("speech") or "")),
                "state": data.get("state") or {},
            }
            out["relation"] = _measure_relation(session, case, card)
            terms = terms or _card_terms(data)
            out["terms"] = terms

        # 窗口测量：必须在末尾点名轮之前做。
        named_probe = "（你又想起" + npc + "）"
        unnamed_probe = "（你又想起刚才门口那个人）"

        store.set_setting("history_policy", {"keep_turns": KEEP_TURNS_NEAR})
        out["probe_near_unnamed"] = _probe(session, case, unnamed_probe, 16384)
        out["probe_near_named"] = _probe(session, case, named_probe, 16384)

        store.set_setting("history_policy", {"keep_turns": KEEP_TURNS_LONG})
        out["probe_long_unnamed"] = _probe(session, case, unnamed_probe, 16384)
        out["probe_long_named"] = _probe(session, case, named_probe, 16384)

        out["probe_tight_budget"] = _probe(session, case, named_probe, TIGHT_BUDGET)

        # 末尾点名追问：真机跑一轮，看模型有没有用上卡里的数据。
        recall = (
            "（过了一会儿，你忽然想起" + npc + "）他当时带着什么、站在哪儿，"
            "你还记得吗？"
        )
        reply = _chat(session, case, recall)
        replies.append(reply["content"])
        out["recall"] = {
            "reply_len": len(reply["content"]),
            "prompt_tokens": reply["prompt_tokens"],
            "npc_named": npc in reply["content"],
            "terms_in_reply": _hits(terms, reply["content"]),
            "reply": reply["content"],
        }
        all_replies = "\n".join(replies)
        out["card_terms_in_replies"] = _hits(terms, all_replies)
        out["npc_in_replies"] = sum(1 for text in replies if npc in text)
        out["reply_head"] = replies[0][:200] if replies else ""
    finally:
        # 探针期间自动建出来的其他配角卡一并删掉：它们落在全局卡库里，
        # 留着就是污染用户的卡。
        extra = sorted(supporting_ids() - before_ids - {card_id} - {""})
        out["auto_created_extra"] = extra
        for target in [card_id, *extra]:
            if target:
                delete_card(target)
    return out


def main() -> None:
    report: dict[str, Any] = {"cases": []}
    root = config.data_root()
    original_policy = store.get_setting("history_policy")
    original_settings = store.read_json(store.settings_path(), {})
    if not isinstance(original_settings, dict):
        original_settings = {}
    snapshot = _snapshot(root)
    before_ids = supporting_ids()
    try:
        for case in CASES:
            # 对话本身按长窗口跑，提示词里历史是二十轮
            store.set_setting("history_policy", {"keep_turns": KEEP_TURNS_LONG})
            result = _run_case(case, before_ids)
            report["cases"].append(result)
            print(json.dumps(result, ensure_ascii=True, indent=1))
    finally:
        # settings 是跨进程写的：探针改 history_policy 时可能把后端写的
        # 别的键抹掉。收尾按原始内容合回去，只留 history_policy 的原值。
        current = store.read_json(store.settings_path(), {})
        if not isinstance(current, dict):
            current = {}
        merged = dict(current)
        for key, value in original_settings.items():
            if key not in merged:
                merged[key] = value
        if isinstance(original_policy, dict):
            merged["history_policy"] = original_policy
        else:
            merged.pop("history_policy", None)
        store.write_json(store.settings_path(), merged)
        report["settings_policy"] = store.get_setting("history_policy")
        report["settings_keys_kept"] = sorted(
            key for key in original_settings if key in merged
        )
        report["cleanup"] = {"files": _restore(root, snapshot)}
        for folder in sorted((root / "sessions").glob("*")):
            if folder.is_dir() and not any(folder.iterdir()):
                shutil.rmtree(folder, ignore_errors=True)
        report["cards_left"] = [
            str(item.get("name") or "")
            for item in http("GET", "/v1/cards/", params={"role": "supporting"}).get("items", [])
        ]
        report["sessions_left"] = [
            str(item.get("id") or "")
            for item in http("GET", "/v1/sessions/").get("items", [])
        ]
        out_path = ROOT / "backend" / "tmp" / "probe_v2_long_context.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=1), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "cleanup": report["cleanup"],
                    "cards_left": report["cards_left"],
                    "sessions_left": report["sessions_left"],
                    "settings_policy": report["settings_policy"],
                },
                ensure_ascii=True,
            )
        )


if __name__ == "__main__":
    main()
