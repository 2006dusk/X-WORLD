"""内置 RP 预设与回合要求测试。

覆盖：
- 首启播种后内置预设存在、默认预设指向它，重复播种不改动内容；
- 预设解析链：请求指定 → 设置里的默认预设 → 内置预设；
- output_spec 渲染成回合要求并附在本轮输入末尾；
- 没有 output_spec 的预设在装配时不追加任何内容。
"""

from __future__ import annotations

from pathlib import Path

from app import presets as preset_service
from app import store
from app.assembly.pipeline import assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions


def test_ensure_builtin_preset_writes_preset(data_dir: Path) -> None:
    """首次播种写出内置预设，并把默认预设指向它。"""
    preset_id = preset_service.ensure_builtin_preset()
    assert preset_id == preset_service.BUILTIN_PRESET_ID
    preset = store.get_preset(preset_id)
    assert preset is not None
    assert preset["builtin"] is True
    # 内置预设不声明字数下限与上限：回合长度交给模型自己判断。
    assert "min_chars" not in preset["output_spec"]
    assert "max_chars" not in preset["output_spec"]
    assert preset["output_spec"]["structure"] == [
        "action",
        "scene",
        "dialogue",
        "state",
    ]
    assert store.get_setting("default_preset_id") == preset_id


def test_ensure_builtin_preset_is_idempotent(data_dir: Path) -> None:
    """重复播种不改动已写入的预设。"""
    preset_service.ensure_builtin_preset()
    first = store.get_preset(preset_service.BUILTIN_PRESET_ID)
    preset_service.ensure_builtin_preset()
    second = store.get_preset(preset_service.BUILTIN_PRESET_ID)
    assert first == second


def test_resolve_preset_id_prefers_request(data_dir: Path) -> None:
    """请求指定的预存在时优先。"""
    preset_service.ensure_builtin_preset()
    store.save_preset({"id": "p_user", "name": "用户预设", "prompts": []})
    assert preset_service.resolve_preset_id("p_user") == "p_user"


def test_resolve_preset_id_falls_back_to_default(data_dir: Path) -> None:
    """请求未指定时用设置里的默认预设。"""
    preset_service.ensure_builtin_preset()
    store.save_preset({"id": "p_user", "name": "用户预设", "prompts": []})
    store.set_setting("default_preset_id", "p_user")
    assert preset_service.resolve_preset_id(None) == "p_user"


def test_resolve_preset_id_falls_back_to_builtin(data_dir: Path) -> None:
    """默认预设为空时退回内置预设。"""
    preset_service.ensure_builtin_preset()
    store.set_setting("default_preset_id", "")
    assert preset_service.resolve_preset_id(None) == preset_service.BUILTIN_PRESET_ID


def test_resolve_preset_id_ignores_missing(data_dir: Path) -> None:
    """请求指定的预不存在时按未指定处理。"""
    preset_service.ensure_builtin_preset()
    assert preset_service.resolve_preset_id("p_absent") == preset_service.BUILTIN_PRESET_ID


def test_builtin_prompt_has_no_format_example(data_dir: Path) -> None:
    """内置预设不写具体格式示例。

    实测四种写法：带具体叙事示例的三种都会被模型逐字照抄，
    只有完全不写示例的一组六轮全在字数带内且零复读。
    """
    preset_service.ensure_builtin_preset()
    content = preset_service.BUILTIN_SYSTEM_PROMPT
    assert "格式示例" not in content
    assert "雨点敲在铁皮屋顶上" not in content
    # 回合要求只写在回合要求里：系统段不再复述。
    # 同一句写两遍时模型会把整轮回复逐字复现（实测 backend/tools/probe_copy_loop.py）。
    assert content.count("每次回复写一个完整回合") == 0
    round_req = preset_service.render_output_spec(preset_service.BUILTIN_OUTPUT_SPEC)
    assert round_req.count("本次回复是一个完整回合") == 1


