"""代码约定哨兵。

参考 StarHub 的 tests/test_python_conventions.py 与 test_dead_code_sentinels.py：
用测试把约定固定住，避免风格随改动漂移。

这些哨兵只覆盖本项目已经明确遵守、且改动时容易破坏的约定。
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
ROUTER_DIR = APP_DIR / "routers"

# 模型权重按设计放在模型库目录，不在数据目录内，路径由模型档案声明。
# 该模块是盘符路径的唯一例外。
DRIVE_LETTER_ALLOWLIST = {"inference/traits.py"}

DRIVE_LETTER = "hardcoded drive letter"
BARE_EXCEPT = "bare except"
PRINT_CALL = "print call"
SQLITE = "sqlite usage"
MISSING_DOCSTRING = "missing module docstring"
MISSING_FUNCTION_DOCSTRING = "missing function docstring"


def _python_files() -> list[Path]:
    return sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _sources() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in _python_files()]


def _rel(path: Path) -> str:
    return path.relative_to(APP_DIR.parent).as_posix()


def test_no_hardcoded_drive_letters() -> None:
    """数据目录一律经 app.config，盘符路径只允许出现在模型档案里。"""
    offenders = []
    for path, text in _sources():
        if _rel(path) in DRIVE_LETTER_ALLOWLIST:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if len(stripped) > 2 and stripped[1] == ":" and stripped[2] in "\\/":
                offenders.append(f"{_rel(path)}:{number}")
    assert not offenders, f"{DRIVE_LETTER}: {offenders}"


def test_no_bare_except() -> None:
    """不允许裸 except，异常必须分类捕获。"""
    offenders = []
    for path, text in _sources():
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip() == "except:":
                offenders.append(f"{_rel(path)}:{number}")
    assert not offenders, f"{BARE_EXCEPT}: {offenders}"


def test_no_print_calls() -> None:
    """统一走 logging，禁止 print。"""
    offenders = []
    for path, text in _sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "print":
                    offenders.append(f"{_rel(path)}:{node.lineno}")
    assert not offenders, f"{PRINT_CALL}: {offenders}"


def test_no_sqlite_imports() -> None:
    """数据层是文件与 JSON，不引入关系型数据库。"""
    offenders = []
    for path, text in _sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "sqlite" in alias.name.lower() or alias.name in {"db"}:
                        offenders.append(f"{_rel(path)}:{node.lineno} {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").lower()
                if "sqlite" in module or module.endswith(".db") or module == "app.db":
                    offenders.append(f"{_rel(path)}:{node.lineno} {node.module}")
    assert not offenders, f"{SQLITE}: {offenders}"


def test_modules_have_docstrings() -> None:
    """每个模块都要有模块级说明。"""
    offenders = []
    for path, text in _sources():
        tree = ast.parse(text)
        if not ast.get_docstring(tree):
            offenders.append(_rel(path))
    assert not offenders, f"{MISSING_DOCSTRING}: {offenders}"


def test_router_handlers_have_docstrings() -> None:
    """路由处理函数都要有说明。"""
    offenders = []
    for path in sorted(ROUTER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            if not ast.get_docstring(node):
                offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert not offenders, f"{MISSING_FUNCTION_DOCSTRING}: {offenders}"
