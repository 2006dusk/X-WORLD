"""由扫描数据生成质量与速度平衡点报告。

读 docs/实践情况/data/sweep/sweep.json 与同目录的 judge.json，
输出 docs/实践情况/质量与速度平衡点.md。

用法：
    python backend\tools\build_sweep_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
REPORT = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "质量与速度平衡点.md"

MODEL_LABELS = {
    "lumi_q4_km": "lumi_finally_v2",
    "qwen2.5-14b-roleplay-zh_q4_km": "qwen2.5-14B-roleplay-zh",
    "arityflow-4b_q4_km": "ArityFlow-Qwen3-4B-Instruct-2507-RolePlay",
    "arityflow-4b_q5_km": "ArityFlow-Qwen3-4B-Instruct-2507-RolePlay",
    "arityflow-4b_q8_0": "ArityFlow-Qwen3-4B-Instruct-2507-RolePlay",
    "tifa-7b_q4_km": "Tifa-7B-Qwen2-v0.1",
    "tifa-7b_q6_k": "Tifa-7B-Qwen2-v0.1",
    "peach-9b_q4_km": "Peach-9B-8k-Roleplay",
    "nqlsg-14b_q4_km": "NQLSG-Qwen2.5-14B-MegaFusion-v5-roleplay",
}

JUDGE_KEYS = ("character", "narrative", "chinese", "format", "clean", "pov")
TARGET_BAND = (150, 300)


def deterministic_score(metrics: dict) -> float:
    """无裁判时的确定性质量分，满分 100。"""
    score = 0.0
    if metrics.get("in_length_band"):
        score += 25
    if metrics.get("complete_turn"):
        score += 30
    if int(metrics.get("dialogue_count") or 0) >= 1:
        score += 15
    ratio = float(metrics.get("dialogue_ratio") or 0)
    if 0.1 <= ratio <= 0.7:
        score += 10
    score += min(int(metrics.get("keyword_hits") or 0), 6) * 2.5
    repeat = float(metrics.get("repeat_rate") or 0)
    score += max(0.0, 20.0 - repeat * 400)
    return round(min(score, 100.0), 1)


def judge_score(entry: dict, judge: dict) -> tuple[float | None, str]:
    scores = judge.get(entry["name"])
    if not isinstance(scores, dict):
        return None, "无裁判记录"
    if scores.get("skipped"):
        return None, str(scores["skipped"])
    if scores.get("error"):
        return None, str(scores["error"])
    total = sum(float(scores.get(k) or 0) for k in JUDGE_KEYS)
    return round(total * 100 / 60, 1), str(scores.get("reason") or "")


def main() -> int:
    sweep_path = DATA_DIR / "sweep.json"
    if not sweep_path.exists():
        print("缺少 sweep.json")
        return 1
    entries = json.loads(sweep_path.read_text(encoding="utf-8"))
    judge_path = DATA_DIR / "judge.json"
    judge = {}
    judge_model = "未运行"
    if judge_path.exists():
        payload = json.loads(judge_path.read_text(encoding="utf-8"))
        judge = payload.get("scores") or {}
        judge_model = payload.get("judge_model") or "未知"

    rows = []
    for entry in entries:
        if "error" in entry or "sample_p3" not in entry:
            continue
        p1 = entry["sample_p1"]["metrics"]
        p2 = entry["sample_p2"]["metrics"]
        p3 = entry["sample_p3"]["metrics"]
        jscore, reason = judge_score(entry, judge)
        rows.append(
            {
                "key": entry["name"],
                "label": MODEL_LABELS.get(entry["name"], entry["name"]),
                "params": entry.get("params"),
                "quant": entry.get("quant"),
                "size_mb": entry.get("size_mb"),
                "decode": entry.get("short", {}).get("decode_tok_per_s"),
                "prefill": entry.get("long", {}).get("prefill_tok_per_s"),
                "ttft": entry.get("ttft", {}).get("ttft_s"),
                "p1_chars": p1.get("chars"),
                "p1_complete": p1.get("complete_turn"),
                "p2_chars": p2.get("chars"),
                "p2_complete": p2.get("complete_turn"),
                "p3_chars": p3.get("chars"),
                "p3_band": p3.get("in_length_band"),
                "p3_complete": p3.get("complete_turn"),
                "p3_dialogue": p3.get("dialogue_count"),
                "p3_seconds": p3.get("wall_s"),
                "p3_first_person": p3.get("first_person_count"),
                "repeat": p3.get("repeat_rate"),
                "det": deterministic_score(p3),
                "judge": jscore,
                "reason": reason,
                "note": entry.get("note"),
            }
        )

    def table(headers: list[str], body: list[list[str]]) -> str:
        out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
        for line in body:
            out.append("| " + " | ".join(str(x) for x in line) + " |")
        return chr(10).join(out)

    speed_rows = [
        [
            r["label"],
            r["params"],
            r["quant"],
            f"{r['size_mb']} MB" if r["size_mb"] else "-",
            f"{r['decode']}" if r["decode"] else "-",
            f"{r['prefill']}" if r["prefill"] else "-",
            f"{r['ttft']}" if r["ttft"] else "-",
        ]
        for r in rows
    ]
    quality_rows = [
        [
            r["label"],
            r["quant"],
            f"{r['p1_chars']}",
            "是" if r["p1_complete"] else "否",
            f"{r['p2_chars']}",
            "是" if r["p2_complete"] else "否",
            f"{r['p3_chars']}",
            "是" if r["p3_band"] else "否",
            "是" if r["p3_complete"] else "否",
            f"{r['p3_dialogue']}",
            f"{r['p3_seconds']}",
            f"{r['det']}",
            f"{r['judge']}" if r["judge"] is not None else "无",
        ]
        for r in rows
    ]
    reason_rows = [[r["label"], r["quant"], r["reason"] or "-"] for r in rows if r["reason"]]

    ordered = sorted(rows, key=lambda r: (r["p3_seconds"] is None, r["p3_seconds"] or 0))
    feel_rows = [
        [
            str(index + 1),
            r["label"],
            r["quant"],
            f"{r['p3_chars']} 字",
            f"{r['p3_seconds']} s",
            f"{r['decode']} tok/s",
            f"{r['judge']}" if r["judge"] is not None else "无",
        ]
        for index, r in enumerate(ordered)
    ]

    quant_rows = []
    for label in sorted({r["label"] for r in rows}):
        group = [r for r in rows if r["label"] == label]
        if len(group) < 2:
            continue
        for r in sorted(group, key=lambda x: x["quant"]):
            quant_rows.append(
                [
                    r["label"],
                    r["quant"],
                    f"{r['size_mb']} MB" if r["size_mb"] else "-",
                    f"{r['decode']}" if r["decode"] else "-",
                    f"{r['p3_chars']}",
                    f"{r['det']}",
                    f"{r['judge']}" if r["judge"] is not None else "无",
                ]
            )

    text = f"""# 本地 RP 模型的质量与速度平衡点

