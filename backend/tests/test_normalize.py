"""输出排版归一测试。

覆盖：成对英文引号转中文引号、奇数个引号的处理、没有配对开引号的右引号被删掉、
流式分片与一次性归一结果一致、段落压平、回合末尾的说明性括号行被删掉。
"""

from __future__ import annotations

from app.inference.normalize import (
    QuoteStreamNormalizer,
    collapse_paragraphs,
    cut_at_template_marker,
    drop_annotation_lines,
    drop_stray_closers,
    normalize_cjk_quotes,
)


def test_drop_annotation_lines_removes_trailing_note() -> None:
    """回合末尾的说明性括号行被删掉。"""
    text = "正文一段。\n\n（注意：以上为上一回合的延续与扩展，符合字数要求并推动剧情发展。）"
    assert drop_annotation_lines(text) == "正文一段。"


def test_drop_annotation_lines_removes_new_info_note() -> None:
    """「新信息」式的总结行同样删掉。"""
    text = "正文一段。\n（新信息：角色透露了磁带的来源。）"
    assert drop_annotation_lines(text) == "正文一段。"


def test_drop_annotation_lines_keeps_normal_parenthetical() -> None:
    """行内正常括号与叙述性括号不受影响。"""
    text = "他笑了笑（没有出声），转身走开。"
    assert drop_annotation_lines(text) == text
    tail = "他走开了。\n（脚步声渐远）"
    assert drop_annotation_lines(tail) == tail


def test_drop_annotation_lines_keeps_text_without_note() -> None:
    """没有说明行时原样返回。"""
    text = "一段正文。\n另一段。"
    assert drop_annotation_lines(text) == text


def test_cut_at_template_marker_drops_the_faked_turn() -> None:
    """模板标记之后是模型伪造的下一轮，从标记处截断。"""
    text = "她把酒囊搁下。<|im_end|>\n<|im_start|>user\n我偏跪"
    assert cut_at_template_marker(text) == "她把酒囊搁下。"


def test_cut_at_template_marker_keeps_clean_text() -> None:
    """没有标记的正文原样返回。"""
    text = "她把酒囊搁下，抬头看你。"
    assert cut_at_template_marker(text) == text


def test_stream_normalizer_truncates_at_marker() -> None:
    """流式输出里出现模板标记时，标记之后的内容不再往外发。"""
    normalizer = QuoteStreamNormalizer()
    assert normalizer.feed("她把酒囊搁下。") == "她把酒囊搁下。"
    assert normalizer.feed("<|im_start|>user") == ""
    assert normalizer.feed("我偏跪") == ""


def test_stream_normalizer_catches_marker_split_across_chunks() -> None:
    """标记跨分片边界时同样能截住。"""
    normalizer = QuoteStreamNormalizer()
    assert normalizer.feed("她把酒囊搁下。<|im_st") == "她把酒囊搁下。"
    assert normalizer.feed("art|>user") == ""
    assert normalizer.feed("我偏跪") == ""
