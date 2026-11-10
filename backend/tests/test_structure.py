"""结构化回合分块解析测试。

覆盖：标记拆分、缺失标记的容错、流式分块器（含标记被切断）与展示标签。
"""

from __future__ import annotations

from app.inference.structure import (
    StructureSplitter,
    block_entry,
    block_names,
    display_labels,
    split_blocks,
    strip_labels,
)

SCENE = "地址U在二楼，台灯是唯一的光源。"
DIALOGUE = "他压低声音：“你终于来了。”"
RAW = "【场景】" + SCENE + chr(10) + "【对话】" + DIALOGUE
NAMES = ["scene", "dialogue"]


def test_block_names_filters_unknown_and_dedup() -> None:
    """块名只保留已定义的，按声明顺序去重。"""
    assert block_names(["scene", "dialogue"]) == ["scene", "dialogue"]
    assert block_names(["scene", "scene", "nope"]) == ["scene"]
    assert block_names("scene") == []
    assert block_names(None) == []


def test_split_blocks_reads_two_blocks() -> None:
    """两块齐全时按标记拆开。"""
    blocks = split_blocks(RAW, NAMES)
    assert blocks["scene"] == SCENE
    assert blocks["dialogue"] == DIALOGUE


def test_split_blocks_tolerates_missing_marker() -> None:
    """缺标记时按容错口径归位。"""
    only_dialogue = "【对话】" + DIALOGUE
    blocks = split_blocks(only_dialogue, NAMES)
    assert blocks["scene"] == ""
    assert blocks["dialogue"] == DIALOGUE

    no_marker = split_blocks("只有一段正文。", NAMES)
    assert no_marker["scene"] == "只有一段正文。"
    assert no_marker["dialogue"] == ""


def test_split_blocks_keeps_text_before_first_marker() -> None:
    """第一个标记之前的文字算第一块。"""
    blocks = split_blocks("开头一段。" + RAW, NAMES)
    assert blocks["scene"] == "开头一段。" + SCENE
    assert blocks["dialogue"] == DIALOGUE


def test_strip_labels_removes_markers() -> None:
    """去标记后只留正文。"""
    assert strip_labels(RAW, NAMES) == SCENE + chr(10) + DIALOGUE


def test_display_labels_drop_brackets() -> None:
    """展示标签去掉标记的括号。"""
    assert display_labels(NAMES) == {"scene": "场景", "dialogue": "对话"}


def test_action_block_is_defined() -> None:
    """动作块是独立的一块，与场景块分开。

    动作块写角色这一轮做了什么，场景块写此刻周围的环境。
    两块混用时模型每轮先写一段景物，回合就退成描述。
    """
    marker, hint = block_entry("action")
    assert marker == "【动作】"
    # 实测（backend/tools/probe_action_beat.py，五个写法各六轮）：
    # 一句话加字数上限的动作块，环境词 0/6、均长 27 字；
    # 写成「一到两句」不限字数时均长 70 字，八轮里五轮混进环境词。
    assert "一句话" in hint
    assert "最多 35 字" in hint
    assert "环境词" in hint
    # 动作块写这一格实际发生的事：用户指定的剧情要落在这一块里，
    # 用户身上发生的事用「你」或「对方」写。
    # 实测（backend/tools/probe_paren_v3.py）：括号落实率 0.25 升到 0.75。
    assert "写这一格实际发生的事" in hint
    assert "「你」或「对方」" in hint
    assert block_names(["action", "scene", "dialogue", "state"]) == [
        "action",
        "scene",
        "dialogue",
        "state",
    ]
    assert display_labels(["action", "dialogue"]) == {
        "action": "动作",
        "dialogue": "对话",
    }


def test_block_entry_switches_hint_by_mode() -> None:
    """对话推进轮的台词提示换成「接着上一轮往下说」。

    用户这一轮没有说话，提示不能要求「回应用户这一轮」，
    但仍然要对用户说，不能写成自言自语。
    """
    normal_marker, normal_hint = block_entry("dialogue")
    continue_marker, continue_hint = block_entry("dialogue", "continue")
    assert normal_marker == continue_marker == "【对话】"
    assert "台词只说此刻角色会对用户说出口的话" in normal_hint
    assert "用户没有说话" in continue_hint
    assert "对用户说" in continue_hint
    # 动作块两种模式一致。
    assert block_entry("action") == block_entry("action", "continue")


def test_splitter_reassembles_char_by_char() -> None:
    """逐字喂入时切分结果与一次性解析一致。"""
    splitter = StructureSplitter(NAMES)
    pieces: list[tuple[str, str]] = []
    for char in RAW:
        pieces.extend(splitter.feed(char))
    pieces.extend(splitter.flush())

    scene = "".join(text for name, text in pieces if name == "scene")
    dialogue = "".join(text for name, text in pieces if name == "dialogue")
    assert scene == SCENE
    assert dialogue == DIALOGUE
    assert "【" not in "".join(text for _, text in pieces)


def test_splitter_handles_marker_cut_in_middle() -> None:
    """标记被切断时不会误切块。"""
    splitter = StructureSplitter(NAMES)
    pieces = splitter.feed("【场")
    assert pieces == []
    pieces = splitter.feed("景】正文")
    assert pieces == [("scene", "正文")]


def test_splitter_uses_first_block_before_any_marker() -> None:
    """还没遇到标记时归第一块。"""
    splitter = StructureSplitter(NAMES)
    assert splitter.feed("先写点东西") == [("scene", "先写点东西")]
