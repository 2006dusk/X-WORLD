"""测流式响应的 usage 支持情况，供 probe_adult 取速率时参考。

用法：
    python tools\probe_stream_usage.py
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
from multi_turn import start_server, stop_server, wait_ready  # noqa: E402

MODEL = r"E:\X-WORLD\models\hf\ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"
ARGS = ["--ctx-size", "4096", "--n-gpu-layers", "999"]


def main() -> int:
    port = free_port()
    base = "http://{}:{}".format(HOST, port)
    proc, log_path, handle = start_server(MODEL, ARGS, port)
    try:
        if not wait_ready(base, proc):
            print("服务启动超时")
            return 1
        time.sleep(1.0)
        print("=== 非流式，看 usage ===")
        payload = {
            "model": "local",
            "messages": [{"role": "user", "content": "说一句你好，五个字以内。"}],
            "max_tokens": 32,
            "temperature": 0.8,
        }
        started = time.perf_counter()
        resp = httpx.post(base + "/v1/chat/completions", json=payload, timeout=300.0)
        resp.raise_for_status()
        wall = time.perf_counter() - started
        body = resp.json()
        print("usage", body.get("usage"))
        print("wall", round(wall, 3))
        print("content", repr((body.get("choices") or [{}])[0].get("message", {}).get("content")))
    finally:
        stop_server(proc, log_path, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
