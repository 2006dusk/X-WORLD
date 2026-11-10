"""插件清单解析与校验。

清单为 YAML，固定字段 id、name、version、apiVersion、capabilities、entry。
本模块只做解析与校验，不管理进程；校验采用「一次性列出全部违规项」的策略，
不逐条抛异常，调用方据 ``ManifestValidation.issues`` 组装结构化错误。

字段约定：

- id：小写字母与数字，可用连字符分段，例如 ``builtin-regex``；必填且全局唯一
  （唯一性由宿主在加载阶段校验）。
- name：非空字符串，展示名。
- version：语义化版本 ``MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]``，必填。
- apiVersion：区间字符串，形如 ``>=1.0.0 <2.0.0``，用空格或逗号分隔多个比较式；
  逗号与空白等价，比较符支持 ``>=``、``<=``、``>``、``<``、``=``、``==``，
  省略比较符视为 ``=``。
- capabilities：字符串列表，取值限定 regex、worldbook、memory、tts、image、http。
- entry：入口命令与参数，``{"command": ["python", "main.py"], "args": ["--stdio"]}``，
  command 也接受单个字符串；``command`` 首项为 ``builtin`` 时表示进程内内置插件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MANIFEST_FILENAME = "manifest.yaml"

HOST_API_VERSION = "1.0.0"
HOST_API_RANGE = ">=1.0.0 <2.0.0"

ALLOWED_CAPABILITIES: tuple[str, ...] = (
    "regex",
    "worldbook",
    "memory",
    "tts",
    "image",
    "http",
)

BUILTIN_COMMAND = "builtin"

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
_RANGE_TOKEN_RE = re.compile(r"\s*(>=|<=|==|=|>|<)?\s*([^\s,]+)\s*,?")


@dataclass(frozen=True)
class ManifestIssue:
    """单条校验问题。"""

    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """转为可序列化字典。"""
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class PluginManifest:
    """校验通过的清单。"""

    id: str
    name: str
    version: str
    api_version: str
    capabilities: tuple[str, ...]
    entry_command: tuple[str, ...]
    entry_args: tuple[str, ...]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_builtin(self) -> bool:
        """是否为进程内内置插件。"""
        return bool(self.entry_command) and self.entry_command[0] == BUILTIN_COMMAND

    def to_dict(self) -> dict[str, Any]:
        """转为接口返回的形状。"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "apiVersion": self.api_version,
            "capabilities": list(self.capabilities),
            "entry": {
                "command": list(self.entry_command),
                "args": list(self.entry_args),
            },
        }


@dataclass
class ManifestValidation:
    """校验结果，issues 为空且 manifest 非空表示通过。"""

    manifest: PluginManifest | None = None
    issues: list[ManifestIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """清单是否可用。"""
        return self.manifest is not None and not self.issues

    def error_payload(self, *, code: str = "plugin_manifest_invalid") -> dict[str, Any]:
        """组装结构化错误体。"""
        return {
            "code": code,
            "message": "插件清单校验失败",
            "issues": [issue.to_dict() for issue in self.issues],
        }


# ── 版本与区间 ────────────────────────────────────────────────


def is_plugin_id(value: Any) -> bool:
    """判断是否为合法插件 id。"""
    return isinstance(value, str) and bool(_ID_RE.match(value))


def parse_version(text: Any) -> tuple[int, int, int, str] | None:
    """解析语义化版本，返回 (major, minor, patch, prerelease)；非法返回 None。"""
    if not isinstance(text, str):
        return None
    match = _SEMVER_RE.match(text.strip())
    if match is None:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        match.group("pre") or "",
    )


def is_semver(text: Any) -> bool:
    """判断是否为语义化版本字符串。"""
    return parse_version(text) is not None


