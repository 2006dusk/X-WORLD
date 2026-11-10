"""候选模型的单轮 RP 生成抽样。

起服务后经 /v1/chat/completions 发一条真实 RP 提示词，
由服务端套用模型自带的对话模板，取回中文生成结果作人工比对。

用法：
    python backend\tools\sample_rp.py <档位名> [...]
    python backend\tools\sample_rp.py ArityFlow Tifa
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

from bench_local import CASES, HOST, SHORT_PROMPT, free_port, wait_gpu_free  # noqa: E402
from wwc_server import start_server, stop_server, wait_ready  # noqa: E402

MAX_TOKENS = 400
OUT_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "rp_samples"


def sample(case: dict[str, object]) -> dict[str, object]:
    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(str(case["model"]), list(case["args"]), port)
    record: dict[str, object] = {
        "name": case["name"],
        "note": case["note"],
        "args": " ".join(str(a) for a in case["args"]),
    }
    try:
        if not wait_ready(base, proc):
            record["error"] = "服务启动超时"
            return record
        time.sleep(1.0)
        resp = httpx.post(
            f"{base}/v1/chat/completions",
            json={
                "model": "local",
                "messages": [
                    {"role": "system", "content": SHORT_PROMPT["system"]},
                    {"role": "user", "content": SHORT_PROMPT["user"]},
                ],
                "max_tokens": MAX_TOKENS,
                "temperature": 0.8,
                "seed": 42,
            },
            timeout=1800.0,
        )
        resp.raise_for_status()
        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        record["content"] = message.get("content") or ""
        record["finish_reason"] = choice.get("finish_reason")
        record["usage"] = body.get("usage")
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop_server(proc, log_path, handle)
        wait_gpu_free()
    return record


def main() -> int:
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    cases = [
        case
        for case in CASES
        if not wanted
        or any(token.lower() in str(case["name"]).lower() for token in wanted)
    ]
    if not cases:
        print("没有匹配的档位")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['name']}", flush=True)
        item = sample(case)
        results.append(item)
        if "error" in item:
            print(f"    失败：{item['error']}", flush=True)
        else:
            print(f"    生成 {len(str(item['content']))} 字", flush=True)
    target = OUT_DIR / "samples.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
