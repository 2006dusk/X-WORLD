"""多随机种子稳定性探针。

单一 seed 的单次样例不能代表一个模型的能力。本脚本对同一提示档用多个 seed
重复生成，记录长度带命中率、对白段数、完整回合率与本地裁判分，
用来区分「模型真的强」与「这一次抽到了好样例」。

裁判沿用 judge_local 的评分量表与机械校正，便于与 judge.json 横向比较。

用法：
    F:\\python\\python.exe tools\\probe_variance.py [配置名子串 ...] [--seeds N]
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
from judge_local import (  # noqa: E402
    JUDGE_ARGS,
    JUDGE_MODEL,
    judge_one,
    mechanical_adjust,
)
from multi_turn import start_server, stop_server, wait_ready  # noqa: E402
from sweep_models import PROMPTS, SWEEP_CASES, heuristic  # noqa: E402

OUT_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
OUT_FILE = OUT_DIR / "variance.json"
DEFAULT_SEEDS = [42, 7, 13, 99, 2024, 31337, 555, 8888]


def generate(base: str, seed: int) -> dict[str, object]:
    system, user = PROMPTS["p3"]
    started = time.perf_counter()
    resp = httpx.post(
        f"{base}/v1/chat/completions",
        json={
            "model": "local",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 900,
            "temperature": 0.8,
            "seed": seed,
        },
        timeout=1800.0,
    )
    resp.raise_for_status()
    elapsed = time.perf_counter() - started
    body = resp.json()
    choice = (body.get("choices") or [{}])[0]
    text = str((choice.get("message") or {}).get("content") or "")
    metrics = heuristic(text, choice.get("finish_reason"), body.get("usage"))
    metrics["wall_s"] = round(elapsed, 2)
    return {"seed": seed, "content": text, "metrics": metrics}


def facts_line(metrics: dict[str, object]) -> str:
    return (
        f"字数 {int(metrics.get('chars') or 0)}，"
        f"引号对白 {int(metrics.get('dialogue_count') or 0)} 段，"
        f"非对白描写句 {int(metrics.get('narration_sentences') or 0)} 句，"
        f"以句末标点收尾 {'是' if metrics.get('ends_clean') else '否'}"
    )


def summarize(samples: list[dict[str, object]]) -> dict[str, object]:
    chars = [int((s["metrics"] or {}).get("chars") or 0) for s in samples]
    totals = [
        sum(float((s.get("judge") or {}).get(k) or 0) for k in
            ("character", "narrative", "chinese", "format", "clean", "pov"))
        for s in samples
        if not (s.get("judge") or {}).get("error")
    ]
    povs = [float((s.get("judge") or {}).get("pov") or 0) for s in samples if not (s.get("judge") or {}).get("error")]
    return {
        "runs": len(samples),
        "chars_mean": round(statistics.fmean(chars), 1) if chars else 0,
        "chars_min": min(chars) if chars else 0,
        "chars_max": max(chars) if chars else 0,
        "in_band_rate": round(sum(1 for c in chars if 150 <= c <= 300) / len(chars), 3) if chars else 0,
        "under_150_rate": round(sum(1 for c in chars if c < 150) / len(chars), 3) if chars else 0,
        "dialogue_ge1_rate": round(
            sum(1 for s in samples if int((s["metrics"] or {}).get("dialogue_count") or 0) >= 1) / len(samples), 3
        ) if samples else 0,
        "complete_turn_rate": round(
            sum(1 for s in samples if (s["metrics"] or {}).get("complete_turn")) / len(samples), 3
        ) if samples else 0,
        "judge_total_mean": round(statistics.fmean(totals), 1) if totals else 0,
        "judge_total_min": min(totals) if totals else 0,
        "judge_total_max": max(totals) if totals else 0,
        "judge_total_std": round(statistics.pstdev(totals), 2) if len(totals) > 1 else 0,
        "pov_min": min(povs) if povs else 0,
    }


def main() -> int:
    argv = sys.argv[1:]
    seed_count = 8
    if "--seeds" in argv:
        idx = argv.index("--seeds")
        seed_count = int(argv[idx + 1])
        del argv[idx : idx + 2]
    wanted = [a.lower() for a in argv if not a.startswith("--")]
    seeds = DEFAULT_SEEDS[:seed_count]

    def matches(name: str) -> bool:
        low = name.lower()
        for token in wanted:
            if token.startswith("="):
                if low == token[1:]:
                    return True
            elif token in low:
                return True
        return False

    cases = [case for case in SWEEP_CASES if not wanted or matches(str(case["name"]))]
    if not cases:
        print("没有匹配的档位")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for index, case in enumerate(cases, 1):
        name = str(case["name"])
        model_path = Path(str(case["model"]))
        print(f"[{index}/{len(cases)}] {name} 生成 {len(seeds)} 个 seed", flush=True)
        if not model_path.exists():
            rows.append({"name": name, "error": "权重文件不存在"})
            continue
        port = free_port()
        base = f"http://{HOST}:{port}"
        proc, log_path, handle = start_server(str(model_path), list(case["args"]), port)
        samples: list[dict[str, object]] = []
        try:
            if not wait_ready(base, proc):
                rows.append({"name": name, "error": "服务启动超时"})
                continue
            time.sleep(1.0)
            for seed in seeds:
                item = generate(base, seed)
                samples.append(item)
                m = item["metrics"]
                print(
                    f"    seed {seed}: {m['chars']} 字, 对白 {m['dialogue_count']} 段, "
                    f"带内 {m['in_length_band']}, 完整 {m['complete_turn']}, {m['wall_s']}s",
                    flush=True,
                )
        finally:
            stop_server(proc, log_path, handle)

        print(f"    裁判 {len(samples)} 条 ...", flush=True)
        port = free_port()
        jbase = f"http://{HOST}:{port}"
        proc, log_path, handle = start_server(JUDGE_MODEL, JUDGE_ARGS, port)
        try:
            if wait_ready(jbase, proc):
                time.sleep(1.0)
                for item in samples:
                    raw = judge_one(jbase, str(item["content"]), facts_line(item["metrics"]))
                    item["judge"] = raw if "error" in raw else mechanical_adjust(raw, item["metrics"])
            else:
                print("    裁判服务启动超时", flush=True)
        finally:
            stop_server(proc, log_path, handle)
            wait_gpu_free()

        summary = summarize(samples)
        print(
            f"    汇总：字数 {summary['chars_mean']}({summary['chars_min']}-{summary['chars_max']}) "
            f"带内率 {summary['in_band_rate']} 对白率 {summary['dialogue_ge1_rate']} "
            f"完整率 {summary['complete_turn_rate']} 裁判 {summary['judge_total_mean']}"
            f"(±{summary['judge_total_std']})",
            flush=True,
        )
        rows.append({
            "name": name,
            "params": case.get("params"),
            "quant": case.get("quant"),
            "note": case.get("note"),
            "seeds": seeds,
            "samples": samples,
            "summary": summary,
        })
        OUT_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n结果已写入 {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())