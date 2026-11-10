r"""三轴汇总：质量、逻辑、性能合并成一张对照表。

输入：
- docs/实践情况/data/sweep/multi_turn.json   质量轴（多轮推进）
- docs/实践情况/data/sweep/logic.json         逻辑轴（确定性 + 裁判）
- docs/实践情况/data/bench_local_final.json   性能轴（差分法速率）

输出：
- docs/实践情况/data/sweep/axes_summary.json
- docs/实践情况/data/sweep/axes_summary.md

裁判分按既有结论只作参考标记，确定性指标为主。

用法：
    python backend\tools\summarize_axes.py
"""

from __future__ import annotations

import json
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
ROOT = BACKEND_ROOT.parent
SWEEP_DIR = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
DATA_DIR = ROOT.parent / "docs" / "实践情况" / "data"

MULTI_FILE = SWEEP_DIR / "multi_turn.json"
LOGIC_FILE = SWEEP_DIR / "logic.json"
BENCH_FILE = DATA_DIR / "bench_local_final.json"
OUT_JSON = SWEEP_DIR / "axes_summary.json"
OUT_MD = SWEEP_DIR / "axes_summary.md"


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else list(data.values())
    return [r for r in rows if isinstance(r, dict)]


def quality_axis(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        turns = row.get("turns") or []
        if not turns:
            continue
        metrics = [t.get("metrics") or {} for t in turns]
        chars = [float(m.get("chars") or 0) for m in metrics]
        band = sum(1 for m in metrics if m.get("in_length_band"))
        dialogues = [float(m.get("dialogue_count") or 0) for m in metrics]
        cjk = sum(int(m.get("cjk_dialogue_count") or 0) for m in metrics)
        ascii_d = sum(int(m.get("ascii_dialogue_count") or 0) for m in metrics)
        walls = [float(m.get("wall_s") or 0) for m in metrics]
        overlaps = [float(m.get("overlap_history") or 0) for m in metrics]
        judge = row.get("judge") or []
        progress = [float(s.get("progress") or 0) for s in judge if isinstance(s, dict)]
        out[str(row.get("name"))] = {
            "params": row.get("params"),
            "quant": row.get("quant"),
            "turns": len(turns),
            "chars_mean": _mean(chars),
            "chars_list": [int(c) for c in chars],
            "in_band": band,
            "in_band_rate": round(band / len(turns), 3),
            "over_length": sum(1 for m in metrics if m.get("over_length")),
            "dialogue_mean": _mean(dialogues),
            "dialogue_cjk": cjk,
            "dialogue_ascii": ascii_d,
            "wall_mean_s": _mean(walls),
            "wall_max_s": round(max(walls), 2) if walls else 0.0,
            "overlap_max": round(max(overlaps), 4) if overlaps else 0.0,
            "overlap_list": overlaps,
            "progress_scores": progress,
            "progress_mean": _mean(progress),
            "judge_total": round(sum(progress) * 2.0, 1),
        }
    return out


def logic_axis(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        det = row.get("deterministic") or {}
        judge = row.get("judge") or {}
        total = sum(float(judge.get(k) or 0) for k in ("coherence", "consistency", "response"))
        out[str(row.get("name"))] = {
            "judge_logic": round(total, 1),
            "judge_detail": {
                k: float(judge.get(k) or 0)
                for k in ("coherence", "consistency", "response")
            },
            "response_rate": det.get("response_rate"),
            "contradictions": det.get("contradictions") or [],
            "max_overlap": det.get("max_overlap"),
            "verbatim_repeat": det.get("verbatim_repeat"),
        }
    return out


def perf_axis(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        short = row.get("short") or {}
        long = row.get("long") or {}
        ttft = row.get("ttft") or {}
        out[str(row.get("name"))] = {
            "decode_tok_per_s": long.get("decode_tok_per_s") or short.get("decode_tok_per_s"),
            "prefill_tok_per_s": long.get("prefill_tok_per_s"),
            "prefill_s": long.get("prefill_s"),
            "ttft_s": ttft.get("ttft_s"),
            "prompt_tokens": long.get("prompt_tokens"),
            "server_eval_tok_per_s": long.get("server_eval_tok_per_s"),
            "args": row.get("args"),
            "note": row.get("note"),
        }
    return out


def render_md(merged: list[dict]) -> str:
    lines = [
        "# 三轴汇总（质量 / 逻辑 / 性能）",
        "",
        "口径：质量与逻辑取自本地四轮真实后端会话，指标为确定性口径；",
        "裁判分只作参考，已知单裁判换 seed 的分差均值 10 到 13 分，大于档位差距。",
        "性能取差分法速率（生成 1 与 256 token 作差）。",
        "",
        "## 总表",
        "",
        "| 档位 | 参数量 | 量化 | 平均字数 | 字数带内 | 对白段数(均) | 中文对白 | 照搬峰值 | 推进分(裁判) | 逻辑分(裁判) | 回应率 | 矛盾 | 解码 tok/s | TTFT s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in merged:
        q = row.get("quality") or {}
        lg = row.get("logic") or {}
        pf = row.get("perf") or {}
        lines.append(
            "| {name} | {params} | {quant} | {chars} | {band}/{turns} | {dlg} | {cjk} | {ov} | {prog} | {logic} | {resp} | {contra} | {dec} | {ttft} |".format(
                name=row.get("name"),
                params=q.get("params") or "-",
                quant=q.get("quant") or "-",
                chars=q.get("chars_mean", "-"),
                band=q.get("in_band", "-"),
                turns=q.get("turns", "-"),
                dlg=q.get("dialogue_mean", "-"),
                cjk=q.get("dialogue_cjk", "-"),
                ov=q.get("overlap_max", "-"),
                prog=q.get("progress_mean", "-"),
                logic=lg.get("judge_logic", "-"),
                resp=lg.get("response_rate", "-"),
                contra=(len(lg.get("contradictions") or []) if lg else "-"),
                dec=pf.get("decode_tok_per_s", "-"),
                ttft=pf.get("ttft_s", "-"),
            )
        )
    lines.append("")
    lines.append("## 逐档明细")
    for row in merged:
        q = row.get("quality") or {}
        lg = row.get("logic") or {}
        pf = row.get("perf") or {}
        lines.append("")
        lines.append("### " + str(row.get("name")))
        lines.append("")
        lines.append("- 质量：字数 " + str(q.get("chars_list")) + "，对白段数均值 " + str(q.get("dialogue_mean"))
                     + "，中文引号对白 " + str(q.get("dialogue_cjk")) + "，ASCII 引号 " + str(q.get("dialogue_ascii"))
                     + "，超长 " + str(q.get("over_length")) + " 轮")
        lines.append("- 回合耗时：均值 " + str(q.get("wall_mean_s")) + " s，最大 " + str(q.get("wall_max_s")) + " s")
        lines.append("- 照搬率（与前文 8 字片段重合）：" + str(q.get("overlap_list")) + "，峰值 " + str(q.get("overlap_max")))
        lines.append("- 裁判推进分：" + str(q.get("progress_scores")))
        lines.append("- 逻辑：" + json.dumps(lg, ensure_ascii=False))
        lines.append("- 性能：" + json.dumps(pf, ensure_ascii=False))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    multi = load(MULTI_FILE)
    logic = load(LOGIC_FILE)
    bench = load(BENCH_FILE)
    quality = quality_axis(multi)
    logic_map = logic_axis(logic)
    perf = perf_axis(bench)
    names: list[str] = []
    for source in (quality, logic_map, perf):
        for name in source:
            if name not in names:
                names.append(name)
    merged = []
    for name in names:
        merged.append({
            "name": name,
            "quality": quality.get(name),
            "logic": logic_map.get(name),
            "perf": perf.get(name),
        })
    OUT_JSON.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(render_md(merged), encoding="utf-8")
    print("已写入 %s" % OUT_JSON)
    print("已写入 %s" % OUT_MD)
    for row in merged:
        print("  %s" % row["name"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
