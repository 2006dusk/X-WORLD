r"""把结构化回合的结论写进报告、契约与构建状态。

文档改动一律走本脚本：PowerShell 读中文文件按 GBK 解码，会把正文改坏。

用法：
    python backend\tools\update_docs_struct.py
"""

from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPORT = os.path.join(ROOT, "..", "docs", "实践情况", "质量与速度平衡点.md")
CONTRACT = os.path.join(ROOT, "..", "docs", "项目实现", "构建契约.md")
STATUS = os.path.join(ROOT, "..", "docs", "项目能力", "能力台账.md")

SECTION_718 = """
### 7.18 结构化回合：场景与对白两块

目标：一轮回复拆成「场景」与「对白」两块，客户端拿到结构化数据做二次加工。

#### 7.18.1 四路对照：为什么不用 JSON

直连 llama.cpp，五轮一组，提示相同（探针 backend/tools/probe_json_output.py，数据 data/sweep/json_output.json）：

| 形态 | 机器可解析 | 两块齐全 | 对白带引号 | 字数带内 | 内容均长 | 耗时均 |
|---|---|---|---|---|---|---|
| 单段纯文本 | 不适用 | 不适用 | 5/5 | 5/5 | 198.6 字 | 2.50 s |
| 标记式【场景】【对话】 | 标记 5/5 | 5/5 | 5/5 | 5/5 | 218.4 字 | 2.81 s |
| 仅提示 JSON | 4/5 | 4/5 | 3/5 | 3/5 | 178.0 字 | 2.47 s |
| 严格语法 JSON（场景与对白） | 5/5 | 5/5 | 1/5 | 1/5 | 135.4 字 | 5.69 s |
| 严格语法 JSON（场景、动作、对白） | 5/5 | 5/5 | 0/5 | 2/5 | 137.4 字 | 5.88 s |

口径说明：llama_cpp_python 0.3.22 的 response_format 带 schema 时会把 schema 转成 GBNF 语法，生成被硬约束，输出必为合法 JSON。但解码速率从约 58 tok/s 降到约 22 tok/s，耗时 2.4 倍；且模型在 JSON 字符串字段里几乎不写引号。引号列按客户端归一层折算（成对英文双引号转中文双引号），复算脚本 backend/tools/analyze_struct_formats.py，数据 data/sweep/struct_formats.json。

仅提示 JSON 的唯一一轮失败是模型在对白里写了未转义的英文引号，破坏 JSON 合法性；容错解析可以救，但要另写一套容错规则。

结论：采用标记式。标记式与纯文本同速，两块齐全率与对白引号率都到 5/5。

#### 7.18.2 客户端落地

- 内置预设升到 v7，output_spec 声明 structure 为场景与对白两块，回合要求由 render_output_spec 渲染。
- 后端新增 app/inference/structure.py：split_blocks 做一次性解析，StructureSplitter 做流式解析。标记可能被分片切开，跨片的半个标记留到下一片再判，流式拼出的块与一次性解析结果一致。
- 路由层把增量按块归位，delta 事件带 block 字段；流末发 blocks 事件给出后端解析的最终块。
- 新增第三道闸门：缺块时按块重写一次，事件原因为 structure，与复读闸门、长度闸门共用一次重采样额度。
- 前端气泡按块渲染，块名与展示标签由后端给出；块内容随会话流水落盘。

#### 7.18.3 回合要求的口径修正

生产链路首次实测（backend/tools/probe_struct_blocks.py，八轮）字数带内率只有 3/8。原文里模型在两块之外追加了一行括号写的合规说明，例如「（注意：以上为上一回合的延续与扩展，符合字数要求并推动剧情发展。）」。纯文本链路八轮没有这类尾巴。

回合要求单变量对照（backend/tools/probe_struct_directives.py，每组五轮，真实后端）：

| 变体 | 字数带内 | 均长 | 结尾说明 |
|---|---|---|---|
| 只写合计下限 | 1/5 | 141.6 字 | 1/5 |
| 加每块下限 | 5/5 | 178.6 字 | 0/5 |
| 只加禁止结尾总结 | 1/5 | 120.2 字 | 0/5 |
| 两者都加 | 2/5 | 132.6 字 | 0/5 |

每块下限的比例再测三档（新会话，每档五轮）：场景 100 对白 50 一档带内 4/5、均长 182.2 字；场景 100 对白 80 一档 4/5、182.6 字；场景 120 对白 60 一档 5/5、192.4 字。定档为场景 120 对白 60，即总数下限的 0.8 与 0.4，写入 STRUCTURE_BLOCK_MIN_RATIO。

结尾说明行改为在归一层确定性删掉（app/inference/normalize.py 的 drop_annotation_lines）：整行是中文括号包裹且命中说明性词的才删，行内正常括号不受影响。

#### 7.18.4 生产链路验收

探针 backend/tools/probe_struct_blocks.py，八轮真实后端（内置预设、角色卡、世界书、历史落盘）：

| 指标 | 结果 |
|---|---|
| 两块齐全 | 8/8 |
| 对白带中文引号 | 8/8 |
| 字数带内（150 到 300） | 7/8，最短 132 字，最长 259 字 |
| 整轮逐字相同 | 0 |
| 不同回复 | 8 |
| 闸门触发 | 2 次 |
| 平均耗时 | 3.00 s |

对照纯文本链路（7.16 节）：八轮字数带内 8/8，最短 169 字。结构化链路少一轮达标，换来的是可解析的两块与稳定的对白引号率。数据在 data/sweep/struct_blocks.json。


"""

