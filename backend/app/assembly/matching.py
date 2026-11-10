"""世界书条目匹配。

规则：
- 关键词命中：条目 keys 按逗号分隔，逐一检测是否出现在文本中
- 支持正则（match_regex）与大小写敏感（case_sensitive）
- constant 常驻条目无条件命中
- selective 需二级关键词同时命中（keys 中的第二个词也需出现在文本中）
- 递归触发：注入条目后可触发其他条目，默认深度 2，可配置
- 结果按 priority 降序与 position 决定次序
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class MatchedEntry:
    """匹配命中的世界书条目。"""

    entry_id: str
    book_id: str
    content: str
    priority: int = 100
    position: str | None = None  # before / after / replace
    constant: bool = False
    selective: bool = False
    keys: list[str] = field(default_factory=list)
    recursion: bool = False

    def __hash__(self) -> int:
        return hash(self.entry_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MatchedEntry):
            return NotImplemented
        return self.entry_id == other.entry_id


def _check_keys(
    keys: list[str],
    text: str,
    *,
    match_regex: bool = False,
    case_sensitive: bool = False,
) -> bool:
    """检测 keys 是否命中 text。

    参数：
        keys: 关键词列表（逗号分隔后传入）。
        text: 待匹配文本。
        match_regex: 是否将 key 视为正则。
        case_sensitive: 是否大小写敏感。

    返回：
        是否命中。
    """
    if not keys:
        return False
    target = text if case_sensitive else text.lower()
    for key in keys:
        if not key:
            continue
        if not case_sensitive:
            key = key.lower()
        if match_regex:
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                if re.search(key, text, flags):
                    return True
            except re.error:
                # 正则无效时按普通字符串匹配
                if key in target:
                    return True
        else:
            if key in target:
                return True
    return False


def match_entries(
    entries: list[dict],
    text: str,
    *,
    max_depth: int = 2,
) -> list[MatchedEntry]:
    """对条目列表执行匹配，返回命中条目列表。

    参数：
        entries: 世界书条目列表，每项为 dict，至少包含：
            id、book_id、content、priority、keys（逗号分隔字符串）。
            可选：constant、selective、recursion、case_sensitive、match_regex、
            position。
        text: 待匹配文本（通常为用户输入 + 历史）。
        max_depth: 递归深度上限，默认 2。

    返回：
        命中条目列表，按 priority 降序排列，相同 priority 时 position
        为 before 的在前。

    匹配流程：
    1. 首轮：遍历所有条目，constant 无条件命中；其余按关键词匹配。
    2. 递归：将首轮命中条目内容拼接后，重新匹配尚未命中的条目，深度受
       max_depth 控制。
    3. 去重：同一条目只保留一次。
    """
    # 预处理条目为内部结构
    parsed: list[dict] = []
    for e in entries:
        keys_str = e.get("keys", "")
        keys = [k.strip() for k in keys_str.split(",") if k.strip()] if keys_str else []
        parsed.append({
            "id": e["id"],
            "book_id": e.get("book_id", e.get("book_id", "")),
            "content": e.get("content", ""),
            "priority": e.get("priority", 100),
            "position": e.get("position"),
            "constant": bool(e.get("constant", 0)),
            "selective": bool(e.get("selective", 0)),
            "recursion": bool(e.get("recursion", 0)),
            "case_sensitive": bool(e.get("case_sensitive", 0)),
            "match_regex": bool(e.get("match_regex", 0)),
            "keys": keys,
        })

    hit_ids: set[str] = set()
    results: list[MatchedEntry] = []

    def _match_round(entries_subset: list[dict], current_text: str) -> list[MatchedEntry]:
        new_hits: list[MatchedEntry] = []
        for e in entries_subset:
            eid = e["id"]
            if eid in hit_ids:
                continue

            if e["constant"]:
                # 常驻条目无条件命中
                hit_ids.add(eid)
                new_hits.append(_to_matched(e))
                continue

            if not e["keys"]:
                continue

            # 常规关键词匹配
            if _check_keys(
                e["keys"],
                current_text,
                match_regex=e["match_regex"],
                case_sensitive=e["case_sensitive"],
            ):
                # selective: 需要二级关键词同时命中
                if e["selective"] and len(e["keys"]) >= 2:
                    second_keys = e["keys"][1:]
                    if not _check_keys(
                        second_keys,
                        current_text,
                        match_regex=e["match_regex"],
                        case_sensitive=e["case_sensitive"],
                    ):
                        continue
                hit_ids.add(eid)
                new_hits.append(_to_matched(e))
        return new_hits

    # 首轮匹配
    first_round = _match_round(parsed, text)
    results.extend(first_round)

    # 递归匹配
    if first_round:
        combined_hit_text = "\n".join(h.content for h in first_round)
        for depth in range(1, max_depth):
            next_round = _match_round(parsed, text + "\n" + combined_hit_text)
            if not next_round:
                break
            results.extend(next_round)
            combined_hit_text = "\n".join(h.content for h in next_round)

    # 排序：priority 降序，position 为 before 的排前
    def _sort_key(h: MatchedEntry) -> tuple:
        pos_order = 0
        if h.position == "before":
            pos_order = 0
        elif h.position == "after":
            pos_order = 2
        else:
            pos_order = 1
        return (-h.priority, pos_order)

    results.sort(key=_sort_key)
    return results


def _to_matched(e: dict) -> MatchedEntry:
    """将内部条目字典转为 MatchedEntry。"""
    return MatchedEntry(
        entry_id=e["id"],
        book_id=e["book_id"],
        content=e["content"],
        priority=e["priority"],
        position=e.get("position"),
        constant=e["constant"],
        selective=e["selective"],
        keys=e["keys"],
        recursion=e["recursion"],
    )