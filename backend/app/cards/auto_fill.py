"""AI 补全配角卡。

自动建出来的配角卡只有名字，内容是空的。补全用本机模型跑一次：把这一轮
的剧情喂进去，让它按主角色卡同样的三层写出这个人的身份、行为、说话方式
与此刻的状态。

补全是后台做的，不挂在对话响应上；本机推理只有一个槽位，所以先延后几秒
再发，别抢用户下一轮对话的队。补全失败就把卡留在待完善，等用户重试。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Any

from app import store
from app.cards import get_card, save_card
from app.contracts import ChatMessage
from app.inference.base import InferenceParams
from app.inference.llamacpp import LlamaCppAdapter

# 补全延后多久再发：本机推理服务只有一个槽位，抢队会让用户的下一轮干等。
FILL_DELAY_SECONDS = 3.0

# 补全用多少 token：三层设定加状态，实测三百多字足够。
FILL_MAX_TOKENS = 420

# 补全用标记式输出，不用 JSON。
# 推理适配器会把成对的 ASCII 引号归一成中文引号，JSON 的字符串定界符
# 就此失效，解析必然失败。标记式没有这个问题，跟回合块一个路子。
FILL_SYSTEM = """你在给一张角色卡补设定。这张卡的主角是刚刚在剧情里出现的一个配角。
按下面六行写，每行一个方括号标记，标记后面写内容，不要写别的解释：
【身份与处境】他是谁、眼下什么处境
【行为与细节】外形、随身的东西、行为倾向、他会做什么
【说话方式】语气、句长、措辞习惯
【所在位置】此刻在哪
【当前心情】此刻的心情
【随身物品】用顿号分隔"""

FILL_USER = """角色名：{name}
引入来源：{source}
这段剧情：
{context}

