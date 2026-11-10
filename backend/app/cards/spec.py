"""角色卡规范解析。

支持 Character Card V1、V2 与 V3 的字段读取。V1 与 V2 按未声明许可处理，
仅做兼容性读取；V3 为 MIT 许可，完整解析扩展字段。
"""

from __future__ import annotations

import re
from typing import Any


# 本项目不保存的字段。场景由世界书的地区条目提供，卡片里再放一份会
# 与世界状态冲突（实测角色因此被重新安到别的场景）；开场白与长期对话
# 的用法不相干；创作者与角色版本对本项目没有意义。V2_COMMON_FIELDS 仍保留
# 这些键用于兼容读取外部卡片，保存时丢弃。
DROPPED_FIELDS: tuple[str, ...] = (
    "scenario",
    "first_mes",
    "creator",
    "character_version",
)

# 内部 v1 卡片格式：三层分工。
#
# 旧格式只有 description 与 personality 两段，身份、行为、说话方式混在一起。
# 混在一起的代价实测得到：模型把「好为人师、边做边讲解」这类性格设定直接
# 搬进台词，于是每一轮都在训话。拆成三层之后每层只对应一个输出块：
# identity 决定角色是谁，behavior 决定动作块写什么，speech 决定台词怎么写。
INTERNAL_V1_FIELDS: tuple[str, ...] = (
    "name",
    "identity",
    "behavior",
    "speech",
    "relation",
    "mes_example",
    "tags",
    "creator_notes",
)

# 角色定位：主角还是配角。
#
# 主角卡进 character 段，每轮必发。配角卡是对话中由模型引入的角色，走
# supporting 段：名字在上下文里被提到时才注入，段内带一句边界声明，位置
# 在 character 段之前（弱影响位）。外部卡没有这个字段，一律按主角处理。
ROLE_PRIMARY = "primary"
ROLE_SUPPORTING = "supporting"
CARD_ROLES: tuple[str, ...] = (ROLE_PRIMARY, ROLE_SUPPORTING)

# 关系程度档位。配角与主角的关系不是一句自由文本，是程度：随机路过的人
# 只是「相逢」，聊过才是「相识」，常来常往是「相熟」。档位只升不降。
RELATION_LEVELS: tuple[str, ...] = ("相逢", "相识", "相熟")
RELATION_DEFAULT = "相逢"

# 引入来源：这个角色是被用户带进来的，还是模型自己带出来的。
INTRO_SOURCE_USER = "user"
INTRO_SOURCE_AI = "ai"
INTRO_SOURCES: tuple[str, ...] = (INTRO_SOURCE_USER, INTRO_SOURCE_AI)

# 旧格式落到内部 v1 的字段映射。外部 V1/V2/V3 卡导入后一律转成内部 v1。
LEGACY_TO_INTERNAL: tuple[tuple[str, str], ...] = (
    ("description", "identity"),
    ("personality", "behavior"),
)

# 外部卡没有说话方式层，用这条兜底：短句、贴着当下、不讲道理。
# 不带「说话方式：」前缀：装配层拼 character 段时会加标签，这里再加一遍就成了
# 「说话方式：说话方式：……」。
DEFAULT_SPEECH = (
    "短句，贴着此刻发生的事说；不解释自己的心思，不总结道理，"
    "不训话，也不给用户下指令。"
)

# 标签受控词表。标签进提示词时写成「角色类型标签（必须符合）：……」，
# 写什么就等于给模型下了什么类型的约束，所以词表要对得上、粒度要一致。
#
# 整理前四张卡共十四种写法，同义的有三组（4i 与四爱、脚下玩法与足控调教、
# 女S 与女性支配），还混进了身份与场景标签（标签A、标签B、标签C）：
# 身份与场景在 identity 层与世界书的地区条目里已经写全，作为「类型约束」
# 再写一遍只是噪音，还会把模型往「每段都提标签A」上带。
#
# 词表按「题材 → 偏好」排，进提示词就是这个顺序，读起来是一句话。
TAG_VOCABULARY: tuple[str, ...] = (
    "四爱",
    "女性支配",
    "女攻男受",
    "穿戴式假阳具",
    "足控调教",
    "气味调教",
    "汗脚",
    "淫语",
)

