"""预算裁剪测试。

覆盖第 6 节预算规则：
- system 与 character 不裁剪；
- 超预算时按第 7 至第 3 段逆序裁剪；
- history 丢最早轮次、world_info 按 priority、memory 按 importance、
  preset_prompts 丢尾部、post_history 清空；
- 裁剪后 truncated=True。
"""

from __future__ import annotations

from app.assembly.budget import SegmentData, trim_to_budget


def _counter(text: str) -> int:
    """每字符计 1 token 的确定性计数器。"""
    return len(text)


def _segments() -> list[SegmentData]:
    return [
        SegmentData(name="system", items=[{"content": "S" * 10}], fixed=True),
        SegmentData(name="character", items=[{"content": "C" * 10}], fixed=True),
        SegmentData(
            name="preset_prompts",
            items=[{"content": "P" * 10}, {"content": "Q" * 10}],
        ),
        SegmentData(name="post_history", items=[{"content": "H" * 10}]),
        SegmentData(
            name="world_info",
            items=[
                {"content": "W" * 10, "priority": 50, "entry_id": "e1"},
                {"content": "X" * 10, "priority": 200, "entry_id": "e2"},
            ],
        ),
        SegmentData(
            name="memory",
            items=[
                {"content": "M" * 10, "importance": 0.2},
                {"content": "N" * 10, "importance": 0.9},
            ],
        ),
        SegmentData(
            name="history",
            items=[{"role": "user", "content": "A" * 10}],
        ),
        SegmentData(name="user", items=[{"content": "U" * 10}]),
    ]


def test_within_budget_untouched() -> None:
    """预算内不裁剪。"""
    segs = _segments()
    result, truncated = trim_to_budget(segs, 1000, _counter)
    assert truncated is False
    assert [s.name for s in result] == [
        "system", "character", "preset_prompts", "post_history",
        "world_info", "memory", "history", "user",
    ]


def test_fixed_segments_never_trimmed() -> None:
    """system 与 character 不裁剪。"""
    segs = _segments()
    result, truncated = trim_to_budget(segs, 30, _counter)
    assert truncated is True
    by_name = {s.name: s for s in result}
    assert by_name["system"].tokens == 10
    assert by_name["character"].tokens == 10
    assert by_name["post_history"].items == []
    assert by_name["preset_prompts"].items == []
    assert by_name["world_info"].items == []
    assert by_name["memory"].items == []
    assert by_name["history"].items == []


def test_low_priority_world_info_dropped_first() -> None:
    """world_info 先丢低 priority。"""
    segs = _segments()
    # 总量 100，限 80：需从逆序裁剪，history(10) + memory(20) + world_info
    result, _ = trim_to_budget(segs, 80, _counter)
    by_name = {s.name: s for s in result}
    wi_ids = [i.get("entry_id") for i in by_name["world_info"].items]
    assert "e1" not in wi_ids


def test_low_importance_memory_dropped_first() -> None:
    """memory 先丢低 importance。"""
    segs = _segments()
    result, _ = trim_to_budget(segs, 70, _counter)
    by_name = {s.name: s for s in result}
    contents = [i.get("content") for i in by_name["memory"].items]
    assert "M" * 10 not in contents


def test_history_drops_earliest_first() -> None:
    """history 从最早轮次开始丢弃。"""
    segs = _segments()
    segs[6].items = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "new"},
    ]
    result, _ = trim_to_budget(segs, 95, _counter)
    by_name = {s.name: s for s in result}
    contents = [i.get("content") for i in by_name["history"].items]
    assert "old" not in contents


def test_preset_prompts_trim_from_tail() -> None:
    """preset_prompts 丢尾部。"""
    segs = _segments()
    result, _ = trim_to_budget(segs, 45, _counter)
    by_name = {s.name: s for s in result}
    contents = [i.get("content") for i in by_name["preset_prompts"].items]
    assert "P" * 10 in contents
    assert "Q" * 10 not in contents