按六行写："""

# 标记与卡字段的对应。
_MARKER_KEYS = {
    "身份与处境": "identity",
    "行为与细节": "behavior",
    "说话方式": "speech",
    "所在位置": "location",
    "当前心情": "mood",
    "随身物品": "items",
}
_MARKER_RE = re.compile(
    r"【\s*(" + "|".join(_MARKER_KEYS) + r")\s*】\s*([^\n【]*)"
)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# 宽松解析：模型不总给严格 JSON，有时是中文引号、有时前后带解释。
_FIELD_ASCII = re.compile(r'"(identity|behavior|speech)"\s*:\s*"([^"]*)"')
_FIELD_CN = re.compile(r'"(identity|behavior|speech)"\s*:\s*[“「『]([^”」』]*)[”」』]')
_STATE_ASCII = re.compile(r'"(location|mood)"\s*:\s*"([^"]*)"')
_ITEMS = re.compile(r'"items"\s*:\s*\[([^\]]*)\]')
_ITEM = re.compile(r'"([^"]*)"')


def _lenient_fields(body: str) -> dict[str, Any]:
    """严格 JSON 解析不出来时，按字段抠。

    本机 9B 模型经常把 JSON 写成中文引号或者前后带一句解释，
    直接丢掉整段补全太浪费，按字段抠能救回大部分内容。
    """
    data: dict[str, Any] = {}
    for match in _FIELD_ASCII.finditer(body):
        data.setdefault(match.group(1), match.group(2))
    for match in _FIELD_CN.finditer(body):
        data.setdefault(match.group(1), match.group(2))
    state: dict[str, Any] = {}
    for match in _STATE_ASCII.finditer(body):
        state.setdefault(match.group(1), match.group(2))
    items = _ITEMS.search(body)
    if items:
        names = [m.group(1) for m in _ITEM.finditer(items.group(1)) if m.group(1).strip()]
        if names:
            state["items"] = names
    if state:
        data["state"] = state
    return data


def parse_fill_payload(text: str) -> dict[str, Any]:
    """从模型回复里取出补全结果。解析不出就返回空字典。

    先按标记式解析（主路径），再退回 JSON 解析（模型偶尔自己给 JSON）。
    """
    body = (text or "").strip()
    if not body:
        return {}
    data: dict[str, Any] = {}
    state: dict[str, Any] = {}
    for match in _MARKER_RE.finditer(body):
        key = _MARKER_KEYS.get(match.group(1), "")
        value = match.group(2).strip()
        if not key or not value:
            continue
        if key == "items":
            names = [part.strip() for part in re.split(r"[、,，]", value) if part.strip()]
            if names:
                state["items"] = names
        elif key in ("location", "mood"):
            state[key] = value
        else:
            data.setdefault(key, value)
    if state:
        data["state"] = state
    if data:
        return data
    return _parse_json_payload(body)


def _parse_json_payload(body: str) -> dict[str, Any]:
    """兜底解析：模型给了 JSON。"""
    fence = _FENCE.search(body)
    if fence:
        body = fence.group(1).strip()
    start = body.find("{")
    end = body.rfind("}")
    if start >= 0 and end > start:
        body = body[start : end + 1]
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return _lenient_fields(body)
    if not isinstance(data, dict):
        return {}
    if not any(key in data for key in ("identity", "behavior", "speech", "state")):
        return _lenient_fields(body)
    return data


def _load_profile_params() -> dict[str, Any] | None:
    """当前档位的采样参数。跟对话链路同一套取法。"""
    try:
        active = store.get_setting("active_model")
        profile_id = ""
        if isinstance(active, dict):
            profile_id = str(active.get("profile_id") or "")
        if profile_id:
            profile = store.get_profile(profile_id)
            if isinstance(profile, dict):
                params = profile.get("params")
                return params if isinstance(params, dict) else None
        profiles = store.list_profiles()
        if profiles:
            params = profiles[-1].get("params")
            return params if isinstance(params, dict) else None
    except Exception:
        return None
    return None


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_state(value: Any) -> dict[str, Any]:
    """状态只留位置、心情与随身物品三样。"""
    if not isinstance(value, dict):
        return {}
    state: dict[str, Any] = {}
    for key in ("location", "mood"):
        text = _clean_text(value.get(key))
        if text:
            state[key] = text
    items = value.get("items")
    if isinstance(items, list):
        names = [_clean_text(item) for item in items]
        names = [name for name in names if name]
        if names:
            state["items"] = names
    elif isinstance(items, str) and items.strip():
        state["items"] = [part.strip() for part in re.split(r"[、,，]", items) if part.strip()]
    return state


def fill_card(card_id: str, context: str = "") -> dict[str, Any]:
    """跑一次模型补全这张配角卡并写回。

    返回 {"ok": bool, "card": dict | None, "error": str}。
    补不成就把卡留在待完善，失败原因记在卡上。
    """
    card = get_card(card_id)
    if card is None:
        return {"ok": False, "card": None, "error": "角色卡不存在"}
    payload = card.get("data")
    payload = dict(payload) if isinstance(payload, dict) else {}
    name = str(payload.get("name") or card.get("name") or "").strip()
    source = str(payload.get("introduced_by") or "ai")

    params = InferenceParams(
        temperature=0.7,
        top_p=0.9,
        max_tokens=FILL_MAX_TOKENS,
        repetition_penalty=1.1,
    )
    messages = [
        ChatMessage(role="system", content=FILL_SYSTEM),
        ChatMessage(
            role="user",
            content=FILL_USER.format(name=name, source=source, context=(context or "")[:2000]),
        ),
    ]
    adapter = LlamaCppAdapter()
    try:
        result = asyncio.run(adapter.chat(messages, params, _load_profile_params()))
    except Exception as exc:
        _mark_failed(card_id, payload, f"推理服务不可用：{exc}")
        return {"ok": False, "card": None, "error": str(exc)}

    data = parse_fill_payload(result.content)
    if not data:
        _mark_failed(card_id, payload, "模型没有给出可解析的 JSON")
        return {"ok": False, "card": None, "error": "模型没有给出可解析的 JSON"}

    for key in ("identity", "behavior", "speech"):
        text = _clean_text(data.get(key))
        if text:
            payload[key] = text
    state = _clean_state(data.get("state"))
    if state:
        payload["state"] = state
    payload["pending"] = False
    payload["fill_error"] = ""
    saved = save_card(
        {
            "id": card_id,
            "name": name,
            "spec_version": "v1",
            "data": payload,
        }
    )
    return {"ok": True, "card": saved, "error": ""}


def _mark_failed(card_id: str, payload: dict[str, Any], reason: str) -> None:
    """补全失败：卡留在待完善，并记下原因。"""
    payload["pending"] = True
    payload["fill_error"] = reason
    save_card(
        {
            "id": card_id,
            "name": str(payload.get("name") or ""),
            "spec_version": "v1",
            "data": payload,
        }
    )


def schedule_fill(
    card_id: str,
    context: str,
    delay: float = FILL_DELAY_SECONDS,
) -> threading.Thread:
    """延后几秒再补全。

    推理服务只有一个槽位，延后是为了让用户下一轮对话先拿到槽位。
    """
    if not card_id:
        raise ValueError("card_id 不能为空")

    def worker() -> None:
        time.sleep(max(0.0, delay))
        try:
            fill_card(card_id, context)
        except Exception:
            return

    thread = threading.Thread(target=worker, name="xworld-card-fill", daemon=True)
    thread.start()
    return thread