在 8 GB 显存的本机上，对不同模型与不同量化档做同口径实测，找出「质量够用、速度够快」的实际平衡点。全部推理在本机完成，不调用任何云端模型。

目标长度按客户端实际口径取 {TARGET_BAND[0]} 到 {TARGET_BAND[1]} 字：单次回复要含场景与人物对话，写成完整一段。

## 1. 扫描口径

| 项 | 设定 |
|---|---|
| 速度 | 差分法。同一提示分别生成 1 token 与 256 token，端到端耗时作差得解码速率，回代得预填充速率。首字延迟由流式请求测得 |
| 回合提示 | 三档。P1 默认 RP 行为，模型自定篇幅；P2 显式要求至少 500 字、三段以上；P3 按目标长度写，控制在 {TARGET_BAND[0]} 到 {TARGET_BAND[1]} 字 |
| 判定 | 回合完整 = 含对白、含非对白描写、结尾是句末标点、且未被长度截断 |
| 长度达标 | 落在 {TARGET_BAND[0]} 到 {TARGET_BAND[1]} 字之间记为命中 |
| 确定性分 | 长度达标 25 分、回合完整 30 分、有对白 15 分、对白占比合理 10 分、关键词命中 2.5 分每项、重复退化扣分，满分 100 |
| 裁判分 | 本地裁判模型 {judge_model} 按角色一致性、叙事完整度、中文语感、形式契合、无重复退化、视角正确六项各 1 到 10 分，合计乘 100 除 60 折百分制 |
| 参数 | 温度 0.8，seed 42，最大生成 900 token；单样本，不做多次采样取平均 |

## 2. 模型轴：速度

{table(["模型", "参数", "量化", "体积", "解码 tok/s", "预填充 tok/s", "首字延迟 s"], speed_rows)}

## 3. 模型轴：质量

{table(["模型", "量化", "P1 字数", "P1 完整", "P2 字数", "P2 完整", "P3 字数", "P3 长度达标", "P3 完整", "P3 对白段", "P3 耗时 s", "确定性分", "裁判分"], quality_rows)}

## 4. 量化轴

{table(["模型", "量化", "体积", "解码 tok/s", "P3 字数", "确定性分", "裁判分"], quant_rows)}

## 5. 体感排序

按目标长度回合从发出到生成完毕的总耗时排序，这是用户实际等待的时间。

{table(["序", "模型", "量化", "回合长度", "等待", "解码", "裁判分"], feel_rows)}

## 6. 裁判理由

{table(["模型", "量化", "理由"], reason_rows)}

## 7. 平衡点建议

待填。

## 8. 数据来源与数据边界

| 项 | 说明 |
|---|---|
| 权重来源 | Hugging Face 镜像直链下载，文件字节数与 HTTP 头 Content-Length 一致 |
| 推理 | 本机 llama_cpp_python 0.3.22 的 server 模块，CUDA 已启用 |
| 提示词 | 单一角色卡与单一场景，对所有配置完全一致 |
| 裁判 | 本地裁判模型打分，裁判模型自身那一档不自评；单次打分，采样噪声未量化 |
| 确定性分 | 规则写在第 1 节，可复算；关键词表固定为六个词 |
| 未覆盖 | 多轮上下文下的一致性、成人向内容表现、不同温度与不同 seed 的方差 |
| 未覆盖 | 裁判模型与被测模型同属中文 RP 系列，风格偏好可能偏向同类输出 |

## 9. 后续动作

待填。
"""
    REPORT.write_text(text, encoding="utf-8")
    print(f"报告已写入 {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
