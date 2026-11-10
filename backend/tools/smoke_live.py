r"""实时冒烟：后端健康、推理服务可达、真实分词可用、跑一轮流式回合。

用法：
    python backend\tools\smoke_live.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import httpx

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")

REQUEST = {
    "model": "xw-local-default",
    "messages": [{"role": "user", "content": "你还在地址U吗？"}],
    "session": "s_demo",
    "stream": True,
    "xw": {
        "character_id": "c_demo",
        "preset_id": "p_demo",
        "world_book_ids": ["w_demo"],
        "memory_mode": "hybrid",
        "max_context_tokens": 8192,
    },
}

DIALOGUE_RE = re.compile("[“”「」]")


def main() -> int:
    failures = []

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(BASE + "/healthz")
        print("healthz -> " + str(resp.status_code))
        if resp.status_code != 200:
            failures.append("healthz")

        resp = client.get(BASE + "/v1/tokenizers")
        body = resp.json()
        probe = body.get("probe_result") or {}
        print("tokenizer llamacpp_available=" + str(probe.get("llamacpp_available"))
              + " approx=" + str(probe.get("approx_count"))
              + " llamacpp=" + str(probe.get("llamacpp_count")))
        if not probe.get("llamacpp_available"):
            failures.append("tokenizer_not_real")

        resp = client.get(BASE + "/v1/inference/status")
        body = resp.json()
        print("inference reachable=" + str(body.get("service", {}).get("reachable"))
              + " profile=" + str(body.get("profile", {}).get("name")))

        resp = client.get(BASE + "/v1/profiles")
        print("profiles -> " + resp.text[:200])

    parts = []
    events = []
    started = time.time()
    first = None
    with httpx.Client(timeout=180.0) as client:
        with client.stream("POST", BASE + "/v1/chat/completions", json=REQUEST) as stream:
            status = stream.status_code
            for line in stream.iter_lines():
                if not line:
                    continue
                if line.startswith("event:"):
                    events.append(line[6:].strip())
                elif line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload.get("content"), str) and payload["content"]:
                        if first is None:
                            first = time.time() - started
                        parts.append(payload["content"])
                    if payload.get("budget"):
                        print("budget -> " + json.dumps(payload["budget"], ensure_ascii=False))
                    if "completion_tokens" in payload and payload.get("completion_tokens"):
                        print("completion_tokens -> " + str(payload["completion_tokens"]))

    elapsed = time.time() - started
    text = "".join(parts)
    print("HTTP -> " + str(status))
    print("events -> " + " ".join(events[:6]))
    print("ttft -> " + ("%.2f s" % first if first else "n/a"))
    print("elapsed -> %.2f s" % elapsed)
    print("chars -> " + str(len(text)))
    print("dialogue_quotes -> " + str(len(DIALOGUE_RE.findall(text))))

    if status != 200:
        failures.append("chat_http_" + str(status))
    if not text.strip():
        failures.append("empty_reply")
    if not DIALOGUE_RE.findall(text):
        failures.append("no_dialogue")
    if events and events[0] != "context":
        failures.append("first_event")
    if events and events[-1] != "done":
        failures.append("last_event")

    print()
    print(text)
    print()

    if failures:
        print("FAILURES: " + " | ".join(failures))
        return 1
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