def parse_api_range(text: Any) -> list[tuple[str, tuple[int, int, int, str]]] | None:
    """解析 apiVersion 区间，返回比较式列表；非法返回 None。"""
    if not isinstance(text, str) or not text.strip():
        return None
    comparators: list[tuple[str, tuple[int, int, int, str]]] = []
    position = 0
    length = len(text)
    while position < length:
        match = _RANGE_TOKEN_RE.match(text, position)
        if match is None or match.end() == position:
            return None
        operator = match.group(1) or "="
        parsed = parse_version(match.group(2))
        if parsed is None:
            return None
        comparators.append((operator, parsed))
        position = match.end()
    if not comparators:
        return None
    return comparators


def compare_versions(left: Any, right: Any) -> int | None:
    """比较两个语义化版本，返回 -1、0、1；任一非法返回 None。"""
    lv = parse_version(left) if isinstance(left, str) else left if isinstance(left, tuple) else None
    rv = parse_version(right) if isinstance(right, str) else right if isinstance(right, tuple) else None
    if lv is None or rv is None:
        return None
    if lv[:3] != rv[:3]:
        return -1 if lv[:3] < rv[:3] else 1
    lpre, rpre = lv[3], rv[3]
    if lpre == rpre:
        return 0
    if not lpre:
        return 1
    if not rpre:
        return -1
    return -1 if lpre < rpre else 1


def range_matches(range_text: Any, version: Any) -> bool:
    """判断版本是否落在区间内。

    参数:
        range_text: 区间字符串，如 ``>=1.0.0 <2.0.0``。
        version: 待判定版本，如 ``1.2.0``。

    返回:
        命中返回 True；任一参数非法返回 False。
    """
    comparators = parse_api_range(range_text)
    target = parse_version(version)
    if comparators is None or target is None:
        return False
    for operator, bound in comparators:
        result = compare_versions(target, bound)
        if result is None:
            return False
        if operator == ">=" and result < 0:
            return False
        if operator == ">" and result <= 0:
            return False
        if operator == "<=" and result > 0:
            return False
        if operator == "<" and result >= 0:
            return False
        if operator in ("=", "==") and result != 0:
            return False
    return True


# ── 清单校验 ──────────────────────────────────────────────────


