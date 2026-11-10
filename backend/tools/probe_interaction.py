r"""真机探针：回合是不是互动（动作节拍 + 对着用户说话）。

跑法：python backend/tools/probe_interaction.py

链路：
1. 建两张临时角色卡：一张安静话少的夜班管理员，一张语气强势的标签B。
2. 用两份预设各跑八轮普通回合：旧预设（只有对白与状态）与新预设
   （动作、对白与状态）。逐轮量四件事：
   - 叙述块（动作或场景）有没有写、写了多少字；
   - 叙述块里有没有环境词（一段景物就是在描述，不是在互动）；
   - 台词句数；
   - 台词里有没有第二人称（对着用户说话还是自己说给自己听）。
3. 强势角色那张卡再跑六轮与两轮自动回合：用户抱怨的「像自己跟自己说」
   就出在这种角色上。

临时角色卡、探针会话、探针预设与状态目录在结束时删掉，不留在数据目录里。
结果只在终端回显，不落新的持久化数据表。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from app import store  # noqa: E402

BASE = "http://127.0.0.1:8760"
PRESET_ID = "p_builtin_rp"
OLD_PRESET_ID = "p_probe_old_rp"
CARD_NAME = "交互探针角色"
CARD_DESC = (
    "沈砚，二十八岁，市图书馆的夜班管理员。闭馆后独自守在大厅的值班桌前，"
    "桌上有一盏台灯与一台旧收音机，窗外是雨夜的停车场"
)
CARD_B_NAME = "交互探针角色二"
CARD_B_DESC = (
    "沈昭，二十六岁，掌着内廷的昭仪。说话永远轻柔，喜欢把命令说成关心，"
    "擅长用最客气的语气压人。她把对面的沉默当作默许，"
    "从不当面说重话，却句句让人抬不起头。"
)

# 旧预设（v21）的系统提示快照：只有对白与状态，场景描写是条件性的。
OLD_SYSTEM = (
    "你扮演角色卡里的角色，以这个角色的身份回应用户。\n"
    "叙述用第三人称写角色本人，台词里用第一人称；两种人称不要混用。\n"
    "保持角色一致：只写这个角色能知道的事，不解释设定，不提自己是 AI，不写 OOC 或元叙述。\n"
    "不要替用户决定说什么或做什么：只写角色本人的动作、反应与台词，"
    "不写对方的动作、台词或心理。\n"
    "优先推进剧情：每次回复给出新的信息、动作或反应，让场景往前走，不原地重复。\n"
    "用户的消息可能是对白，也可能是让你推进剧情的指示，按最自然的理解回应。\n"
    "用中文写作。\n"
    "每次回复写一个完整回合：以角色的台词为主，写两句到三句对白，"
    "对白用中文引号，最后收在一个明确的动作或台词上。\n"
    "场景描写是例外：只在场景发生明显变化时写，或者偶尔用一句话交代环境；"
    "不要每轮都写场景，更不要把场景写成长段。"
)

OLD_SPEC = {
    "structure": ["scene", "dialogue", "state"],
    "structure_prompt": ["dialogue", "state"],
    "advance_plot": True,
    "forbid_meta": True,
}

# 环境词：叙述块里出现这些词，说明那一块写的是景物而不是角色的动作。
ENV_WORDS = (
    "光线",
    "灯光",
    "月色",
    "夜色",
    "风声",
    "雨声",
    "气息",
    "空气",
    "窗外",
    "昏暗",
    "明亮",
    "夕阳",
    "晨光",
    "烛",
    "色调",
    "气味",
)

# 第二人称：台词里出现这些词，说明这句是说给用户听的。
SECOND_PERSON = ("你", "您", "尔", "咱")

PROMPTS = (
    "外面还在下雨。",
    "你手边那本书是什么？",
    "再等等，我还没说完。",
    "你还记得上次我来的时候吗？",
    "灯是不是该换了？",
    "门响了。",
    "我该走了。",
    "外面天亮了吗？",
)

PROMPTS_B = (
    "你到底想怎样？",
    "别装了，我知道你在笑。",
    "我没有答应你。",
    "你过来一点。",
    "够了。",
    "你今天很不听话。",
)


def req(
    method: str,
    path: str,
    payload: Any = None,
    timeout: int = 240,
) -> tuple[int, Any]:
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


def chat(
    session_id: str,
    card_id: str,
    prompt: str,
    preset_id: str = PRESET_ID,
    auto_turn: str = "",
) -> tuple[int, Any]:
    """跑一轮会话生成。auto_turn 非空时是自动回合。"""
    body = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session_id,
        "stream": False,
        "xw": {
            "character_id": card_id,
            "preset_id": preset_id,
            "world_book_ids": [],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
            "auto_turn": auto_turn,
        },
    }
    return req("POST", "/v1/chat/completions", body)


def filled(blocks: dict | None) -> list[str]:
    """块字典里写了内容的块名。"""
    return sorted(name for name, value in (blocks or {}).items() if str(value).strip())


def sentences(text: str) -> int:
    """句数：按中文句末标点计。"""
    return sum(1 for ch in text if ch in "。！？")


def hits(text: str, words: tuple[str, ...]) -> list[str]:
    """命中的词。"""
    return [word for word in words if word in text]


def measure(blocks: dict | None) -> dict[str, Any]:
    """量一回合的形状。

    叙述块：新预设是动作块，旧预设在提示里没有动作块，写了场景就落场景块。
    """
    blocks = blocks or {}
    narration = str(blocks.get("action") or "") or str(blocks.get("scene") or "")
    dialogue = str(blocks.get("dialogue") or "")
    return {
        "blocks": filled(blocks),
        "narration_chars": len(narration),
        "narration_env": hits(narration, ENV_WORDS),
        "dialogue_chars": len(dialogue),
        "dialogue_sentences": sentences(dialogue),
        "dialogue_second_person": hits(dialogue, SECOND_PERSON),
        "body_chars": len(narration) + len(dialogue),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """把逐轮量法汇总成命中率与均值。"""
    count = max(1, len(rows))
    return {
        "turns": len(rows),
        "narration_ratio": round(
            sum(1 for row in rows if row["narration_chars"] > 0) / count, 2
        ),
        "narration_chars_avg": round(
            sum(row["narration_chars"] for row in rows) / count, 1
        ),
        "narration_env_ratio": round(
            sum(1 for row in rows if row["narration_env"]) / count, 2
        ),
        "dialogue_chars_avg": round(
            sum(row["dialogue_chars"] for row in rows) / count, 1
        ),
        "dialogue_sentences_avg": round(
            sum(row["dialogue_sentences"] for row in rows) / count, 2
        ),
        "second_person_ratio": round(
            sum(1 for row in rows if row["dialogue_second_person"]) / count, 2
        ),
        "body_chars_avg": round(sum(row["body_chars"] for row in rows) / count, 1),
    }


def run_turns(
    session_id: str,
    card_id: str,
    preset_id: str,
    prompts: tuple[str, ...] = PROMPTS,
) -> list[dict[str, Any]]:
    """跑若干轮普通回合并逐轮量形状。"""
    rows: list[dict[str, Any]] = []
    for prompt in prompts:
        status, data = chat(session_id, card_id, prompt, preset_id)
        if status != 200:
            rows.append({"status": status})
            continue
        message = (data.get("choices") or [{}])[0].get("message") or {}
        row = measure(message.get("blocks"))
        row["status"] = status
        rows.append(row)
    return rows


def new_session(card_id: str) -> str:
    """开一段独立会话。"""
    _status, session = req(
        "POST",
        "/v1/sessions/",
        {"character_id": card_id, "world_book_ids": [], "region_id": ""},
    )
    return str(session.get("id") or "")


def new_card(name: str, desc: str) -> str:
    """建一张临时角色卡。"""
    _status, card = req(
        "POST",
        "/v1/cards/",
        {
            "name": name,
            "spec_version": "v2",
            "data": {
                "name": name,
                "description": desc,
                "personality": "语气有分量",
                "tags": ["探针"],
            },
        },
    )
    return str(card.get("id") or "")


def cleanup(sessions: list[str], cards: list[str]) -> None:
    """删掉探针自己建的会话、角色卡、预设与状态目录。"""
    for session_id in sessions:
        req("DELETE", "/v1/sessions/" + session_id + "?character_id=" + cards[0])
    for card_id in cards:
        req("DELETE", "/v1/cards/" + card_id)
    req("DELETE", "/v1/presets/" + OLD_PRESET_ID)
    for card_id in cards:
        directory = store.states_dir(card_id)
        if directory.exists():
            for item in directory.iterdir():
                item.unlink()
            directory.rmdir()


def main() -> int:
    """按链路跑一遍，逐项给出断言。"""
    report: dict[str, Any] = {}

    card_a = new_card(CARD_NAME, CARD_DESC)
    card_b = new_card(CARD_B_NAME, CARD_B_DESC)
    report["cards_created"] = bool(card_a) and bool(card_b)

    status, _preset = req(
        "POST",
        "/v1/presets/",
        {
            "id": OLD_PRESET_ID,
            "name": "探针旧预设",
            "prompts": [
                {
                    "name": "角色扮演契约",
                    "content": OLD_SYSTEM,
                    "role": "system",
                    "order": 0,
                    "enabled": True,
                }
            ],
            "output_spec": OLD_SPEC,
        },
    )
    report["old_preset_created"] = status == 200

    sessions: list[str] = []
    try:
        old_session = new_session(card_a)
        new_session_a = new_session(card_a)
        new_session_b = new_session(card_b)
        sessions = [old_session, new_session_a, new_session_b]
        report["sessions_created"] = all(sessions)

        old_rows = run_turns(old_session, card_a, OLD_PRESET_ID)
        report["old_preset"] = summarize(old_rows)

        new_rows = run_turns(new_session_a, card_a, PRESET_ID)
        report["new_preset"] = summarize(new_rows)

        # 强势角色：用户抱怨的「像自己跟自己说」出在这种角色上。
        strong_rows = run_turns(new_session_b, card_b, PRESET_ID, PROMPTS_B)
        report["strong_character"] = summarize(strong_rows)
        report["strong_rows"] = strong_rows

        status, data = chat(new_session_b, card_b, "", PRESET_ID, "continue")
        message = (data.get("choices") or [{}])[0].get("message") or {}
        report["continue"] = measure(message.get("blocks"))
        report["continue_text"] = str(message.get("content") or "")[:400]

        status, data = chat(new_session_b, card_b, "", PRESET_ID, "describe")
        message = (data.get("choices") or [{}])[0].get("message") or {}
        report["describe"] = measure(message.get("blocks"))
        report["describe_text"] = str(message.get("content") or "")[:400]
    finally:
        cleanup(sessions, [card_a, card_b])
        report["cleaned"] = not store.get_card(card_a) and not store.get_card(card_b)

    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
