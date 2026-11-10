"""在构建状态文档补记成人向实测与本轮新增工具。

用法：
    python tools\note_adult_in_build_status.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT.parent / "docs" / "项目能力" / "能力台账.md"

OLD = "| 多轮剧情推进 | backend\\tools\\multi_turn.py 四个配置四轮 | 通过，结果同报告 6.5 节 |"
NEW = (OLD
        + chr(10)
        + "| 成人向五回合 | backend\\tools\\probe_adult.py 五档五轮 | 通过，五档均无拒绝；默认档三轮逐字复述，2B 零复述，结果见报告 7.13 节 |")


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    if "probe_adult.py" in text:
        print("已存在，跳过")
        return 0
    if OLD not in text:
        print("未找到锚点行")
        return 1
    DOC.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("构建状态文档已补记")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
