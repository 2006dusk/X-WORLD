"""历史整形与角色卡注入测试。

覆盖：
- 角色卡不再注入 mes_example（示例对话会被模型逐字搬走）；
- 历史只保留最近若干轮，轮数可用设置覆盖；
- 历史里的角色回复保留场景、对白压成最后一句；
- 没有分块信息的旧数据退回整句压缩；
- 历史开头是角色回复时整条丢弃。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import store
from app.assembly.pipeline import NO_SPEECH_MARKER, assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions

CARD_ID = "c_shape_001"
PRESET_ID = "p_shape_001"
SESSION_ID = "s_shape_001"


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，并预置角色卡与预设。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))

    store.write_json(
        store.card_dir(CARD_ID) / "card.json",
        {
            "id": CARD_ID,
            "name": "角色样本",
            "data": {
                "name": "角色样本",
                "description": "描述文本",
                "personality": "人格文本",
                "scenario": "场景文本",
                "mes_example": "示例对话：他把推子推回原位。",
            },
        },
    )
    store.write_json(
        store.presets_dir() / f"{PRESET_ID}.json",
        {"id": PRESET_ID, "name": "测试预设", "prompts": []},
    )
    return tmp_path


def _request() -> ChatRequest:
    """带会话的装配请求。"""
    return ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="继续")],
        session=SESSION_ID,
        stream=False,
        xw=XwOptions(
            character_id=CARD_ID,
            preset_id=PRESET_ID,
            world_book_ids=[],
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )


def _append_round(index: int, assistant_text: str) -> None:
    """往会话流水里写一轮问答。"""
    store.append_message(SESSION_ID, CARD_ID, {"role": "user", "content": f"第{index}句"})
    store.append_message(SESSION_ID, CARD_ID, {"role": "assistant", "content": assistant_text})


def _append_structured_round(index: int, scene: str, dialogue: str) -> None:
    """往会话流水里写一轮带分块的角色回复。"""
    store.append_message(SESSION_ID, CARD_ID, {"role": "user", "content": f"第{index}句"})
    store.append_message(
        SESSION_ID,
        CARD_ID,
        {
            "role": "assistant",
            "content": scene + chr(10) + dialogue,
            "blocks": {
                "scene": scene,
                "dialogue": dialogue,
                "state": "当前位置：地址U二楼" + chr(10) + "时间：雨夜",
            },
        },
    )


def test_character_segment_excludes_mes_example(data_dir: Path) -> None:
    """角色卡的示例对话不进 system 段。"""
    ctx = assemble(_request())
    system = ctx.messages[0].content
    assert "描述文本" in system
    # 场景由世界书的地区条目提供，角色卡里的场景不进 system 段。
    assert "场景文本" not in system
    assert "示例对话" not in system
    assert "推子" not in system


def test_history_keeps_recent_turns_only(data_dir: Path) -> None:
    """历史只保留最近若干轮，默认四轮。

    保留轮数从两轮提到四轮：两轮时模型只剩一句上文，场景靠状态块猜，
    用户的这一轮输入常被当成剧情素材而不是要回应的话。
    """
    for index in range(1, 6):
        _append_round(index, f"第{index}轮回复。")
    ctx = assemble(_request())
    # 去掉 system 段与本轮输入，剩下的才是历史
    history = ctx.messages[1:-1]
    # 五轮里只留最近四轮：八条消息，起点是第二轮的用户输入
    assert len(history) == 8
    assert history[0].content == "第2句"
    # 角色回复只留台词，没有台词时留固定标记
    assert history[1].content == NO_SPEECH_MARKER


def test_assistant_history_without_dialogue_uses_marker(data_dir: Path) -> None:
    """没有台词的角色回复只留固定标记，不留模型自己写的句子。"""
    _append_round(1, "第一句。第二句。第三句。")
    ctx = assemble(_request())
    assistant = [m for m in ctx.messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == NO_SPEECH_MARKER
    assert "第三句" not in assistant[0].content


def test_assistant_history_keeps_dialogue_only(data_dir: Path) -> None:
    """分块回合在历史里只留台词，不留场景叙述。

    场景叙述是模型会照搬的素材，进历史就会顺着自己的话走；台词必须留下，
    否则对话线索断掉，依赖上文的句子会答不上来。实测见
    backend/tools/probe_logic_turn.py。
    """
    _append_structured_round(
        1,
        "雨滴敲打着地址V的玻璃窗，他披着防水外套走进店里。",
        "他低声说：“外面下雨了。”\n“你确定要走吗？”",
    )
    ctx = assemble(_request())
    assistant = [m for m in ctx.messages if m.role == "assistant"]
    assert len(assistant) == 1
    content = assistant[0].content
    # 台词留下：模型据此知道自己说过什么
    assert "外面下雨了" in content
    assert "你确定要走吗" in content
    # 场景叙述不进历史
    assert "雨滴敲打着地址V的玻璃窗" not in content
    # 状态不逐轮重复，它已经在 memory 段整体注入
    assert "当前位置：地址U二楼" not in content


def test_assistant_history_drops_long_scene(data_dir: Path) -> None:
    """超长场景不进历史，历史不会因此膨胀。"""
    scene = "他站在巷口" + "看着雨" * 200
    _append_structured_round(1, scene, "他说：“走吧。”")
    ctx = assemble(_request())
    assistant = [m for m in ctx.messages if m.role == "assistant"]
    assert "看着雨" not in assistant[0].content
    assert "走吧" in assistant[0].content
    assert len(assistant[0].content) < 200


def test_assistant_history_without_punctuation_uses_marker(data_dir: Path) -> None:
    """没有台词时同样只留固定标记。"""
    _append_round(1, "没有标点的一段很长很长的话")
    ctx = assemble(_request())
    assistant = [m for m in ctx.messages if m.role == "assistant"]
    assert assistant[0].content == NO_SPEECH_MARKER


def test_legacy_reply_without_blocks_keeps_quoted_line(data_dir: Path) -> None:
    """旧落盘数据没有分块信息，靠引号把台词切出来保留。"""
    store.append_message(SESSION_ID, CARD_ID, {"role": "user", "content": "第1句"})
    store.append_message(
        SESSION_ID,
        CARD_ID,
        {"role": "assistant", "content": "雨点敲在窗上。他轻声说：“我在这儿。”"},
    )
    ctx = assemble(_request())
    assistant = [m for m in ctx.messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert "我在这儿" in assistant[0].content
    assert "雨点敲在窗上" not in assistant[0].content


def test_leading_assistant_history_dropped(data_dir: Path) -> None:
    """历史开头是角色回复时整条丢弃。"""
    store.append_message(SESSION_ID, CARD_ID, {"role": "assistant", "content": "残留回复。"})
    _append_round(1, "正常回复。")
    ctx = assemble(_request())
    history = ctx.messages[1:-1]
    assert history[0].role == "user"


def test_history_policy_setting_overrides_keep_turns(data_dir: Path) -> None:
    """保留轮数可用设置覆盖。"""
    store.set_setting("history_policy", {"keep_turns": 1})
    for index in range(1, 4):
        _append_round(index, f"第{index}轮回复。")
    ctx = assemble(_request())
    history = ctx.messages[1:-1]
    assert len(history) == 2
    assert history[0].content == "第3句"
