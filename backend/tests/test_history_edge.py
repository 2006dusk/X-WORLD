"""历史整形边界测试：场景截断与角色回复压缩。

覆盖 app.assembly.pipeline 里三个新增件的边界行为：
- _truncate_scene：未超上限原样返回（幂等）；超上限切在最后一个句末标点之后且
  不超过上限；整段没有句末标点时退化为硬切前 limit 个字符（长度正好等于 limit）。
- _history_reply：blocks 缺失时退回 _last_sentence；只有 scene 没有 dialogue 时
  只返回 scene（不夹多余换行、不出现 "None"）；长 scene 被截断，对白只留最后一句。
- _shape_history：整形后输出字典不再残留 "blocks" 键（user 行与 assistant 行都查）。

数据目录一律用 tmp_path + XWORLD_DATA_DIR 隔离，不碰真实用户数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import store
from app.assembly.pipeline import (
    HISTORY_DIALOGUE_CHARS,
    HISTORY_SCENE_CHARS,
    NO_SPEECH_MARKER,
    _history_reply,
    _shape_history,
    _truncate_scene,
)

CARD_ID = "c_edge_001"
PRESET_ID = "p_edge_001"


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，并预置角色卡与预设。

    _shape_history 会读 settings 里的 history_policy，仓库里也没有全局 conftest
    做隔离，所以这里同样把数据根目录钉到 tmp_path。
    """
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
            },
        },
    )
    store.write_json(
        store.presets_dir() / f"{PRESET_ID}.json",
        {"id": PRESET_ID, "name": "测试预设", "prompts": []},
    )
    return tmp_path


def test_truncate_scene_keeps_short_scene(data_dir: Path) -> None:
    """长度不超上限时原样返回，且再截一次结果不变（幂等）。"""
    scene = "他把伞收进门口的架子上，回头看了一眼窗外。"

    once = _truncate_scene(scene, HISTORY_SCENE_CHARS)
    assert once == scene
    assert _truncate_scene(once, HISTORY_SCENE_CHARS) == scene


def test_truncate_scene_cuts_after_sentence_end(data_dir: Path) -> None:
    """超上限时切在句末标点之后：长度不超上限且以「。」结尾。"""
    scene = "他站在巷口，看着雨顺着屋檐往下淌。" * 20
    assert len(scene) > HISTORY_SCENE_CHARS

    result = _truncate_scene(scene, HISTORY_SCENE_CHARS)

    assert len(result) <= HISTORY_SCENE_CHARS
    assert result.endswith("。")
    # 切点在句末，不能把句子拦腰断开：结果必须是原文的前缀
    assert scene.startswith(result)


def test_truncate_scene_without_sentence_end_hard_cuts(data_dir: Path) -> None:
    """整段没有句末标点时返回前 limit 个字符，长度正好等于 limit。"""
    limit = 40
    scene = "看着雨" * 200
    assert not any(char in "。！？" for char in scene)

    result = _truncate_scene(scene, limit)

    assert len(result) == limit
    assert result == scene[:limit]


def test_history_reply_falls_back_without_blocks(data_dir: Path) -> None:
    """没有台词可留时只留固定标记。

    角色回复的叙述文本不进历史：模型会把自己写过的句子当素材，顺着自己的
    话走而不回应当前输入。实测见 backend/tools/probe_logic_turn.py。
    """
    content = "他先走进店里。接着把伞收好。最后他坐下。"
    long_content = "他先走进店里，抖掉外套上的水。接着把伞收好。最后他靠在吧台边坐下。"

    # 有无长短内容都只留标记，不留模型自己写的句子
    assert _history_reply(content, None) == NO_SPEECH_MARKER
    assert _history_reply(long_content, None) == NO_SPEECH_MARKER
    assert chr(10) not in NO_SPEECH_MARKER
    assert "None" not in NO_SPEECH_MARKER


def test_history_reply_keeps_dialogue_only(data_dir: Path) -> None:
    """历史里只留台词，不留场景叙述，也不逐轮重复状态。"""
    scene = "雨还在下，他披着外套站在门口。"
    content = "雨还在下，他披着外套站在门口。他说：“走吧。”"
    state = (
        "当前位置：地址N_地址V-内"
        + chr(10)
        + "人物位置：靠窗"
        + chr(10)
        + "人物物品：防水外套、钱包"
        + chr(10)
        + "场景物品：吧台、咖啡机"
        + chr(10)
        + "地图：地址N_地址U"
        + chr(10)
        + "时间：0625"
    )

    result = _history_reply(content, {"scene": scene, "dialogue": "", "state": state})

    # 分块里没有台词时，从正文里的引号切出台词
    assert "走吧" in result
    # 场景叙述不进历史
    assert "雨还在下" not in result
    # 状态不逐轮重复，它已经在 memory 段整体注入
    assert "当前位置：地址N_地址V-内" not in result


def test_history_reply_caps_long_dialogue(data_dir: Path) -> None:
    """台词超长时只留末尾，场景叙述仍然不进历史。"""
    scene = "他沿着河堤往前走，风把外套下摆吹得贴在腿上。" * 20
    dialogue = "第一句对白不该出现。" * 30 + "你确定要走吗？"
    state = "当前位置：地址N_地址U" + chr(10) + "时间：0625"

    result = _history_reply(
        scene + chr(10) + dialogue,
        {"scene": scene, "dialogue": dialogue, "state": state},
    )

    assert len(result) <= HISTORY_DIALOGUE_CHARS
    # 末尾是最新一句话，必须留着
    assert result.endswith("你确定要走吗？")
    # 场景叙述不进历史
    assert "河堤" not in result
    # 状态不逐轮重复
    assert "当前位置：地址N_地址U" not in result


def test_shape_history_drops_blocks_for_user_and_assistant(data_dir: Path) -> None:
    """整形后 user 行与 assistant 行都不再残留 "blocks" 键。"""
    scene = "雨滴敲打着地址V的玻璃窗，他披着防水外套走进店里。"
    dialogue = "他低声说：外面下雨了。"
    items = [
        {
            "role": "user",
            "content": "我推门进去。",
            "blocks": {"scene": "门口的铃铛响了一声。", "dialogue": "我推门进去。"},
        },
        {
            "role": "assistant",
            "content": scene + chr(10) + dialogue,
            "blocks": {"scene": scene, "dialogue": dialogue},
        },
    ]

    shaped = _shape_history(items)

    assert len(shaped) == 2
    for row in shaped:
        assert "blocks" not in row
    assert shaped[0]["role"] == "user"
    assert shaped[0]["content"] == "我推门进去。"
    # assistant 行只留台词
    assert shaped[1]["content"] == dialogue