# 同义写法归到词表正名。外部卡导入时也走这张表。
TAG_ALIASES: dict[str, str] = {
    "4i": "四爱",
    "四I": "四爱",
    "脚下玩法": "足控调教",
    "女S": "女性支配",
}

# 标签之间允许的分隔符。中英文逗号与顿号都认：前端编辑器用顿号拼接，
# 用逗号切分，两者对不上时整串会变成一个标签。
TAG_SEPARATORS = re.compile(r"[、，,]")


def normalize_tags(tags: Any) -> list[str]:
    """把标签归一成受控词表里的写法与顺序。

    别名归正名、去重、按词表排序。词表外的标签保留：用户在编辑器里自己加的
    不该被静默丢掉，只是排在词表的标签后面，按字面排。
    """
    if isinstance(tags, str):
        items = [part.strip() for part in TAG_SEPARATORS.split(tags)]
    elif isinstance(tags, (list, tuple)):
        items = [str(part).strip() for part in tags]
    else:
        items = []

    resolved: list[str] = []
    for item in items:
        if not item:
            continue
        canonical = TAG_ALIASES.get(item) or TAG_ALIASES.get(item.lower())
        name = canonical or item
        if name not in resolved:
            resolved.append(name)

    known = [name for name in TAG_VOCABULARY if name in resolved]
    unknown = sorted(name for name in resolved if name not in TAG_VOCABULARY)
    return known + unknown


def normalize_role(value: Any) -> str:
    """归一角色定位。

    认不出来的按主角处理：配角是少数派，猜错时把角色留在主角位比把她
    降成配角安全——降级会让她的设定不再每轮注入。
    """
    text = str(value or "").strip().lower()
    return text if text in CARD_ROLES else ROLE_PRIMARY


def normalize_relation_level(value: Any) -> str:
    """归一关系程度档位。认不出来的按「相逢」处理。"""
    text = str(value or "").strip()
    return text if text in RELATION_LEVELS else RELATION_DEFAULT


def normalize_intro_source(value: Any) -> str:
    """归一引入来源。认不出来的按模型引入处理。"""
    text = str(value or "").strip().lower()
    return text if text in INTRO_SOURCES else INTRO_SOURCE_AI


# V2 常见字段，供本模块及外部访问。
V2_COMMON_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "creator",
    "character_version",
    "system_prompt",
    "post_history_instructions",
    "alternate_greetings",
    "character_book",
    "tags",
    "creator_notes",
)


def _normalize_v1(data: dict[str, Any]) -> dict[str, Any]:
    """将 V1 字段映射为 V2 兼容结构。"""
    normalized: dict[str, Any] = {}
    field_map = {
        "char_name": "name",
        "char_persona": "description",
        "world_scenario": "scenario",
        "char_greeting": "first_mes",
        "example_dialogue": "mes_example",
    }
    for old_key, new_key in field_map.items():
        if old_key in data:
            normalized[new_key] = data[old_key]
    for key in V2_COMMON_FIELDS:
        if key not in normalized and key in data:
            normalized[key] = data[key]
    return normalized


def _detect_spec(data: dict[str, Any]) -> str:
    """根据数据形态推断规范版本。
    
    内部 v1 卡的 data 里是 identity / behavior / speech 三层；
    外部 V3 卡的 data 里是 name / description / personality 等旧字段；
    外部 V1 卡用 char_name 这类旧键名，放在顶层。
    内部 v1 与外部 V1 都叫 v1，但结构不同：internal_v1 是本项目的三层结构，
    v1 是外部旧规范，要走 _normalize_v1 归一。两者混为一谈会让外部 V1 卡
    读出空的 name 与描述。
    """
    inner = data.get("data")
    if isinstance(inner, dict):
        if "identity" in inner and "description" not in inner:
            return "internal_v1"
        return "v3"
    if any(k in data for k in ("char_name", "char_persona", "char_greeting")):
        return "v1"
    if any(k in data for k in ("name", "description", "personality", "scenario", "first_mes")):
        return "v2"
    return "unknown"