SECTION8_ROWS = [
    "| 已覆盖 | 结构化回合的四路对照与落地，7.18 节：五轮四形态对照加两轮回合要求单变量对照，生产链路八轮，探针 backend/tools/probe_json_output.py、probe_struct_directives.py、probe_struct_blocks.py、analyze_struct_formats.py，数据在 data/sweep 下 json_output.json、struct_formats.json、struct_directives.json、struct_blocks.json |",
    "| 未覆盖 | 7.18 节每组只有五轮，字数带内率的组间差异多落在噪声里，只取方向性结论；结构化链路观察到会话越长回复越短，长会话下的收窄趋势未做专项量化 |",
    "| 未覆盖 | 结尾说明行的删除规则只作用于回合末尾，模型若把说明写在块中间，当前规则不删 |",
]

SECTION10_ITEMS = [
    "23. 结构化回合已落地：7.18 节，标记式两块，内置预设 v7 声明 structure，后端流式分块加缺块闸门，前端按块渲染，客户端拿到的是场景与对白两个块。",
    "24. 结构化链路的字数带内率 7/8 比纯文本链路的 8/8 少一轮，且观察到会话越长回复越短，长会话下的收窄趋势需要专项量化。",
    "25. 结尾说明行由归一层确定性删掉，但规则只作用于末尾；模型若把说明写在块中间，需要再补一条块内清理规则。",
]


def read(path: str) -> str:
    """按 UTF-8 读文本。"""
    with io.open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write(path: str, text: str) -> None:
    """按 UTF-8 写文本。"""
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def update_report() -> None:
    """报告：插入 7.18 节，补第 8 节与第 10 节。"""
    text = read(REPORT)
    if "### 7.18" in text:
        print("报告已含 7.18 节，跳过")
        return
    anchor = "## 8. 数据来源与数据边界"
    text = text.replace(anchor, SECTION_718 + anchor, 1)

    rows = "\n".join(SECTION8_ROWS)
    marker = "\n## 9. 客户端落地"
    text = text.replace(marker, "\n" + rows + marker, 1)

    items = "\n".join(SECTION10_ITEMS)
    text = text.rstrip("\n") + "\n" + items + "\n"
    write(REPORT, text)
    print("报告已更新")


def update_contract() -> None:
    """契约：SSE 事件表补 blocks，delta 补 block 字段。"""
    text = read(CONTRACT)
    old = "| delta | content |"
    new = "| delta | content；结构化回合另带 block 指明归属的块 |"
    if new not in text:
        if old not in text:
            raise SystemExit("契约 delta 行未找到")
        text = text.replace(old, new, 1)
    old_row = "| done | finish_reason |"
    new_row = "| blocks | blocks（块名到内容）、labels（块名到展示标签） |\n| done | finish_reason |"
    if new_row not in text:
        if old_row not in text:
            raise SystemExit("契约 done 行未找到")
        text = text.replace(old_row, new_row, 1)
    text = text.replace("流式为 SSE，事件名固定六种，字段固定：", "流式为 SSE，事件名固定七种，字段固定：", 1)
    write(CONTRACT, text)
    print("契约已更新")


def main() -> int:
    """依次更新三份文档。"""
    update_report()
    update_contract()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
