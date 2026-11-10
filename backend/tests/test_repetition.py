"""复读检测测试。

覆盖：
- 按句切分保留句末标点；
- 上一轮的长句被新一轮逐字搬走时判定为复读；
- 短句与不同表述不算复读；
- 空输入不误判。
"""

from __future__ import annotations

from app.inference.repetition import (
    copied_fragment,
    copied_sentences,
    is_repetitive,
    split_sentences,
)

LONG_LINE = "突然，他猛地站起身，走到控制台前，拿起一个老旧的音箱，将音箱对准了来人"
OTHER_LINE = "雨点斜织，敲打着地址U的玻璃窗，灯光在水痕上碎成一片"


def test_split_sentences_keeps_punctuation() -> None:
    """按句末标点切句，标点保留在句尾。"""
    assert split_sentences("甲。乙！丙？") == ["甲。", "乙！", "丙？"]


def test_split_sentences_keeps_tail() -> None:
    """末尾没有句末标点的片段也成句。"""
    assert split_sentences("甲。乙") == ["甲。", "乙"]


def test_copied_sentence_detected() -> None:
    """上一轮的长句被新一轮逐字搬走时判为复读。"""
    previous = LONG_LINE + "。" + OTHER_LINE + "。"
    new = "我是见证者。" + LONG_LINE + "。"
    assert is_repetitive(new, previous)
    assert copied_sentences(new, previous) == [LONG_LINE]


def test_different_wording_not_repetitive() -> None:
    """表述不同不算复读。"""
    previous = LONG_LINE + "。"
    new = "他缓缓转过身，手指在旋钮上停了一瞬。"
    assert not is_repetitive(new, previous)


def test_short_sentence_not_repetitive() -> None:
    """短句照搬不算复读，避免把「好。」这类套话判进去。"""
    previous = "好。"
    new = "好。"
    assert not is_repetitive(new, previous)


def test_empty_inputs_not_repetitive() -> None:
    """空输入不误判。"""
    assert not is_repetitive("", LONG_LINE + "。")
    assert not is_repetitive(LONG_LINE + "。", "")


def test_copied_fragment_detected_when_head_changed() -> None:
    """上一轮的后半段被原样搬过来时判为复读：整句比对看不出来。

    实测（backend/tools/probe_story_run.py，四张卡各六回合）：角色上一轮说
    「抬起头来看着我，记住这张脸，从今天起……」，下一轮把后半段原样搬过来，
    只是前面换成了别的字。整句比对要求整句逐字出现，这种搬法会漏判。
    """
    previous = (
        "她指尖用力，金属环边缘狠狠压入你的皮肤，"
        "“抬起头来看着我，记住这张脸，从今天起，你身上除了我赏赐的滋味，"
        "不许沾染半分别的东西。”"
    )
    new = (
        "她看着被按住的你，嘴角的笑意愈发冰冷，"
        "“抬起头来看着我，记住这张脸，从今天起，你身上除了我赏赐的滋味，"
        "不许沾染半分别的东西。”"
    )
    assert not copied_sentences(new, previous)
    assert is_repetitive(new, previous)
    assert "抬起头来看着我" in copied_fragment(new, previous)


def test_short_shared_fragment_not_repetitive() -> None:
    """共享一小段不算复读，避免把套话判进去。"""
    previous = "她把茶盏搁在榻沿，指尖敲了敲床柱。"
    new = "她抬手推开窗扇，远处传来更鼓声。"
    assert not is_repetitive(new, previous)


def test_copied_fragment_threshold() -> None:
    """片段阈值是十八字：十七字不算，十八字才算。"""
    short = "甲" * 17
    long = "甲" * 18
    assert not is_repetitive("另一段" + short, "前面的话" + short + "后面的话")
    assert is_repetitive("另一段" + long, "前面的话" + long + "后面的话")