def parse_card(data: dict[str, Any]) -> dict[str, Any]:
    """解析角色卡字典，返回规范化的内部结构。

    返回字典始终包含以下字段：
    - spec: 检测到的版本字符串（internal_v1/v1/v2/v3/unknown）。
    - data: 原始有效载荷（V3 取 data 字段，其余保持原样或 V1 归一化）。
    - v2: V2 兼容展平字段，便于装配层使用。
    """
    spec = _detect_spec(data)
    payload: dict[str, Any]
    v2_fields: dict[str, Any]

    if spec == "internal_v1":
        payload = data.get("data", {})
        v2_fields = {k: payload.get(k) for k in V2_COMMON_FIELDS}
        # 旧调用方要的字段名：identity 当描述、behavior 当性格。
        v2_fields["description"] = payload.get("identity", "")
        v2_fields["personality"] = payload.get("behavior", "")
    elif spec == "v3":
        payload = data.get("data", {})
        v2_fields = {k: payload.get(k) for k in V2_COMMON_FIELDS}
    elif spec == "v2":
        payload = data
        v2_fields = {k: data.get(k) for k in V2_COMMON_FIELDS}
    elif spec == "v1":
        payload = _normalize_v1(data)
        spec = "v2"
        v2_fields = {k: payload.get(k) for k in V2_COMMON_FIELDS}
    else:
        payload = data
        v2_fields = {}

    return {"spec": spec, "data": payload, "v2": v2_fields}


def to_internal_v1(payload: dict[str, Any]) -> dict[str, Any]:
    """把任意来源的卡片载荷转成内部 v1 结构。

    外部卡只有 description 与 personality：前者进 identity，后者进 behavior，
    说话方式用默认那条补上。已经是 v1 的卡原样返回，只剔除本项目不用的字段。
    标签顺带归一：别名归正名、去重、按受控词表排序。
    """
    result = {
        key: value
        for key, value in payload.items()
        if key not in DROPPED_FIELDS
    }
    for legacy, target in LEGACY_TO_INTERNAL:
        value = result.pop(legacy, None)
        if value and not result.get(target):
            result[target] = value
    if not result.get("speech"):
        result["speech"] = DEFAULT_SPEECH
    result.setdefault("name", "")
    # 角色定位与配角档案：外部卡没有这些字段，主角卡也用不上。
    result["role"] = normalize_role(result.get("role"))
    result.setdefault("relation", "")
    state = result.get("state")
    result["state"] = state if isinstance(state, dict) else {}
    if result["role"] == ROLE_SUPPORTING:
        # 配角卡专有的三样：来源、关系程度、待完善标记。
        # 关系是程度不是自由句，所以外来的一律按档位归一。
        result["introduced_by"] = normalize_intro_source(result.get("introduced_by"))
        result["relation"] = normalize_relation_level(result.get("relation"))
        result["pending"] = bool(result.get("pending"))
    if "tags" in result:
        result["tags"] = normalize_tags(result.get("tags"))
    return result


def v2_text_fields(card: dict[str, Any]) -> dict[str, str]:
    """提取 V2 兼容文本字段，空值转为空字符串。"""
    v2 = card.get("v2", {})
    return {
        field: (v2.get(field) or "") if not isinstance(v2.get(field), list) else ""
        for field in V2_COMMON_FIELDS
        if field
        not in (
            "alternate_greetings",
            "character_book",
            "tags",
        )
    }
