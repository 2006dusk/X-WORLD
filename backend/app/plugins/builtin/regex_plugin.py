"""内置正则插件。

规则来自预设 JSON 的 ``regex_rules`` 字段，规则项形状：

```json
{
  "name": "去掉旁白括号",
  "pattern": "（[^）]*）",
  "replacement": "",
  "target": "output",
  "trim": true
}
```

字段约定：

- name：规则名，仅用于日志与回执。
- pattern：Python 正则表达式；非法正则被跳过并记入 issues，不中断其它规则。
- replacement：替换模板，支持反斜杠分组引用。
- target：``input`` 应用于用户输入消息，``output`` 应用于模型输出。
- trim：为真时，删除匹配后一并去掉匹配处残留的空白，并把因此产生的连续空行压成一空行。

规则解析顺序：请求参数里的 ``rules`` > 请求参数里的 ``preset_id`` 对应预设 >
设置项 ``active_preset_id`` 对应预设 > 全部预设按 id 升序汇总。

本插件与第三方插件共用同一套 hook 与清单格式，由宿主经 JSON-RPC 派发，
不另开特例通道。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app import store

TARGETS: tuple[str, ...] = ("input", "output")
DEFAULT_TARGET = "output"
RULE_FIELDS: tuple[str, ...] = ("name", "pattern", "replacement", "target", "trim")
ACTIVE_PRESET_SETTING = "active_preset_id"


@dataclass(frozen=True)
class RegexRule:
    """一条正则规则。"""

    name: str
    pattern: str
    replacement: str
    target: str = DEFAULT_TARGET
    trim: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        return {
            "name": self.name,
            "pattern": self.pattern,
            "replacement": self.replacement,
            "target": self.target,
            "trim": self.trim,
        }


def parse_rules(raw: Any) -> tuple[list[RegexRule], list[dict[str, Any]]]:
    """解析规则列表，返回 (合法规则, 问题清单)。

    单条规则非法不影响其它规则，问题清单记录位置与原因。
    """
    rules: list[RegexRule] = []
    issues: list[dict[str, Any]] = []
    if raw is None:
        return rules, issues
    if not isinstance(raw, (list, tuple)):
        issues.append({"index": None, "code": "invalid_format", "message": "regex_rules 必须是数组"})
        return rules, issues

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            issues.append(
                {"index": index, "code": "invalid_format", "message": "规则项必须是对象"}
            )
            continue
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            issues.append(
                {"index": index, "code": "pattern_required", "message": "规则缺少 pattern"}
            )
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            issues.append(
                {
                    "index": index,
                    "code": "pattern_invalid",
                    "message": f"正则非法：{exc}",
                    "pattern": pattern,
                }
            )
            continue
        replacement = item.get("replacement", "")
        if replacement is None:
            replacement = ""
        if not isinstance(replacement, str):
            issues.append(
                {
                    "index": index,
                    "code": "replacement_invalid",
                    "message": "replacement 必须是字符串",
                }
            )
            continue
        target = item.get("target", DEFAULT_TARGET)
        if target not in TARGETS:
            issues.append(
                {
                    "index": index,
                    "code": "target_invalid",
                    "message": "target 只能是 input 或 output",
                }
            )
            continue
        name = item.get("name")
        name = str(name) if name else f"rule-{index}"
        rules.append(
            RegexRule(
                name=name,
                pattern=pattern,
                replacement=replacement,
                target=str(target),
                trim=bool(item.get("trim", False)),
            )
        )
    return rules, issues


def _apply_one(
    text: str,
    compiled: re.Pattern[str],
    replacement: str,
    trim: bool,
) -> tuple[str, bool]:
    """应用单条规则，返回 (新文本, 是否命中)。

    trim 为真且替换内容为空时，删除匹配并去掉匹配处相邻空白。
    """
    pieces: list[str] = []
    position = 0
    matched = False
    for match in compiled.finditer(text):
        start, end = match.span()
        head = text[position:start]
        try:
            repl = match.expand(replacement)
        except re.error:
            repl = replacement
        if trim and not repl.strip():
            pieces.append(head.rstrip(" \t"))
            position = end
            while position < len(text) and text[position] in " \t":
                position += 1
            matched = True
            continue
        pieces.append(head)
        pieces.append(repl)
        position = end
        matched = True
    pieces.append(text[position:])
    return "".join(pieces), matched


def _normalize_blank_lines(text: str) -> str:
    """删除行尾空白，并把连续空行压成一个空行。"""
    stripped = re.sub(r"[ \t]+\n", "\n", text)
    collapsed = re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", stripped)
    return collapsed.strip("\n") if collapsed.strip() else ""


def apply_rules(
    text: str,
    rules: Iterable[RegexRule],
    target: str,
) -> tuple[str, list[dict[str, Any]]]:
    """按 target 应用规则，返回 (新文本, 逐条回执)。"""
    if not isinstance(text, str):
        return "", [{"code": "invalid_text", "message": "文本必须是字符串"}]
    current = text
    receipts: list[dict[str, Any]] = []
    removed = False
    for rule in rules:
        if rule.target != target:
            continue
        compiled = re.compile(rule.pattern)
        new_text, matched = _apply_one(current, compiled, rule.replacement, rule.trim)
        if not matched:
            continue
        if rule.trim and not rule.replacement.strip():
            removed = True
        receipts.append(
            {"name": rule.name, "pattern": rule.pattern, "target": rule.target, "trim": rule.trim}
        )
        current = new_text
    if removed:
        current = _normalize_blank_lines(current)
    return current, receipts


# ── 规则来源 ──────────────────────────────────────────────────


def _rules_from_preset(preset: dict[str, Any] | None) -> Any:
    """从预设字典取 regex_rules。"""
    if not isinstance(preset, dict):
        return None
    if "regex_rules" in preset:
        return preset.get("regex_rules")
    data = preset.get("data")
    if isinstance(data, dict):
        return data.get("regex_rules")
    return None


def load_rules(params: dict[str, Any]) -> tuple[list[RegexRule], list[dict[str, Any]], str]:
    """按优先级解析规则，返回 (规则, 问题清单, 来源说明)。"""
    raw = params.get("rules")
    source = "params"
    if raw is None:
        preset_id = params.get("preset_id")
        if not preset_id:
            preset_id = store.get_setting(ACTIVE_PRESET_SETTING, None)
        if preset_id:
            raw = _rules_from_preset(store.get_preset(str(preset_id)))
            source = f"preset:{preset_id}"
        else:
            collected: list[Any] = []
            for summary in sorted(store.list_presets(), key=lambda item: str(item.get("id"))):
                preset = store.get_preset(str(summary.get("id")))
                rules_raw = _rules_from_preset(preset)
                if isinstance(rules_raw, (list, tuple)):
                    collected.extend(rules_raw)
            raw = collected
            source = "all-presets"
    rules, issues = parse_rules(raw)
    return rules, issues, source


def _apply_to_messages(
    messages: Any,
    rules: list[RegexRule],
    target: str,
    role_filter: str,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """对指定角色的消息应用规则，原样保留其它消息。"""
    if not isinstance(messages, list):
        return [], [{"code": "invalid_messages", "message": "messages 必须是数组"}]
    receipts: list[dict[str, Any]] = []
    updated: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            updated.append(message)
            continue
        if message.get("role") != role_filter:
            updated.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, str):
            updated.append(message)
            continue
        new_content, applied = apply_rules(content, rules, target)
        if applied:
            receipts.extend(applied)
            merged = dict(message)
            merged["content"] = new_content
            updated.append(merged)
        else:
            updated.append(message)
    return updated, receipts


# ── hook 处理 ─────────────────────────────────────────────────


def handle(method: str, params: dict[str, Any]) -> Any:
    """处理一次 JSON-RPC 调用。"""
    if method == "ping":
        return {"plugin": "builtin-regex", "ok": True}
    if method == "rules":
        rules, issues, source = load_rules(params)
        return {
            "source": source,
            "rules": [rule.to_dict() for rule in rules],
            "issues": issues,
        }
    if method == "apply":
        rules, issues, source = load_rules(params)
        target = params.get("target", DEFAULT_TARGET)
        text, receipts = apply_rules(str(params.get("text", "")), rules, target)
        return {"source": source, "text": text, "applied": receipts, "issues": issues}
    if method == "preRequest":
        rules, issues, source = load_rules(params)
        messages, receipts = _apply_to_messages(
            params.get("messages"), rules, "input", "user"
        )
        return {"messages": messages, "notes": {"source": source, "applied": receipts, "issues": issues}}
    if method == "postResponse":
        rules, issues, source = load_rules(params)
        content, content_receipts = apply_rules(str(params.get("content", "")), rules, "output")
        reasoning_raw = params.get("reasoning")
        reasoning = reasoning_raw
        reasoning_receipts: list[dict[str, Any]] = []
        if isinstance(reasoning_raw, str) and reasoning_raw:
            reasoning, reasoning_receipts = apply_rules(reasoning_raw, rules, "output")
        return {
            "content": content,
            "reasoning": reasoning,
            "notes": {
                "source": source,
                "applied": content_receipts + reasoning_receipts,
                "issues": issues,
            },
        }
    # 未实现的 hook 视为无操作，避免派发时产生噪声错误。
    return {}
