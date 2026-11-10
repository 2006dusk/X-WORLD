"""角色符合度检查。

回答一个问题：模型这一轮说的话，算不算这个角色说的。

三层判定，全是确定性的，不问裁判模型：

L1 结构层——这段话本身成不成立。有没有模板标记残留，引号配不配平，
    长度在不在要求的区间，有没有对白。

L2 身份层——这段话像不像这个角色。角色自称是不是卡里声明的那个，
    有没有写别人名字，有没有把用户该做的事替用户做了。

L3 声线层——这条声线向量挂上去之后，角色自己的原话是不是更可能出现。
    这一层要推理端配合，见 app.routers.voices 的 /v1/voices/verify。

每层各自给结论与理由。任何一层不过，程序就知道这一轮该重写、
该归一，还是该换剂量，而不是靠人眼看。

数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

from typing import Any

from app.inference.normalize import (
    IMPERIAL_SELF_REFERENCES,
    TEMPLATE_MARKERS,
    drop_stray_closers,
)

# 判定档位。
PASS = "pass"
WEAK = "weak"
FAIL = "fail"

# 单次回复的长度区间（字符）。与预设的回合要求一致。
# 下沿取 40：实测四张现役卡的真实回合是 130 到 200 字，短于此说明这轮
# 没写成完整一回合（只接了半句，或只有叙述没有展开）。
MIN_CHARS = 40
MAX_CHARS = 400

# 帝王自称。角色卡声明的自称不在这一组里，却在台词里出现，就是自称滑了。
SELF_REFERENCE_FALLBACKS = IMPERIAL_SELF_REFERENCES

# 替用户行动的信号句式。模型替用户安排动作或心理时用这些词起头。
USER_AGENT_PATTERNS = (
    "你决定",
    "你选择",
    "你想",
    "你心里",
    "你暗想",
    "你不自觉",
    "你伸手",
    "你握住",
    "你退后",
    "你点头",
    "你摇头",
    "你笑",
    "你叹",
    "你看着",
    "你朝",
)


def _verdict(ok: bool, weak: bool = False) -> str:
    """把两个布尔压成三档结论。"""
    if ok:
        return PASS
    if weak:
        return WEAK
    return FAIL


def check_structure(text: str, *, min_chars: int = MIN_CHARS) -> dict[str, Any]:
    """L1 结构层：这段话本身成不成立。

    参数：
        text: 模型的回复正文。
        min_chars: 长度下沿。

    返回：
        {"verdict": ..., "issues": [...], "metrics": {...}}。
    """
    issues: list[str] = []
    body = str(text or "")

    markers = [marker for marker in TEMPLATE_MARKERS if marker in body]
    if markers:
        issues.append("模板标记没有清理干净: " + "、".join(markers))

    _, balance = drop_stray_closers(body)
    if balance:
        issues.append("引号没有配平")

    stripped = body.strip()
    length = len(stripped)
    if length < min_chars:
        issues.append("正文过短: %d 字" % length)
    elif length > MAX_CHARS:
        issues.append("正文过长: %d 字" % length)

    has_dialogue = ("“" in body and "”" in body) or ("「" in body and "」" in body)
    if not has_dialogue:
        issues.append("没有对白")

    return {
        "verdict": _verdict(not issues),
        "issues": issues,
        "metrics": {
            "chars": length,
            "has_dialogue": has_dialogue,
            "quote_balance": balance,
        },
    }


def check_identity(
    text: str,
    *,
    declared_self_reference: str = "",
    character_name: str = "",
    other_names: list[str] | None = None,
) -> dict[str, Any]:
    """L2 身份层：这段话像不像这个角色。

    参数：
        text: 模型的回复正文。
        declared_self_reference: 卡里声明的自称，空串表示没声明。
        character_name: 当前角色的名字。
        other_names: 别的角色的名字，写进来就是串场。

    返回：
        {"verdict": ..., "issues": [...], "metrics": {...}}。
    """
    issues: list[str] = []
    body = str(text or "")

    declared = str(declared_self_reference or "").strip()
    if declared:
        for word in SELF_REFERENCE_FALLBACKS:
            if word == declared:
                continue
            if word in body:
                issues.append("自称滑成了「%s」，卡里声明的是「%s」" % (word, declared))
                break
    else:
        hits = [word for word in SELF_REFERENCE_FALLBACKS if word in body]
        if hits:
            issues.append("出现了帝王自称「%s」，但卡里没有声明这个角色用这种自称" % hits[0])

    for name in other_names or []:
        if name and name != character_name and name in body:
            issues.append("写到了别的角色: %s" % name)
            break

    user_agent = [item for item in USER_AGENT_PATTERNS if item in body]
    if user_agent:
        issues.append("替用户安排了动作或心理: " + "、".join(user_agent[:3]))

    return {
        "verdict": _verdict(not issues),
        "issues": issues,
        "metrics": {
            "declared_self_reference": declared,
            "other_name_hits": len(
                [n for n in other_names or [] if n and n != character_name and n in body]
            ),
            "user_agent_hits": len(user_agent),
        },
    }


def summarize(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """把三层的结论合成一个总判定。

    参数：
        layers: 各层的检查结果，按 L1、L2、L3 顺序。

    返回：
        {"verdict": ..., "issues": [...]}。任何一层不过，总体就不过。
    """
    issues: list[str] = []
    verdicts: list[str] = []
    for layer in layers:
        verdicts.append(str(layer.get("verdict") or FAIL))
        for issue in layer.get("issues") or []:
            issues.append(str(issue))
    if not layers:
        return {"verdict": FAIL, "issues": ["没有任何一层可判"], "layers": []}
    if FAIL in verdicts:
        verdict = FAIL
    elif WEAK in verdicts:
        verdict = WEAK
    else:
        verdict = PASS
    return {"verdict": verdict, "issues": issues, "layers": layers}
