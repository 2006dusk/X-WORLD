r"""多轮剧情推进测试。

单回合写得好不等于能推进剧情。本脚本让每个模型连演四轮，
同一段故事逐轮推进，逐轮记录：长度、格式、与前文的内容重合度、
是否复述前文，以及本地裁判给的剧情推进分。

用法：
    python backend\tools\multi_turn.py [配置名子串 ...]
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(BACKEND_ROOT))

from bench_local import HOST, free_port, wait_gpu_free  # noqa: E402
from wwc_server import start_server as _wwc_start_server  # noqa: E402
from wwc_server import stop_server as _wwc_stop_server  # noqa: E402
from wwc_server import wait_ready as _wwc_wait_ready  # noqa: E402
from sweep_models import (  # noqa: E402
    ASCII_DIALOGUE_RE,
    DIALOGUE_RE,
    SWEEP_CASES,
    heuristic,
)
from local_paths import model_file  # noqa: E402


OUT_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
JUDGE_MODEL = model_file("local/qwen3.5-4b/Qwen3.5-4B-UD-Q4_K_XL.gguf")
JUDGE_ARGS = ["--ctx-size", "8192", "--n-gpu-layers", "999"]

SYSTEM = (
    "你扮演角色K，在地址N经营地址U的调音师，沉默、观察力强。"
    "回复必须保持角色一致，不解释设定，不使用旁白总结，不提及自己是 AI。"
    "用中文写作。"
    "每次回复写一个完整回合：先写场景与环境的短描写，再写人物的动作，"
    "然后写一到三句对白，对白用中文引号，最后收在一个明确的动作或台词上。"
    "全文控制在 150 到 300 字之间。"
)

TURNS = [
    "我推开门，手里攥着一盘无人认领的磁带，走到控制台前，把它放在调音台上。",
    "磁带开始转动，传出女人的哼唱。我伸手去按停止键，指尖停在按钮上方。",
    "便签上的名字我认出来了，是我妹妹的笔迹。她三年前失踪了。",
    "磁带里忽然出现第二个声音，它在叫我的名字，一遍又一遍。",
]

PROGRESS_RUBRIC = """你是中文角色扮演的剧情推进评审。下面给你一段 RP 对话的前文和最新一轮回复。

判断这一轮有没有把剧情往前推，0 到 10 分：
- 10：出现新的动作、对白或信息，场景明确往前走了一步
- 5：只有描写，没有新推进，也没有明显复述
- 0：整段复述前文、原地打转、或答非所问

另外三项各 0 到 10 分：
- new_info：给出了多少新信息
- action：是否有具体的人物动作
- dialogue：是否有中文引号包起来的人物对白

