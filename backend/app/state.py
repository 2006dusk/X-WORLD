"""世界状态。

状态由模型每轮回复自动更新：回合结构里多一块【状态】，模型按固定键名逐行写
当前场景、装备物品、地图与时间，后端解析成结构化字段落盘，并在下一轮装配时
回灌给模型。

状态块用固定键名的行式文本而不是 JSON：本地实测（docs/实践情况/
质量与速度平衡点.md 7.18.1）里，让模型在 JSON 字段里写中文状态的失败率明显
高于自由文本，行式文本的解析成功率与可读性都更好。
"""

from __future__ import annotations

import re
from typing import Any

# 状态字段的固定顺序，回灌与展示都按这个顺序。
# location 是地点，position 是角色在这个地点里的具体站位。
# 分成两个维度是因为只给地点时，模型可以在同一个地点里反复「站在门口」
# 与「推门而入」（实测：状态位置一直是地址V，正文却轮流写门口与店内）。
STATE_FIELDS: tuple[str, ...] = (
    "location",
    "position",
    "items",
    "scene_items",
    "map",
    "time",
)

# 列表型字段：物品类，按分隔符拆成列表。
# 人物物品与场景物品分开记：店里的抹布、咖啡机属于场景，不属于角色。
# 混在一栏时模型会把角色写成店里的员工，物品栏也跟着被污染。
LIST_FIELDS: tuple[str, ...] = ("items", "scene_items")

# 字段名到展示标签。
FIELD_LABELS: dict[str, str] = {
    "location": "当前位置",
    "position": "人物位置",
    "items": "人物物品",
    "scene_items": "场景物品",
    "map": "地图",
    "time": "时间",
}

# 键名别名：模型可能写成「场景」「随身物品」等，归一到同一字段。
FIELD_ALIASES: dict[str, str] = {
    "当前位置": "location",
    "当前场景": "location",
    "所在场景": "location",
    "所在位置": "location",
    "场景": "location",
    "地点": "location",
    "位置": "location",
    "人物位置": "position",
    "具体位置": "position",
    "站位": "position",
    "所处位置": "position",
    "站在哪": "position",
    "坐在哪": "position",
    "人物物品": "items",
    "装备物品": "items",
    "随身物品": "items",
    "携带物品": "items",
    "持有物品": "items",
    "身上的东西": "items",
    "物品": "items",
    "场景物品": "scene_items",
    "环境物品": "scene_items",
    "现场物品": "scene_items",
    "场景里的物品": "scene_items",
    "场景中的物品": "scene_items",
    "环境中的物品": "scene_items",
    "周围的东西": "scene_items",
    "地图": "map",
    "地点地图": "map",
    "所在地图": "map",
    "区域": "map",
    "当前时间": "time",
    "时间": "time",
}

# 物品列表的分隔符：顿号、逗号、斜杠，以及口语里的「和」「跟」。
_ITEM_SPLIT = re.compile(r"[、,，/]|和|跟")

# 物品栏里的括号注释：模型会写「金笼（隐喻实指）」这种带注释的物件名。
# 注释不是物件名的一部分，括号内容丢掉；括号本身当分隔符，
# 括号后紧跟的另一件物品因此不会被粘成一串。
_PAREN_NOTE = re.compile(r"[（(][^）)]*[）)]")

# 物品栏的占位值：模型写「无」「没有」时表示这一栏是空的，不是一件物品。
_EMPTY_ITEM_WORDS = {"无", "没有", "空", "暂无", "没有物品", "none", "nothing", "-", "—"}

# 行分隔：换行与分号都算一条状态的边界。
_LINE_SPLIT = re.compile(r"[\n；;]+")


def _split_key_value(line: str) -> tuple[str, str] | None:
    """把一行拆成键与值。

    键值之间用全角或半角冒号，取最先出现的那个。没有冒号时返回 None。
    """
    best = -1
    width = 0
    for candidate in ("：", ":"):
        pos = line.find(candidate)
        if pos != -1 and (best == -1 or pos < best):
            best = pos
            width = len(candidate)
    if best == -1:
        return None
    return line[:best].strip(), line[best + width:].strip()


def _split_items(value: str) -> list[str]:
    """把物品串拆成列表。

    「无」「没有」这类占位值表示这一栏是空的，拆完是空列表，
    合并时据此把上一轮的物品清掉。
    """
    parts = [part.strip() for part in _ITEM_SPLIT.split(_PAREN_NOTE.sub("、", value))]
    return [part for part in parts if part and part not in _EMPTY_ITEM_WORDS]


def _clean_value(value: str) -> str:
    """清掉值两端的空白与句末标点。"""
    text = value.strip().strip("「」『』\"")
    text = text.rstrip("。！？；，、 ")
    return text.strip()


def parse_state_line(line: str) -> tuple[str, Any] | None:
    """解析单行状态键值，返回 (字段名, 值)；不是状态行时返回 None。

    值去掉句末标点。物品栏按分隔符拆成列表；写成「无」时是空列表，
    表示模型明确说这一栏没有东西。
    """
    pair = _split_key_value(line)
    if pair is None:
        return None
    key, value = pair
    name = FIELD_ALIASES.get(key)
    if name is None:
        return None
    cleaned = _clean_value(value)
    if not cleaned:
        return None
    if name in LIST_FIELDS:
        return name, _split_items(cleaned)
    return name, cleaned


