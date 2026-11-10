r"""本地 RP 输出模型 token 级速率复测。

流式响应不含 token 数，本脚本对同一提示词做非流式生成，
直接读取 usage.completion_tokens 与服务端 timings，得到精确 tok/s 与提示词评估速率。

用法：
    python backend\tools\bench_local_tokens.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_local import (  # noqa: E402
    CASES,
    HOST,
    LONG_PROMPT,
    SHORT_PROMPT,
    free_port,
    wait_gpu_free,
)
from wwc_server import start_server, stop_server, wait_ready  # noqa: E402

MAX_TOKENS = 300


def measure(base: str, prompt: dict[str, str]) -> dict[str, object]:
    """非流式生成一次，取 usage 与 timings。"""
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        "max_tokens": MAX_TOKENS,
        # 自写服务端不认 min_tokens，实际生成多少 token 看 usage.completion_tokens。
        "temperature": 0.8,
        "stream": False,
    }
    started = time.perf_counter()
    resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=900.0)
    resp.raise_for_status()
    total = time.perf_counter() - started
    body = resp.json()
    usage = body.get("usage") or {}
    timings = body.get("timings") or {}
    completion_tokens = int(usage.get("completion_tokens") or 0)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    # 自写服务端的计时字段名：gen_per_second / gen_ms / prompt_per_second。
    predicted_per_second = float(timings.get("gen_per_second") or 0.0)
    prompt_per_second = float(timings.get("prompt_per_second") or 0.0)
    predicted_ms = float(timings.get("gen_ms") or 0.0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "wall_total_s": round(total, 3),
        "server_predicted_per_second": predicted_per_second,
        "server_prompt_per_second": prompt_per_second,
        "server_predicted_ms": predicted_ms,
        "measured_tok_per_second": round(completion_tokens / (predicted_ms / 1000.0), 3)
        if predicted_ms > 0
        else 0.0,
        "measured_prompt_tok_per_second": round(prompt_tokens / (total - predicted_ms / 1000.0), 3)
        if predicted_ms > 0 and total > predicted_ms / 1000.0
        else 0.0,
        "finish_reason": body.get("choices", [{}])[0].get("finish_reason"),
    }


def measure_ttft(base: str) -> dict[str, object]:
    """短流式请求，测首字延迟。"""
    payload = {
        "model": "local",
        "messages": [{"role": "user", "content": "说一句你好"}],
        "max_tokens": 8,
        "temperature": 0.8,
        "stream": True,
    }
    started = time.perf_counter()
    ttft = 0.0
    with httpx.Client(timeout=300.0) as client:
        with client.stream("POST", f"{base}/v1/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    break
                try:
                    obj = json.loads(body)
                except json.JSONDecodeError:
                    continue
                content = obj.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                if content:
                    ttft = time.perf_counter() - started
                    break
    return {"ttft_s": round(ttft, 3)}


def run_case(case: dict[str, object]) -> dict[str, object]:
    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(str(case["model"]), list(case["args"]), port)
    result: dict[str, object] = {
        "name": case["name"],
        "note": case["note"],
        "args": " ".join(str(a) for a in case["args"]),
    }
    try:
        if not wait_ready(base, proc):
            result["error"] = "服务启动超时"
            return result
        time.sleep(1.0)
        result["ttft"] = measure_ttft(base)
        result["short"] = measure(base, SHORT_PROMPT)
        result["long"] = measure(base, LONG_PROMPT)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop_server(proc, log_path, handle)
        wait_gpu_free()
    return result


def main() -> int:
    print("== 本地 RP 输出模型 token 级速率复测 ==", flush=True)
    only = os.environ.get("BENCH_CASES", "").strip()
    cases = [
        case
        for case in CASES
        if not only
        or any(
            token in f"{case['name']} {case['note']} {' '.join(str(a) for a in case['args'])}"
            for token in only.split(",")
        )
    ]
    results = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['name']} | {case['note']}", flush=True)
        item = run_case(case)
        results.append(item)
        if "error" in item:
            print(f"    失败：{item['error']}", flush=True)
        else:
            for key in ("short", "long"):
                row = item[key]
                print(
                    f"    {key}: 提示 {row['prompt_tokens']} tok, 生成 {row['completion_tokens']} tok, "
                    f"{row['server_predicted_per_second']:.2f} tok/s, "
                    f"提示评估 {row['server_prompt_per_second']:.1f} tok/s",
                    flush=True,
                )
    out = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "docs"
        / "实践情况"
        / "data"
    )
    out.mkdir(parents=True, exist_ok=True)
    target = out / "bench_local_tokens.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