只输出 JSON，不要解释：
{"progress": 0, "new_info": 0, "action": 0, "dialogue": 0, "reason": "一句话"}"""

JSON_RE = re.compile(r"\{.*\}", re.S)
GRAM = 8


def grams(text: str) -> set[str]:
    return {text[i : i + GRAM] for i in range(0, max(len(text) - GRAM, 0))}


def overlap(text: str, history: list[str]) -> float:
    """本轮文本中，有多少 8 字片段在历史里出现过。"""
    if not history:
        return 0.0
    mine = grams(text)
    if not mine:
        return 0.0
    seen: set[str] = set()
    for item in history:
        seen |= grams(item)
    return round(len(mine & seen) / len(mine), 4)


def start_server(model: str, args: list[str], port: int):
    """起自写推理服务端。

    命令行怎么拼在 wwc_server 里，这里只转发；本脚本不自己拼参数。
    """
    return _wwc_start_server(model, args, port, HOST)


def wait_ready(base: str, proc, timeout: float = 600.0) -> bool:
    return _wwc_wait_ready(base, proc, timeout)


def stop_server(proc, log_path: Path, handle) -> None:
    """停服务端并等显存回落。"""
    _wwc_stop_server(proc, log_path, handle)
    wait_gpu_free()


def play_turns(case: dict[str, object]) -> dict[str, object]:
    model_path = Path(str(case["model"]))
    record: dict[str, object] = {
        "name": case["name"],
        "params": case.get("params"),
        "quant": case.get("quant"),
        "note": case.get("note"),
        "turns": [],
    }
    if not model_path.exists():
        record["error"] = "权重文件不存在"
        return record

    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(str(model_path), list(case["args"]), port)
    history: list[str] = []
    messages = [{"role": "system", "content": SYSTEM}]
    try:
        if not wait_ready(base, proc):
            record["error"] = "服务启动超时"
            return record
        time.sleep(1.0)
        for index, user in enumerate(TURNS, 1):
            messages.append({"role": "user", "content": user})
            started = time.perf_counter()
            resp = httpx.post(
                f"{base}/v1/chat/completions",
                json={
                    "model": "local",
                    "messages": messages,
                    "max_tokens": 900,
                    "temperature": 0.8,
                    "seed": 42,
                },
                timeout=1800.0,
            )
            resp.raise_for_status()
            elapsed = time.perf_counter() - started
            body = resp.json()
            choice = (body.get("choices") or [{}])[0]
            text = str(choice.get("message", {}).get("content") or "")
            metrics = heuristic(text, choice.get("finish_reason"), body.get("usage"))
            metrics["overlap_history"] = overlap(text, history)
            metrics["wall_s"] = round(elapsed, 2)
            record["turns"].append({"turn": index, "user": user, "content": text, "metrics": metrics})
            history.append(text)
            messages.append({"role": "assistant", "content": text})
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop_server(proc, log_path, handle)
    return record


def judge_progress(base: str, turns: list[dict]) -> list[dict]:
    out = []
    for item in turns:
        context = "\n".join(
            f"第{t['turn']}轮 用户：{t['user']}\n第{t['turn']}轮 回复：{t['content']}"
            for t in turns
            if t["turn"] <= item["turn"]
        )
        resp = httpx.post(
            f"{base}/v1/chat/completions",
            json={
                "model": "local",
                "messages": [
                    {"role": "system", "content": PROGRESS_RUBRIC},
                    {"role": "user", "content": context},
                ],
                "max_tokens": 400,
                "temperature": 0.0,
                "seed": 42,
                "response_format": {"type": "json_object"},
            },
            timeout=1800.0,
        )
        resp.raise_for_status()
        body = resp.json()
        text = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "")
        match = JSON_RE.search(text)
        scores: dict[str, object] = {"turn": item["turn"]}
        if match:
            try:
                data = json.loads(match.group(0))
                for key in ("progress", "new_info", "action", "dialogue"):
                    scores[key] = float(data.get(key) or 0)
                scores["reason"] = str(data.get("reason") or "")[:200]
            except json.JSONDecodeError:
                scores["error"] = "裁判输出解析失败"
        else:
            scores["error"] = "裁判输出无法解析"
            scores["raw"] = text[:200]
        out.append(scores)
    return out


def main() -> int:
    wanted = [a.lower() for a in sys.argv[1:]]
    cases = [
        case
        for case in SWEEP_CASES
        if not wanted
        or any(token in str(case["name"]).lower() for token in wanted)
    ]
    if not cases:
        print("没有匹配的档位")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['name']}", flush=True)
        item = play_turns(case)
        records.append(item)
        if "error" in item:
            print(f"    失败：{item['error']}", flush=True)
        else:
            for turn in item["turns"]:
                m = turn["metrics"]
                print(
                    f"    第{turn['turn']}轮 {m['chars']} 字, 对白 {m['dialogue_count']} 段, "
                    f"与前文重合 {m['overlap_history']}, {m['wall_s']} s",
                    flush=True,
                )

    print("== 裁判 ==", flush=True)
    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(JUDGE_MODEL, JUDGE_ARGS, port)
    try:
        if wait_ready(base, proc):
            time.sleep(1.0)
            for record in records:
                if "turns" not in record:
                    continue
                record["judge"] = judge_progress(base, record["turns"])
                scores = [s.get("progress") for s in record["judge"] if isinstance(s.get("progress"), float)]
                print(f"    {record['name']}: 推进分 {scores}", flush=True)
        else:
            print("裁判服务启动超时", flush=True)
    finally:
        stop_server(proc, log_path, handle)

    target = OUT_DIR / "multi_turn.json"
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
