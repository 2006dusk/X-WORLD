"""括号即剧情与动作块进历史的测试。

覆盖：
- 用户写了圆括号时，本轮输入末尾追加括号即剧情的指令；
- 用户没写圆括号时不追加（提了就等于叫模型写它）；
- 动作块进历史：这一格发生了什么是接戏依据，不能丢；
- 动作块里混着台词时整段丢掉，台词不在历史里出现两遍。
"""

from __future__ import annotations

from pathlib import Path

from app import presets as preset_service
from app.assembly.pipeline import _history_reply, assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions


def _request(text: str) -> ChatRequest:
    """带一段用户输入的装配请求。"""
    return ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=text)],
        stream=False,
        xw=XwOptions(),
    )


def test_paren_directive_appended_when_user_writes_paren(data_dir: Path) -> None:
    """用户用圆括号指定剧情时，指令附在本轮输入末尾。"""
    preset_service.ensure_builtin_preset()
    text = "（她的两个贴身侍女把我牵着，开始了调教）"
    ctx = assemble(_request(text))
    content = ctx.messages[-1].content
    directive = preset_service.paren_directive(text)
    assert directive
    assert directive in content
    # 指令落在最后，紧挨生成点。
    assert content.rstrip().endswith(directive)
    # 括号里的动作被原样带回来：模型得知道这一格要写的是哪一件事。
    assert "她的两个贴身侍女把我牵着" in directive


def test_paren_directive_carries_every_paren_action(data_dir: Path) -> None:
    """多个圆括号都原样带回来。"""
    directive = preset_service.paren_directive("（她把茶盏搁下）（宫女的脚步声近了）")
    assert "她把茶盏搁下" in directive
    assert "宫女的脚步声近了" in directive


def test_paren_directive_empty_without_matched_paren(data_dir: Path) -> None:
    """没闭合的圆括号不算，指令为空串。"""
    assert preset_service.paren_directive("（她把茶盏搁下") == ""


def test_paren_directive_absent_without_paren(data_dir: Path) -> None:
    """没有圆括号时不追加这条指令。

    提示里提了模型就会去写：圆括号规则无条件出现时，模型会在正文里
    自己加括号。同一条结论见 backend/tools/probe_scene_ratio.py。
    """
    preset_service.ensure_builtin_preset()
    ctx = assemble(_request("好大……好深"))
    assert "圆括号" not in ctx.messages[-1].content


def test_history_keeps_action_with_dialogue(data_dir: Path) -> None:
    """历史里留动作与台词：动作是这一格发生了什么的接戏依据。"""
    result = _history_reply(
        "她轻勾门帘。\n\u201c陛下莫要惊慌。\u201d",
        {
            "action": "她轻勾门帘，两名宫女鱼贯而入。",
            "dialogue": "\u201c陛下莫要惊慌。\u201d",
        },
    )
    assert "两名宫女鱼贯而入" in result
    assert "莫要惊慌" in result


def test_history_drops_action_with_dialogue_inside(data_dir: Path) -> None:
    """动作块里混着台词时整段丢掉，台词不在历史里出现两遍。"""
    action = "她慵懒地靠在椅背上。\n\u201c陛下莫要惊慌。\u201d"
    result = _history_reply(action, {"action": action, "dialogue": ""})
    assert "靠在椅背上" not in result
