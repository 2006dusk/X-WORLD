"""Quote normalisation: the model gets quote styles wrong in two directions.

A stray closing quote with no opening one, and an opening quote that was
meant to close the previous one. Both are fixed deterministically here.
"""

from __future__ import annotations

from app.inference.normalize import (
    close_unclosed_openers,
    drop_stray_closers,
    normalize_cjk_quotes,
)


def test_stray_closing_quote_is_dropped() -> None:
    """A closing quote with no opening quote goes away."""
    text = "她笑了一声。”然后走了。"
    cleaned, balance = drop_stray_closers(text)
    assert "”" not in cleaned
    assert balance == 0


def test_unclosed_opener_becomes_a_closer() -> None:
    """Two openers in a row: the first one was meant to be a closer."""
    text = "“今日？朕在此，还是……“她指尖顺着你的喉结往下滑"
    fixed = close_unclosed_openers(text)
    assert fixed == "“今日？朕在此，还是……”她指尖顺着你的喉结往下滑"
    _, balance = drop_stray_closers(fixed)
    assert balance == 0


def test_balanced_quotes_are_left_alone() -> None:
    """A well formed text is not touched."""
    text = "“今日？我这里都好。”她抬眼看他。"
    assert close_unclosed_openers(text) == text


def test_nested_brackets_are_not_treated_as_quotes() -> None:
    """『』 and 「」 are separate styles and stay as they are."""
    text = "「他说：『走吧。』」她转身。"
    assert close_unclosed_openers(text) == text


def test_normalize_cjk_quotes_fixes_english_quotes() -> None:
    """English double quotes become Chinese ones and stay balanced."""
    result = normalize_cjk_quotes('她说"走"，然后又"停下"。')
    assert result == "她说“走”，然后又“停下”。"


def test_normalize_cjk_quotes_handles_unclosed_opener() -> None:
    """The one shot path also closes the unmatched opener."""
    result = normalize_cjk_quotes("“甲……“乙")
    assert result == "“甲……”乙"