def split_state_block(text: str) -> tuple[dict[str, Any], list[str]]:
    """拆分状态块，返回 (字段字典, 剩余行)。

    键值行归入字段；其余行是模型塞进状态块的非键值内容：成句的行或括号
    段落归回正文，短碎片丢弃（多半是换行折断的值）。
    """
    fields: dict[str, Any] = {}
    rest: list[str] = []
    if not text:
        return fields, rest
    for raw_line in _LINE_SPLIT.split(text):
        line = raw_line.strip()
        if not line:
            continue
        parsed = parse_state_line(line)
        if parsed is not None:
            name, value = parsed
            # 物品栏即使写成「无」也要落成空列表：模型明确说没有时，
            # 上一轮的旧物品就该被清掉，而不是留着。
            fields[name] = value
            continue
        if len(line) >= 12 or (line.startswith("（") and line.endswith("）")):
            rest.append(line)
    return fields, rest


def parse_state_block(text: str) -> dict[str, Any]:
    """解析状态块文本，返回字段字典。

    行式解析：每行「键：值」，键按别名归一，值去掉句末标点。
    装备物品按分隔符拆成列表。识别不到的键忽略。
    没有任何可识别字段时返回空字典，调用方据此判定状态块不可用。
    """
    fields, _rest = split_state_block(text)
    return fields


def merge_state(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """合并新旧状态。

    模型写到的字段覆盖旧值；模型这轮没写的字段沿用旧值，避免面板闪空。
    """
    merged: dict[str, Any] = dict(old or {})
    for name in STATE_FIELDS:
        if name not in new:
            continue
        value = new[name]
        if name in LIST_FIELDS:
            # 物品栏整体替换，包括空列表：模型这轮说没有就该清掉。
            merged[name] = [
                str(item).strip() for item in (value or []) if str(item).strip()
            ]
            continue
        text = str(value or "").strip()
        if text:
            merged[name] = text
    return merged


def render_state_block(state: dict[str, Any] | None) -> str:
    """把状态渲染成五行文本，供装配回灌给模型。

    键名与解析用的别名表一致，模型看到的就是自己上轮写的那种写法。
    """
    if not state:
        return ""
    lines: list[str] = []
    for name in STATE_FIELDS:
        label = FIELD_LABELS[name]
        if name in LIST_FIELDS:
            items = [str(item).strip() for item in (state.get(name) or []) if str(item).strip()]
            value = "、".join(items)
        else:
            value = str(state.get(name) or "").strip()
        if not value:
            continue
        lines.append(label + "：" + value)
    return chr(10).join(lines)


def relocate_state(
    state: dict[str, Any] | None,
    label: str,
    region_id: str = "",
) -> dict[str, Any]:
    """把状态搬到新地点：场景陈设清空，随身物品与时间不动。

    角色换区域时人也换了地方，原地点的陈设属于上一个地方，留着会被当成
    新地方的实情，模型照着写就把旧场景带过来了。地点直接落到新区域，
    场景由下一轮模型按区域设定重建。
    """
    updated = dict(state or {})
    updated["location"] = label
    updated["map"] = label
    updated["region_id"] = str(region_id or "")
    if str((state or {}).get("region_id") or "") == str(region_id or ""):
        # 已经在这个地区：不算搬家，场景留着
        return updated
    updated["position"] = ""
    updated["scene_items"] = []
    return updated


def character_name_of(character_id: str) -> str:
    """角色卡里的名字。状态按角色存，界面要显示是谁在这儿。

    卡片不在时返回空串，界面按「—」显示，不报错。
    """
    if not character_id:
        return ""
    from app import store

    card = store.get_card(character_id)
    if not isinstance(card, dict):
        return ""
    return str(card.get("name") or "")


def state_payload(
    state: dict[str, Any] | None,
    character_id: str = "",
) -> dict[str, Any]:
    """对外的状态形状：字段齐备，缺失填空，物品恒为列表。

    带 character_name：状态按角色存，界面要显示是谁在这儿。角色 id
    优先取传入值，其次取状态文件里记的。
    """
    source = state or {}
    items = [str(item).strip() for item in (source.get("items") or []) if str(item).strip()]
    scene_items = [
        str(item).strip() for item in (source.get("scene_items") or []) if str(item).strip()
    ]
    return {
        "character_name": character_name_of(
            character_id or str(source.get("character_id") or "")
        ),
        "location": str(source.get("location") or ""),
        "position": str(source.get("position") or ""),
        "items": items,
        "scene_items": scene_items,
        "map": str(source.get("map") or ""),
        "time": str(source.get("time") or ""),
        # 角色绑定的地区：他人在哪，切换人物时对话的位置跟着他走
        "region_id": str(source.get("region_id") or ""),
        "updated_at": source.get("updated_at") or None,
    }
