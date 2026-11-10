r"""本地 RP 输出模型速率基准（差分法口径）。

向 /v1/chat/completions 请求生成。对话模板由服务端按权重自带的模板渲染，
客户端不再本地拼 chatml，再按差分法拆出预填充与解码两段耗时：

- T_A：同一提示生成 1 token 的端到端耗时
- T_B：同一提示生成 256 token 的端到端耗时
- 解码速率 = (256 - 1) / (T_B - T_A)
- 预填充耗时 = T_A - 1 / 解码速率
- 预填充速率 = 提示 token 数 / 预填充耗时

两个请求之间插入一次不同提示的请求，避免命中 KV 前缀复用
（命中后只评估剩余 token，会让预填充耗时失真）。

三处版本事实：
- 自写服务端没有模板开关：对话模板取权重自带的 tokenizer.chat_template，
  /v1/chat/completions 由服务端自己渲染，客户端不再本地拼 chatml。
- 自写服务端不认 min_tokens，请求给的是 max_tokens；实际生成多少 token
  以响应里的 usage.completion_tokens 为准，提前收尾的模型按实数作差。
- 响应里有 timings 字段（prompt_ms、gen_ms、gen_per_second），
  拿它与服务端自报的解码速率交叉校验。

用法：
    python backend\tools\bench_local_final.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(BACKEND_ROOT))

from bench_local import (  # noqa: E402
    BENCH_CASES,
    CASES,
    HOST,
    LONG_PROMPT,
    SHORT_PROMPT,
    free_port,
    wait_gpu_free,
)
from wwc_server import start_server, stop_server, wait_ready  # noqa: E402

N_SHORT = 1
N_LONG = 256
SEED = 42

# 打断 KV 前缀复用用的旁路提示：内容与服务端渲染出的提示完全不同。
FILLER_MESSAGES = [
    {"role": "system", "content": "仅用于打断 KV 前缀复用。"},
    {"role": "user", "content": "标记。"},
]


def prompt_messages(prompt: dict[str, str]) -> list[dict[str, str]]:
    """提示转成消息列表。

    模板在服务端渲染，这里只给结构化消息。
    """
    return [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"]},
    ]


def generate(
    base: str, messages: list[dict[str, str]], n_predict: int
) -> tuple[float, int, int, float | None]:
    """生成至多 n_predict token。

    返回端到端耗时、提示 token 数、实际生成 token 数与服务端自报的解码速率。
    """
    payload = {
        "model": "local",
        "messages": messages,
        "max_tokens": n_predict,
        "temperature": 0.8,
        "seed": SEED,
    }
    started = time.perf_counter()
    resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=1800.0)
    resp.raise_for_status()
    wall = time.perf_counter() - started
    body = resp.json()
    usage = body.get("usage") or {}
    timings = body.get("timings") or {}
    server_rate = timings.get("gen_per_second")
    return (
        wall,
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        float(server_rate) if server_rate else None,
    )


def measure(base: str, prompt: dict[str, str]) -> dict[str, object]:
    """差分法拆分预填充与解码。"""
    messages = prompt_messages(prompt)
    t_short, prompt_tokens, tokens_short, rate_short = generate(base, messages, N_SHORT)
    # 打断 KV 前缀复用：让下一次请求的首 token 与本提示不同
    generate(base, FILLER_MESSAGES, N_SHORT)
    t_long, prompt_tokens_long, tokens_long, rate_long = generate(base, messages, N_LONG)
    if prompt_tokens_long:
        prompt_tokens = prompt_tokens_long

    # 用实际生成的 token 数作差。部分模型会提前收尾结束，
    # 早先按假定 token 数算会把速率抬高一个量级。
    span = t_long - t_short
    token_span = tokens_long - tokens_short
    if token_span <= 0:
        token_span = N_LONG - N_SHORT
    decode_rate = token_span / span if span > 0 else 0.0
    prefill_s = t_short - (tokens_short / decode_rate) if decode_rate > 0 else 0.0
    prefill_rate = prompt_tokens / prefill_s if prefill_s > 0 else 0.0
    return {
        "prompt_chars": len(prompt["system"]) + len(prompt["user"]),
        "prompt_tokens": prompt_tokens,
        "tokens_short": tokens_short,
        "tokens_long": tokens_long,
        "t_short_s": round(t_short, 3),
        "t_long_s": round(t_long, 3),
        "decode_tok_per_s": round(decode_rate, 2),
        "prefill_s": round(prefill_s, 3),
        "prefill_tok_per_s": round(prefill_rate, 1),
        "server_eval_tok_per_s": rate_long if rate_long is not None else rate_short,
    }


def measure_ttft(base: str) -> dict[str, float]:
    """流式请求测首字延迟。"""
    payload = {
        "model": "local",
        "messages": [{"role": "user", "content": "说一句你好"}],
        "max_tokens": 16,
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
    print("== 本地 RP 输出模型速率基准（差分法，生成 1 与 256 token 作差）==", flush=True)
    only = os.environ.get("BENCH_CASES", "").strip()
    # BENCH_CASES 置空时用默认档位表；置了名字则从 BENCH_CASES 里挑，
    # 名字、备注或参数任一命中即算匹配。
    source = BENCH_CASES if only else CASES
    cases = [
        case
        for case in source
        if not only
        or any(
            token
            in f"{case['name']} {case['note']} {' '.join(str(a) for a in case['args'])}"
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
                    f"    {key}: 提示 {row['prompt_tokens']} tok, "
                    f"解码 {row['decode_tok_per_s']} tok/s, "
                    f"预填充 {row['prefill_tok_per_s']} tok/s "
                    f"({row['prefill_s']}s)",
                    flush=True,
                )
    out = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data"
    out.mkdir(parents=True, exist_ok=True)
    target = out / "bench_local_final.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
