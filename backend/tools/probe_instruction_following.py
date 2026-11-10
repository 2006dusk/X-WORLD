"""指令遵循 vs 角色扮演语料：拆解 2B 的分数来源。

同一模型同一场景，跑四档提示：
- q0：不加任何格式要求（默认 RP 行为）
- q1：只要求中文引号对白
- q2：只要求 150 到 300 字
- q3：同时要求对白与字数（客户端实际口径）

如果分数随格式要求逐档上升，说明模型靠指令遵循拿到格式分，
而不是语料本身带角色扮演能力。

用法：
    python tools\probe_instruction_following.py [配置名] [--seeds N]
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
from multi_turn import start_server, stop_server, wait_ready  # noqa: E402
from sweep_models import SWEEP_CASES, heuristic  # noqa: E402

OUT_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
OUT_FILE = OUT_DIR / "instruction_following.json"

BASE_SYSTEM = (
    "你扮演角色K，在地址N经营地址U的调音师，沉默、观察力强。"
    "回复必须保持角色一致，不解释设定，不使用旁白总结，不提及自己是 AI。"
    "用中文写作。"
)
SCENE = (
    "【场景】深夜，地址U只剩一盏台灯，雨点敲在铁皮屋顶上。\n"
    "【细节】控制台还亮着，桌角压着一张手写便签，上面是一个陌生名字。\n"
    "【你的行动】我推开门，手里攥着一盘无人认领的磁带，走到控制台前，把它放在调音台上。"
)
VARIANTS = {
    "q0": "",
    "q1": "\n对白请用中文引号包起来。",
    "q2": "\n请控制在 150 到 300 字之间。",
    "q3": "\n请写一个完整回合，包含场景描写、人物动作和对白，对白用中文引号，"
          "全文控制在 150 到 300 字之间，写到自然收尾为止。",
}
DEFAULT_SEEDS = [42, 7, 13, 99, 2024]


def main() -> int:
    argv = sys.argv[1:]
    seed_count = 5
    if "--seeds" in argv:
        i = argv.index("--seeds")
        seed_count = int(argv[i + 1])
        del argv[i : i + 2]
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

    cases = [c for c in SWEEP_CASES if matches(str(c["name"]))]
    if not cases:
        print("没有匹配的档位")
        return 1

    out_rows = []
    for index, case in enumerate(cases, 1):
        name = str(case["name"])
        model_path = Path(str(case["model"]))
        print(f"[{index}/{len(cases)}] {name}", flush=True)
        if not model_path.exists():
            out_rows.append({"name": name, "error": "权重文件不存在"})
            continue
        port = free_port()
        base = f"http://{HOST}:{port}"
        proc, log_path, handle = start_server(str(model_path), list(case["args"]), port)
        variants = {}
        try:
            if not wait_ready(base, proc):
                out_rows.append({"name": name, "error": "服务启动超时"})
                continue
            time.sleep(1.0)
            for vname, extra in VARIANTS.items():
                stats = []
                for seed in seeds:
                    payload = {
                        "model": "local",
                        "messages": [
                            {"role": "system", "content": BASE_SYSTEM},
                            {"role": "user", "content": SCENE + extra},
                        ],
                        "max_tokens": 900,
                        "temperature": 0.8,
                        "seed": seed,
                    }
                    resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=1800.0)
                    resp.raise_for_status()
                    body = resp.json()
                    choice = (body.get("choices") or [{}])[0]
                    text = str((choice.get("message") or {}).get("content") or "")
                    m = heuristic(text, choice.get("finish_reason"), body.get("usage"))
                    stats.append({
                        "seed": seed,
                        "chars": m["chars"],
                        "dialogue_count": m["dialogue_count"],
                        "cjk_dialogue_count": m["cjk_dialogue_count"],
                        "ascii_dialogue_count": m["ascii_dialogue_count"],
                        "narration_sentences": m["narration_sentences"],
                        "ends_clean": m["ends_clean"],
                        "finish_reason": m["finish_reason"],
                        "truncated": m["truncated"],
                        "complete_turn": m["complete_turn"],
                        "in_length_band": m["in_length_band"],
                        "repeat_rate": m["repeat_rate"],
                        "content": text,
                    })
                summary = {
                    "runs": len(stats),
                    "chars_mean": round(statistics.fmean([s["chars"] for s in stats]), 1),
                    "dialogue_rate": round(sum(1 for s in stats if s["dialogue_count"] >= 1) / len(stats), 3),
                    "complete_rate": round(sum(1 for s in stats if s["complete_turn"]) / len(stats), 3),
                    "band_rate": round(sum(1 for s in stats if s["in_length_band"]) / len(stats), 3),
                }
                variants[vname] = {"summary": summary, "samples": stats}
                print(f"    {vname}: 字数 {summary['chars_mean']} 对白率 {summary['dialogue_rate']} "
                      f"完整率 {summary['complete_rate']} 带内率 {summary['band_rate']}", flush=True)
        finally:
            stop_server(proc, log_path, handle)
        out_rows.append({"name": name, "params": case.get("params"), "variants": variants})
        OUT_FILE.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n结果已写入 {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
