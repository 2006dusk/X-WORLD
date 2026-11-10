r"""把结构化回合与崩溃修复的验收结果写进构建状态文档。

文档改动一律走本脚本：PowerShell 读中文文件按 GBK 解码，会把正文改坏。

用法：
    python backend\tools\update_status_struct.py
"""

from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATUS = os.path.join(ROOT, "..", "docs", "项目能力", "能力台账.md")

OLD_SCALE = r"| 后端 Python 文件 | 应用层 55 个 9089 行，测试 12 个 3015 行，工具 89 个 10581 行，合计 156 个约 22685 行 |"
OLD_TESTS = "| 后端测试用例 | 240 条，239 通过 1 跳过 |"
OLD_FE_TESTS = "| 前端测试 | 19 条用例 |"
OLD_PY = r"| 后端用例 | F:pythonpython.exe -m pytest | 239 通过 1 跳过 |"
OLD_FE = "| 前端用例 | npm run test | 19 条全通过 |"
ANCHOR = r"| 场景转移与物品获取 | backend\tools\probe_scene_and_item.py |"
PENDING_ANCHOR = "| 镜像下载校验 |"


def read(path: str) -> str:
    """按 UTF-8 读文本。"""
    with io.open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write(path: str, text: str) -> None:
    """按 UTF-8 写文本。"""
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def count_backend() -> tuple:
    """统计后端 Python 文件数与行数。"""
    backend = os.path.join(ROOT, "backend")
    groups = [
        ("app", os.path.join(backend, "app")),
        ("tests", os.path.join(backend, "tests")),
        ("tools", os.path.join(backend, "tools")),
    ]
    parts = []
    total_files = 0
    total_lines = 0
    for name, folder in groups:
        files = []
        for dirpath, _dirnames, filenames in os.walk(folder):
            for item in filenames:
                if item.endswith(".py"):
                    files.append(os.path.join(dirpath, item))
        lines = 0
        for item in files:
            with io.open(item, "r", encoding="utf-8", errors="ignore") as handle:
                lines += handle.read().count("\n")
        parts.append((name, len(files), lines))
        total_files += len(files)
        total_lines += lines
    return parts, total_files, total_lines


def main() -> int:
    """更新构建状态文档。"""
    text = read(STATUS)

    parts, total_files, total_lines = count_backend()
    scale = "、".join(
        "%s %d 个 %d 行" % (name, count, lines) for name, count, lines in parts
    )
    text = text.replace(
        OLD_SCALE,
        "| 后端 Python 文件 | %s，合计 %d 个约 %d 行 |" % (scale, total_files, total_lines),
        1,
    )
    text = text.replace(OLD_TESTS, "| 后端测试用例 | 242 条，241 通过 1 跳过 |", 1)
    text = text.replace(OLD_FE_TESTS, "| 前端测试 | 26 条用例 |", 1)
    text = text.replace(OLD_PY, r"| 后端用例 | F:pythonpython.exe -m pytest | 241 通过 1 跳过 |", 1)
    text = text.replace(OLD_FE, "| 前端用例 | npm run test | 26 条全通过 |", 1)

    if ANCHOR not in text:
        raise SystemExit("验收表锚点未找到")
    lines = text.split("\n")
    index = next(i for i, line in enumerate(lines) if line.startswith(ANCHOR))
    additions = [
        r"| 卡片库崩溃修复 | frontend\src\lib\json.ts 与 frontend\src\tests\cloneJson.test.ts | 通过，组件对 Vue 响应式代理做深拷贝导致 Proxy 无法克隆并引发卸载级联报错，改为逐层剥离代理后克隆；浏览器实测打开卡片库、切换卡片、保存卡片均无报错，控制台零错误 |",
        r"| 结构化输出四路对照 | backend\tools\probe_json_output.py 与 analyze_struct_formats.py | 通过，标记式两块齐全 5/5、对白带引号 5/5、耗时 2.81 秒；严格语法 JSON 对白带引号 0 到 1/5、耗时 5.7 秒，不采用 |",
        r"| 结构块解析与缺块闸门 | backend\tests\test_structure.py 与 test_chat_gates.py | 通过，流式分块与一次性解析结果一致，缺块时按块重写一次并补发 replace 事件 |",
        r"| 结构化回合生产链路 | backend\tools\probe_struct_blocks.py | 通过，八轮两块齐全 8/8、对白带中文引号 8/8、字数带内 7/8、整轮逐字相同 0、闸门触发 2 次、平均 3.00 秒，结果见报告 7.18 节 |",
    ]
    lines[index + 1:index + 1] = additions
    text = "\n".join(lines)

    if PENDING_ANCHOR not in text:
        raise SystemExit("未完成项锚点未找到")
    lines = text.split("\n")
    index = next(i for i, line in enumerate(lines) if line.startswith(PENDING_ANCHOR))
    pending = [
        "| 结构化链路的字数带内率 | 7/8 比纯文本链路的 8/8 少一轮，且观察到会话越长回复越短，长会话下的收窄趋势未做专项量化 |",
        "| 结尾说明行清理 | 归一层确定性删掉回合末尾的说明性括号行，模型若把说明写在块中间则不删 |",
    ]
    lines[index:index] = pending
    text = "\n".join(lines)

    write(STATUS, text)
    print("构建状态已更新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
