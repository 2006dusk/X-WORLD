"""Token 预算裁剪。

规则：超预算时按段名逆序裁剪，顺序写在 TRIM_ORDER 里。
- system 与 character 不裁剪。
- history：优先丢弃最早轮次。
- world_info：按 priority 从低到高丢弃。
- memory：按重要度从低到高丢弃。
- preset_prompts：丢弃尾部条目。
- persona 与 supporting：整段清空。
- 裁剪后置 truncated=True。

提供可单测的纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# 裁剪顺序：从最该丢的丢起。按段名而不是下标：段集合会随功能增减
# （新增配角段），按下标定位会错位裁错段。
TRIM_ORDER: tuple[str, ...] = (
    "history",
    "memory",
    "world_info",
    "post_history",
    "preset_prompts",
    "persona",
    "supporting",
)


@dataclass
class SegmentData:
    """用于裁剪的段数据结构。"""

    name: str  # 段名：system/character/preset_prompts/post_history/world_info/memory/history/user
    items: list[dict] = field(default_factory=list)  # 段内子条目
    tokens: int = 0  # 当前 token 数
    fixed: bool = False  # 不可裁剪

    def total(self) -> int:
        return self.tokens


def _segment_by_name(segments: list[SegmentData], name: str) -> SegmentData | None:
    """按段名取段。段集合会增减，按下标定位会错位。"""
    for seg in segments:
        if seg.name == name:
            return seg
    return None


def _compute_segment_tokens(segments: list[SegmentData], token_counter) -> int:
    """使用 token_counter 重新计算各段 token 并返回总量。"""
    total = 0
    for seg in segments:
        text = _segment_text(seg)
        seg.tokens = token_counter(text)
        total += seg.tokens
    return total


def _segment_text(seg: SegmentData) -> str:
    """拼接段内所有 item 的文本。"""
    parts: list[str] = []
    for item in seg.items:
        parts.append(item.get("content", ""))
    return "\n".join(parts)


def trim_to_budget(
    segments: list[SegmentData],
    token_limit: int,
    token_counter,
) -> tuple[list[SegmentData], bool]:
    """按预算裁剪段集合。

    裁剪顺序见 TRIM_ORDER，按段名定位，不依赖段在集合里的位置。

    参数：
        segments: 按顺序排列的段集合。
        token_limit: token 上限。
        token_counter: Tokenizer 协议或可调用对象 count(text) -> int。

    返回：
        (裁剪后的 segments, truncated)。truncated 为 True 表示发生过裁剪。

    裁剪策略：
    - system 与 character 不裁剪（fixed=True）。
    - 从第 7 段（history）开始逆序裁剪，直到总量不超过 token_limit。
    - history 段：丢弃最早轮次（item 列表头部）。
    - world_info 段：按 priority 升序丢弃（低 priority 先丢）。
    - memory 段：按 importance 升序丢弃（低重要度先丢）。
    - preset_prompts 段：丢弃尾部 item。
    - post_history 段：丢弃。
    """
    truncated = False

    # 先计算当前总 tokens
    total = _compute_segment_tokens(segments, token_counter)

    if total <= token_limit:
        return segments, False

    truncated = True

    # 各段裁剪策略，顺序见 TRIM_ORDER
    for name in TRIM_ORDER:
        if total <= token_limit:
            break
        seg = _segment_by_name(segments, name)
        if seg is None or seg.fixed or not seg.items:
            continue

        total = _trim_segment(segments, seg, token_limit, token_counter)
        if total is None:
            # _trim_segment 原地修改了 seg，需要重算总量
            total = _compute_segment_tokens(segments, token_counter)
        if total <= token_limit:
            break

    # 如果仍然超预算，强制清空非固定段
    if total > token_limit:
        for name in TRIM_ORDER:
            seg = _segment_by_name(segments, name)
            if seg is None or seg.fixed or not seg.items:
                continue
            seg.items.clear()
            seg.tokens = 0
            total = _compute_segment_tokens(segments, token_counter)
            if total <= token_limit:
                break

    return segments, truncated


def _trim_segment(
    segments: list[SegmentData],
    seg: SegmentData,
    token_limit: int,
    token_counter,
) -> int | None:
    """对单个段执行裁剪，返回裁剪后的总量。原地修改 seg。

    返回 None 表示调用者需重算总量。
    """
    name = seg.name

    if name == "history":
        return _trim_history(segments, seg, token_limit, token_counter)
    elif name == "world_info":
        return _trim_world_info(segments, seg, token_limit, token_counter)
    elif name == "memory":
        return _trim_memory(segments, seg, token_limit, token_counter)
    elif name in ("preset_prompts",):
        return _trim_preset_prompts(segments, seg, token_limit, token_counter)
    elif name == "post_history":
        return _trim_post_history(segments, seg, token_limit, token_counter)
    elif name in ("persona", "supporting"):
        return _trim_whole(segments, seg, token_limit, token_counter)
    return None


def _trim_history(
    segments: list[SegmentData],
    seg: SegmentData,
    token_limit: int,
    token_counter,
) -> int:
    """history 段：丢弃最早轮次（从头部移除）。"""
    while seg.items and _compute_segment_tokens(segments, token_counter) > token_limit:
        # 丢弃第一个 item（最早轮次）
        seg.items.pop(0)
        if not seg.items:
            break
        # 重算本段 tokens
        seg.tokens = token_counter(_segment_text(seg))
    return _compute_segment_tokens(segments, token_counter)


def _trim_world_info(
    segments: list[SegmentData],
    seg: SegmentData,
    token_limit: int,
    token_counter,
) -> int:
    """world_info 段：按 priority 升序丢弃（低 priority 先丢）。"""
    while seg.items and _compute_segment_tokens(segments, token_counter) > token_limit:
        # 找最低 priority 的 item
        min_idx = 0
        min_priority = float("inf")
        for i, item in enumerate(seg.items):
            p = item.get("priority", 100)
            if p < min_priority:
                min_priority = p
                min_idx = i
        seg.items.pop(min_idx)
        if not seg.items:
            break
        seg.tokens = token_counter(_segment_text(seg))
    return _compute_segment_tokens(segments, token_counter)


def _trim_memory(
    segments: list[SegmentData],
    seg: SegmentData,
    token_limit: int,
    token_counter,
) -> int:
    """memory 段：按 importance 升序丢弃（低重要度先丢）。"""
    while seg.items and _compute_segment_tokens(segments, token_counter) > token_limit:
        min_idx = 0
        min_importance = float("inf")
        for i, item in enumerate(seg.items):
            imp = item.get("importance", 0.5)
            if imp < min_importance:
                min_importance = imp
                min_idx = i
        seg.items.pop(min_idx)
        if not seg.items:
            break
        seg.tokens = token_counter(_segment_text(seg))
    return _compute_segment_tokens(segments, token_counter)


def _trim_preset_prompts(
    segments: list[SegmentData],
    seg: SegmentData,
    token_limit: int,
    token_counter,
) -> int:
    """preset_prompts 段：丢弃尾部 item。"""
    while seg.items and _compute_segment_tokens(segments, token_counter) > token_limit:
        seg.items.pop()
        if not seg.items:
            break
        seg.tokens = token_counter(_segment_text(seg))
    return _compute_segment_tokens(segments, token_counter)


def _trim_whole(
    segments: list[SegmentData],
    seg: SegmentData,
    token_limit: int,
    token_counter,
) -> int:
    """persona 段与 supporting 段：整段清空。"""
    seg.items.clear()
    seg.tokens = 0
    return _compute_segment_tokens(segments, token_counter)


def _trim_post_history(
    segments: list[SegmentData],
    seg: SegmentData,
    token_limit: int,
    token_counter,
) -> int:
    """post_history 段：直接清空。"""
    seg.items.clear()
    seg.tokens = 0
    return _compute_segment_tokens(segments, token_counter)