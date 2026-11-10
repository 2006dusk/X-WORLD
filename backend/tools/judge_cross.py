"""异族裁判交叉验证。

默认裁判是 Qwen3.5-4B-UD，与被测的 Qwen3.5-2B 同族，存在同族偏好风险。
本脚本不重新生成样例，直接复用 sweep.json 里已存的 P3 样例，
换另一个裁判模型重新打分，结果写入 judge_cross.json，用于看排名是否随裁判改变。

用法：
    python tools\judge_cross.py --judge-model <gguf 路径> --judge-name <显示名> [--skip <配置名>] [配置名子串 ...]
"""

from __future__ import annotations

import json
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
from judge_local import judge_one, mechanical_adjust  # noqa: E402

SWEEP_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
OUT_FILE = SWEEP_DIR / "judge_cross.json"
JUDGE_ARGS = ["--ctx-size", "8192", "--n-gpu-layers", "999"]


def main() -> int:
    argv = sys.argv[1:]
    judge_model = ""
    judge_name = ""
    skip: list[str] = []
    while "--judge-model" in argv:
        i = argv.index("--judge-model")
        judge_model = argv[i + 1]
        del argv[i : i + 2]
    while "--judge-name" in argv:
        i = argv.index("--judge-name")
        judge_name = argv[i + 1]
        del argv[i : i + 2]
    while "--skip" in argv:
        i = argv.index("--skip")
        skip.append(argv[i + 1].lower())
        del argv[i : i + 2]
    wanted = [a.lower() for a in argv if not a.startswith("--")]
    if not judge_model:
        print("缺少 --judge-model")
        return 1
    judge_name = judge_name or Path(judge_model).stem

    sweep_path = SWEEP_DIR / "sweep.json"
    entries = json.loads(sweep_path.read_text(encoding="utf-8"))
    targets = []
    for entry in entries:
        name = str(entry.get("name") or "")
        if "sample_p3" not in entry or "error" in entry:
            continue
        if wanted and not any(t in name.lower() for t in wanted):
            continue
        if name.lower() in skip or judge_name.lower() in name.lower():
            continue
        targets.append(entry)
    if not targets:
        print("没有可评的配置")
        return 1

    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(judge_model, JUDGE_ARGS, port)
    scores: dict[str, object] = {}
    try:
        if not wait_ready(base, proc):
            print("裁判服务启动超时")
            return 1
        time.sleep(1.0)
        for entry in targets:
            name = str(entry.get("name"))
            sample = entry.get("sample_p3") or {}
            content = str(sample.get("content") or "")
            if not content.strip():
                scores[name] = {"error": "样例为空"}
                continue
            metrics = sample.get("metrics") or {}
            facts = (
                f"字数 {int(metrics.get('chars') or 0)}，"
                f"引号对白 {int(metrics.get('dialogue_count') or 0)} 段，"
                f"非对白描写句 {int(metrics.get('narration_sentences') or 0)} 句，"
                f"以句末标点收尾 {'是' if metrics.get('ends_clean') else '否'}"
            )
            print(f"评判 {name} ...", flush=True)
            raw = judge_one(base, content, facts)
            scores[name] = raw if "error" in raw else mechanical_adjust(raw, metrics)
            print(f"    {scores[name]}", flush=True)
    finally:
        stop_server(proc, log_path, handle)
        wait_gpu_free()

    existing = {}
    if OUT_FILE.exists():
        try:
            existing = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing[judge_name] = scores
    OUT_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