def test_builtin_prompt_requires_action_and_interaction(data_dir: Path) -> None:
    """内置预设要求写这一格实际发生的事，并且台词要对用户说。

    动作块的口径从「角色此刻的动作或反应」改成「这一格实际发生的事」：
    用户用圆括号指定的剧情要落在动作块里，否则回合只剩角色摆姿势，
    场面全被挤进对白变成光说不做。实测（backend/tools/probe_paren_v3.py，
    两种子各六轮）：只改这一句，括号落实率从 0.25 升到 0.75。
    """
    content = preset_service.BUILTIN_SYSTEM_PROMPT
    round_req = preset_service.render_output_spec(preset_service.BUILTIN_OUTPUT_SPEC)
    # 2026-11-07 提示层审计后：动作与台词的口径从系统段挪到回合要求里，
    # 离生成点更近，系统段不再复述（复述会稀释规则）。
    assert "这一格实际发生的事" in round_req
    assert "最多 35 字" in round_req
    # 「台词是对用户说的」整句只留一处，不再出现三遍。
    assert round_req.count("台词是对用户说的") == 1
    # 角色归属口径留在系统段：照写用户指定的场面不算越位。
    # 「他」在提示里只指用户，不能拿来指代扮演的角色——角色可能是女性。
    # 原来写「不替用户安排他没做过的新动作」，模型会把「他」读成角色，
    # 身份识别跟着滑。角色一律写「扮演的角色」或名字。
    assert "不替用户安排对方没做过的新动作" in content
    assert "扮演角色卡里的角色" in content
    assert "你扮演角色卡里的角色" not in content
    assert "叙述用第三人称" in content
    assert "角色本人写" not in content
    # 自称的优先级：角色卡写了自称就照卡的，卡没写才按身份与礼制推。
    # 原来这条直接按身份与礼制定，压过卡里写明的自称：角色A卡里写「我」，
    # 实测六轮里五轮自称「本宫」。
    assert "角色卡写了自称就照那个自称" in content
    assert "没写才按身份与礼制推" in content
    assert "提到用户时写「你」或「对方」" in round_req
    # 场景长段不再写进系统提示：那一轮由场景推进轮负责。
    assert "不要每轮都写场景" not in content
    # 节制：锋芒的话不是每轮都有，也不要连着两轮同一种劲头。
    assert "带锋芒的话偶尔一句就够" in round_req
    assert "连着两轮都用同一种劲头" in round_req
    # 发散：慢慢发展，不要每轮都推进一步。
    assert "剧情慢慢发展" in content
    assert "不必每轮都往前推一大步" in content


def test_render_output_spec_narrows_blocks_per_auto_turn() -> None:
    """自动回合的回合要求只写该模式该写的块。"""
    spec = preset_service.BUILTIN_OUTPUT_SPEC
    text = preset_service.render_output_spec(spec, "continue")
    assert "【场景】" not in text
    assert "【动作】" in text
    assert "【对话】" in text
    text = preset_service.render_output_spec(spec, "describe")
    assert "【对话】" not in text
    assert "【场景】" in text
    # 普通回合的提示里不出现场景块，模型没有场景槽位可填。
    text = preset_service.render_output_spec(spec)
    assert "【场景】" not in text
    assert "【动作】" in text
    assert "【对话】" in text
    # 提示里不提场景块，也不提场景描写：提了就等于叫模型写它。
    assert "场景描写" not in text
    assert "写环境" not in text


def test_render_output_spec_has_no_length_and_free_structure() -> None:
    """回合要求不再写字数区间，块顺序自由，引号要求保留。

    字数下限对 4B 模型不可达：自然正文长度均值 129.6，18 次采样只有 16.7%
    能到 150 字，闸门重写也救不回来，却让耗时翻倍并造成界面「内容消失又重新生成」。
    """
    text = preset_service.render_output_spec(preset_service.BUILTIN_OUTPUT_SPEC)
    assert "150" not in text and "300" not in text
    # 回合总长不设上下限：长度交给模型自己判断，闸门不按总长重写。
    assert "正文合计不少于" not in text and "正文合计不超过" not in text
    # 但每块有自己的要求：对白块自己带下限与上限要求。
    assert "合计不少于 100 字" in text
    assert "最多 35 字" in text
    assert "顺序由你定" in text
    assert "状态块写在最后" in text
    assert "引号" in text
    assert "如果是闲聊" in text
    # 动作块与对白块这一轮必须都写，不再是「不需要的块可以不写」。
    assert "【动作】块与【对话】块都要写" in text
    assert "不需要的块可以不写" not in text


def test_render_output_spec_partial() -> None:
    """只声明上限时只渲染上限。"""
    text = preset_service.render_output_spec({"max_chars": 200})
    assert "200" in text
    assert "150" not in text


def test_render_output_spec_empty() -> None:
    """空 spec 渲染成空串或只剩开场句。"""
    assert preset_service.render_output_spec({}) == ""
    assert preset_service.render_output_spec({"min_chars": 0}) == "本次回复是一个完整回合。"


def test_assemble_appends_round_directive(data_dir: Path) -> None:
    """回合要求附在本轮输入末尾。"""
    preset_service.ensure_builtin_preset()
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="你还在地址U吗？")],
        stream=False,
        xw=XwOptions(),
    )
    ctx = assemble(request)
    user_message = ctx.messages[-1]
    assert user_message.role == "user"
    assert user_message.content.startswith("你还在地址U吗？")
    assert "顺序由你定" in user_message.content
    assert "150" not in user_message.content and "300" not in user_message.content


def test_assemble_without_output_spec_keeps_user_text(data_dir: Path) -> None:
    """没有 output_spec 的预设在装配时不追加内容。"""
    store.save_preset({"id": "p_plain", "name": "裸预设", "prompts": []})
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="你还在地址U吗？")],
        stream=False,
        xw=XwOptions(preset_id="p_plain"),
    )
    ctx = assemble(request)
    assert ctx.messages[-1].content == "你还在地址U吗？"