def _validate_entry(
    value: Any,
    issues: list[ManifestIssue],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """校验 entry 字段，返回 (command, args)。"""
    if value is None:
        issues.append(ManifestIssue("entry", "required", "缺少 entry 字段"))
        return (), ()
    if not isinstance(value, dict):
        issues.append(
            ManifestIssue("entry", "invalid_format", "entry 必须是包含 command 的映射")
        )
        return (), ()

    command: tuple[str, ...] = ()
    raw_command = value.get("command")
    if raw_command is None:
        issues.append(ManifestIssue("entry.command", "required", "缺少 entry.command 字段"))
    elif isinstance(raw_command, str):
        command = (raw_command,) if raw_command.strip() else ()
        if not command:
            issues.append(ManifestIssue("entry.command", "invalid_value", "entry.command 不能为空"))
    elif isinstance(raw_command, (list, tuple)):
        if not raw_command or not all(
            isinstance(item, str) and item.strip() for item in raw_command
        ):
            issues.append(
                ManifestIssue(
                    "entry.command",
                    "invalid_value",
                    "entry.command 必须是非空字符串数组",
                )
            )
        else:
            command = tuple(raw_command)
    else:
        issues.append(
            ManifestIssue(
                "entry.command",
                "invalid_format",
                "entry.command 必须是字符串或字符串数组",
            )
        )

    args: tuple[str, ...] = ()
    raw_args = value.get("args")
    if raw_args is not None:
        if isinstance(raw_args, (list, tuple)) and all(isinstance(item, str) for item in raw_args):
            args = tuple(raw_args)
        else:
            issues.append(
                ManifestIssue("entry.args", "invalid_format", "entry.args 必须是字符串数组")
            )

    return command, args


def validate_manifest(data: Any) -> ManifestValidation:
    """校验清单数据，一次性收集全部违规项。

    参数:
        data: 已解析的 YAML 对象。

    返回:
        ManifestValidation，ok 为 True 时 manifest 可用。
    """
    issues: list[ManifestIssue] = []
    if not isinstance(data, dict):
        issues.append(ManifestIssue("$", "invalid_format", "清单根节点必须是映射"))
        return ManifestValidation(manifest=None, issues=issues)

    plugin_id = data.get("id")
    if plugin_id is None:
        issues.append(ManifestIssue("id", "required", "缺少 id 字段"))
    elif not is_plugin_id(plugin_id):
        issues.append(
            ManifestIssue(
                "id",
                "invalid_format",
                "id 只能由小写字母、数字与连字符分段组成，例如 builtin-regex",
            )
        )

    name = data.get("name")
    if name is None:
        issues.append(ManifestIssue("name", "required", "缺少 name 字段"))
    elif not isinstance(name, str) or not name.strip():
        issues.append(ManifestIssue("name", "invalid_value", "name 必须是非空字符串"))

    version = data.get("version")
    if version is None:
        issues.append(ManifestIssue("version", "required", "缺少 version 字段"))
    elif not is_semver(version):
        issues.append(
            ManifestIssue("version", "invalid_format", "version 必须是语义化版本，例如 1.0.0")
        )

    api_version = data.get("apiVersion")
    if api_version is None:
        issues.append(ManifestIssue("apiVersion", "required", "缺少 apiVersion 字段"))
    elif not isinstance(api_version, str):
        issues.append(
            ManifestIssue("apiVersion", "invalid_format", "apiVersion 必须是区间字符串")
        )
    elif parse_api_range(api_version) is None:
        issues.append(
            ManifestIssue(
                "apiVersion",
                "invalid_format",
                "apiVersion 必须是合法区间，例如 >=1.0.0 <2.0.0",
            )
        )

    capabilities: tuple[str, ...] = ()
    raw_caps = data.get("capabilities")
    if raw_caps is None:
        issues.append(ManifestIssue("capabilities", "required", "缺少 capabilities 字段"))
    elif not isinstance(raw_caps, (list, tuple)):
        issues.append(ManifestIssue("capabilities", "invalid_format", "capabilities 必须是数组"))
    else:
        collected: list[str] = []
        for index, item in enumerate(raw_caps):
            if not isinstance(item, str):
                issues.append(
                    ManifestIssue(
                        f"capabilities[{index}]",
                        "invalid_format",
                        "capabilities 元素必须是字符串",
                    )
                )
                continue
            if item not in ALLOWED_CAPABILITIES:
                issues.append(
                    ManifestIssue(
                        f"capabilities[{index}]",
                        "unknown_capability",
                        "capabilities 取值限定为 " + "、".join(ALLOWED_CAPABILITIES),
                    )
                )
                continue
            if item not in collected:
                collected.append(item)
        capabilities = tuple(collected)

    command, args = _validate_entry(data.get("entry"), issues)

    extra = {
        key: value
        for key, value in data.items()
        if key
        not in {"id", "name", "version", "apiVersion", "capabilities", "entry"}
    }

    if issues:
        return ManifestValidation(manifest=None, issues=issues)

    manifest = PluginManifest(
        id=str(plugin_id),
        name=str(name).strip(),
        version=str(version).strip(),
        api_version=str(api_version).strip(),
        capabilities=capabilities,
        entry_command=command,
        entry_args=args,
        extra=extra,
    )
    return ManifestValidation(manifest=manifest, issues=[])


def parse_manifest_text(text: str) -> ManifestValidation:
    """解析 YAML 文本并校验。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return ManifestValidation(
            manifest=None,
            issues=[ManifestIssue("$", "invalid_yaml", f"YAML 解析失败：{exc}")],
        )
    return validate_manifest(data)


def load_manifest(path: Path) -> ManifestValidation:
    """读取清单文件并校验。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ManifestValidation(
            manifest=None,
            issues=[ManifestIssue("$", "unreadable", f"清单文件不可读：{exc}")],
        )
    return parse_manifest_text(text)
