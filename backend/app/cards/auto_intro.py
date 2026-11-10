"""对话中引入的角色：自动建卡。

抽到的名字立刻落成一张配角卡（role=supporting），并记下三件事：
他是谁带出来的（用户 / 模型）、跟主角什么程度（相逢 / 相识 / 相熟），
以及「待完善」标记——卡里的三层设定与状态由 AI 补全，补全之前是空的。

空卡不进 supporting 段：空设定进提示词就是噪音，模型会照着空白编。
待完善的卡由卡片页的待完善清单兜着，用户可以补信息、改关系档或删掉。
"""

from __future__ import annotations

import re
from typing import Any

from app import store
from app.cards import get_card, list_cards, save_card
from app.cards.spec import (
    INTRO_SOURCE_AI,
    INTRO_SOURCE_USER,
    RELATION_DEFAULT,
    RELATION_LEVELS,
    ROLE_SUPPORTING,
    normalize_intro_source,
    normalize_role,
)
from app.memory.extract import (
    extract_character_names,
    is_person_name_like,
    is_self_introduced,
)

# 对白引号：他有没有开口说过话。
_QUOTE = re.compile(r"[“「\"]([^”」\"]{1,200})[”」\"]")

# 关系档位的次序，只升不降。
_LEVEL_ORDER = {level: index for index, level in enumerate(RELATION_LEVELS)}


def relation_level(turns: int, has_dialogue: bool) -> str:
    """按用户与他打交道的轮数、与他是否开过口，判定关系程度。

    轮数是**用户提他名字的次数**，不是模型回复里出现他名字的次数。
    模型一旦被注入配角段，每轮都会提他；按模型输出计数的话档位会被
    自己的输出推着走，永远是「相熟」，也分不出认识程度。

    一轮只是相逢；两轮是相识；三轮以上是相熟。
    他开口说过话的，一轮也算相识——被记住的不只是名字。
    """
    if turns >= 3:
        return RELATION_LEVELS[2]
    if turns == 2:
        return RELATION_LEVELS[1]
    return RELATION_LEVELS[1] if has_dialogue else RELATION_DEFAULT


def _turn_stats(name: str, messages: list[dict[str, Any]]) -> tuple[int, bool]:
    """数用户与他打了几轮交道，以及他有没有开口说过话。

    轮数数的是**用户消息里提到他的次数**，不是模型回复里提到他的次数。
    这一点是实测逼出来的：配角段一旦注入，模型每轮都会提他，档位于是
    被自己的输出推着走——四组长上下文实测里，用户只提名 1 到 4 轮，
    档位却全部判成「相熟」。更糟的是这是个自增强闭环：注入 → 模型多提
    → 档位升高 → 继续注入，档位成了注入量的函数，而不是认识程度的函数。

    用户提他几次，才是他与主角认识多深的直接表达。这一项不随模型输出
    变化，档位才是稳定的。

    开口说过话仍是信号：他不是个名字，是个人。但它是二元的，不累加，
    不会形成闭环。
    """
    turns = 0
    dialogue = False
    for item in messages:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if not content:
            continue
        if role == "user":
            if name in content:
                turns += 1
            continue
        if role != "assistant" or dialogue:
            continue
        for quote in _QUOTE.findall(content):
            if name in quote:
                dialogue = True
                break
    return turns, dialogue


def _known_names() -> set[str]:
    """卡片库里已有的角色名。"""
    return {str(item.get("name") or "") for item in list_cards()}


def collect_new_characters(
    character_id: str,
    session_id: str,
    scope: str,
    user_text: str,
    assistant_text: str,
    history: list[str] | None = None,
) -> list[dict[str, Any]]:
    """回合末抽新人物，自动落成配角卡。

    返回新建的卡。已有的同名角色不重复建；名字只出现一次、又不是自报
    写法的，多半是叙述句里切出来的词组，不建卡；不像人名的词组
    （「怎么知」「看完再」）直接丢掉。

    来源判定：名字先出现在用户输入里就是用户引入，先出现在模型回复里
    就是模型引入；两边都有时以用户为先。
    """
    if not character_id or not session_id:
        return []
    known = _known_names()
    blob = "\n".join(
        [text for text in [user_text, assistant_text, *(history or [])] if text and text.strip()]
    )
    if not blob.strip():
        return []

    messages = store.get_messages(session_id, scope)
    fresh: list[dict[str, Any]] = []
    for text, source in ((user_text, INTRO_SOURCE_USER), (assistant_text, INTRO_SOURCE_AI)):
        if not text or not text.strip():
            continue
        for name in extract_character_names(text):
            if name in known:
                continue
            if not is_person_name_like(name):
                continue
            if blob.count(name) < 2 and not is_self_introduced(blob, name):
                continue
            known.add(name)
            fresh.append(_create_supporting_card(name, source, text, messages))
    refresh_relations(scope, session_id, user_text + "\n" + assistant_text)
    return fresh


def _create_supporting_card(
    name: str,
    source: str,
    evidence_text: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """落一张空的配角卡，等 AI 补全。"""
    turns, dialogue = _turn_stats(name, messages)
    payload: dict[str, Any] = {
        "name": name,
        "role": ROLE_SUPPORTING,
        "identity": "",
        "behavior": "",
        "speech": "",
        "relation": relation_level(turns, dialogue),
        "state": {},
        "introduced_by": normalize_intro_source(source),
        "pending": True,
        "fill_error": "",
        "intro_evidence": _excerpt(evidence_text, name),
    }
    return save_card({"name": name, "spec_version": "v1", "data": payload})


def _excerpt(text: str, name: str) -> str:
    """抽到名字的那一句，存下来给用户判断这个角色值不值得留。"""
    body = (text or "").replace("\n", " ").strip()
    index = body.find(name)
    if index < 0:
        return body[:120]
    start = max(0, index - 40)
    return body[start : start + 120].strip()


def refresh_relations(scope: str, session_id: str, text: str) -> None:
    """名字再次出现时把关系档位往上调。只升不降。"""
    if not text or not text.strip():
        return
    messages = store.get_messages(session_id, scope)
    for summary in list_cards():
        if normalize_role(summary.get("role")) != ROLE_SUPPORTING:
            continue
        name = str(summary.get("name") or "").strip()
        if not name or name not in text:
            continue
        card = get_card(str(summary.get("id") or ""))
        if not isinstance(card, dict):
            continue
        payload = card.get("data")
        payload = payload if isinstance(payload, dict) else {}
        current = str(payload.get("relation") or RELATION_DEFAULT)
        turns, dialogue = _turn_stats(name, messages)
        level = relation_level(turns, dialogue)
        if _LEVEL_ORDER.get(level, 0) <= _LEVEL_ORDER.get(current, 0):
            continue
        payload["relation"] = level
        save_card(
            {
                "id": str(summary.get("id") or ""),
                "name": name,
                "spec_version": "v1",
                "data": payload,
            }
        )


def list_pending_cards() -> list[dict[str, Any]]:
    """列出待完善的配角卡：AI 还没补全、用户也没改过的那些。"""
    return [
        dict(summary)
        for summary in list_cards()
        if normalize_role(summary.get("role")) == ROLE_SUPPORTING
        and bool(summary.get("pending"))
    ]
