"""裁判噪声分解：把「生成方差」与「裁判方差」拆开。

同一批生成样例，用多个裁判 seed 重复打分：
- 生成方差：固定裁判 seed，看不同生成样例之间的分数差；
- 裁判方差：固定生成样例，看不同裁判 seed 之间的分数差。

如果两者量级相当且都远小于档位间的差值，那么单次打分无法支撑排名结论。

用法：
    python tools\judge_seed_noise.py [--judge-seeds 42,7,99]
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(BACKEND_ROOT))

from bench_local import HOST, free_port, wait_gpu_free  # noqa: E402
from wwc_server import start_server, stop_server, wait_ready  # noqa: E402
from judge_local import JUDGE_ARGS, JUDGE_MODEL, mechanical_adjust  # noqa: E402

SWEEP_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
VAR_FILE = SWEEP_DIR / "variance.json"
OUT_FILE = SWEEP_DIR / "judge_noise.json"
DIMS = ("character", "narrative", "chinese", "format", "clean", "pov")


def judge_at(base: str, content: str, facts: str, seed: int) -> dict:
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": facts},
        ],
        "max_tokens": 400,
        "temperature": 0.7,
        "seed": seed,
        "response_format": {"type": "json_object"},
    }
    resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=1800.0)
    resp.raise_for_status()
    body = resp.json()
    text = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    match = JSON_RE.search(text)
    if not match:
        return {"error": "裁判输出无法解析", "raw": text[:200]}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"error": "裁判输出解析失败", "raw": text[:200]}
    out = {}
    for key in DIMS:
        try:
            out[key] = float(data.get(key) or 0)
        except (TypeError, ValueError):
            out[key] = 0.0
    out["reason"] = str(data.get("reason") or "")[:200]
    return out


if __name__ == "__main__":
    from judge_local import JSON_RE, RUBRIC  # noqa: E402

    argv = sys.argv[1:]
    judge_seeds = [42, 7, 99]
    if "--judge-seeds" in argv:
        i = argv.index("--judge-seeds")
        judge_seeds = [int(x) for x in argv[i + 1].split(",")]
        del argv[i : i + 2]

    rows = json.loads(VAR_FILE.read_text(encoding="utf-8"))
    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(JUDGE_MODEL, JUDGE_ARGS, port)
    out_rows = []
    try:
        if not wait_ready(base, proc):
            print("裁判服务启动超时")
            raise SystemExit(1)
        time.sleep(1.0)
        for row in rows:
            samples = row.get("samples") or []
            if not samples:
                continue
            name = str(row.get("name"))
            print(f"== {name} 用 {len(judge_seeds)} 个裁判 seed 重打 {len(samples)} 条", flush=True)
            per_sample = []
            for sample in samples:
                metrics = sample.get("metrics") or {}
                facts = (
                    f"机械统计：字数 {int(metrics.get('chars') or 0)}，"
                    f"引号对白 {int(metrics.get('dialogue_count') or 0)} 段，"
                    f"非对白描写句 {int(metrics.get('narration_sentences') or 0)} 句，"
                    f"以句末标点收尾 {'是' if metrics.get('ends_clean') else '否'}\n\n回复正文：\n"
                    f"{sample.get('content') or ''}"
                )
                scores = {}
                for jseed in judge_seeds:
                    raw = judge_at(base, str(sample.get("content") or ""), facts, jseed)
                    adjusted = raw if "error" in raw else mechanical_adjust(raw, metrics)
                    total = sum(float(adjusted.get(k) or 0) for k in DIMS)
                    scores[str(jseed)] = {"total": total, "dims": {k: adjusted.get(k) for k in DIMS}}
                totals = [scores[str(s)]["total"] for s in judge_seeds]
                per_sample.append({
                    "seed": sample.get("seed"),
                    "chars": metrics.get("chars"),
                    "by_judge_seed": scores,
                    "judge_mean": round(statistics.fmean(totals), 2),
                    "judge_spread": round(max(totals) - min(totals), 2),
                    "judge_std": round(statistics.pstdev(totals), 2),
                })
                print(f"    gen seed {sample.get('seed')}: {totals} 差 {max(totals) - min(totals):.0f}", flush=True)
            gen_means = [p["judge_mean"] for p in per_sample]
            spreads = [p["judge_spread"] for p in per_sample]
            summary = {
                "generation_std": round(statistics.pstdev(gen_means), 2) if len(gen_means) > 1 else 0,
                "generation_range": round(max(gen_means) - min(gen_means), 2) if gen_means else 0,
                "judge_seed_std_mean": round(statistics.fmean([p["judge_std"] for p in per_sample]), 2) if per_sample else 0,
                "judge_seed_spread_max": round(max(spreads), 2) if spreads else 0,
                "judge_seed_spread_mean": round(statistics.fmean(spreads), 2) if spreads else 0,
            }
            print(f"    汇总 {summary}", flush=True)
            out_rows.append({"name": name, "summary": summary, "samples": per_sample})
    finally:
        stop_server(proc, log_path, handle)
        wait_gpu_free()

    OUT_FILE.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {OUT_FILE}")
