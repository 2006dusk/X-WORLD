# -*- coding: utf-8 -*-
"""本机推理栈的端点与计量字段探针。

对着已经跑起来的推理服务端（本仓库的自写服务端 world-work-cpp），
用同一份真实提示量六件事：
1. 冷启动就绪时间与首轮耗时；
2. 前缀复用：同一段长前缀连发两次，第二次的预填时间省了多少；
3. 并发：两条**不同前缀**的请求同时发，看是真并行还是串行；
4. SSE 原始格式：上游给的是什么形状，适配层能不能直接吃；
5. token 计数端点与其它端点：对接时要改哪些；
6. 计量字段：usage 与 timings 的形状。

服务端要先自己起好，例如：
    world-work-cpp --model <gguf 路径> --host 127.0.0.1 --port 8081

用法：
    python backend\tools\probe_server_endpoints.py --base http://127.0.0.1:8081 --label wwc

--label 给这轮测量起个名字（例如 wwc），结果按名字追加进 --out 指定的 JSON。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DATA = Path(os.environ.get("XWORLD_DATA_DIR", Path(os.environ.get("APPDATA", "")) / "X-WORLD"))

CARD_A = "c_card_01"
CARD_B = "c_card_03"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_prompt(card_id: str = CARD_A) -> tuple[str, str]:
    """用真实数据拼一段长提示：预设 system + 角色卡 + 世界书地区条目。

    不自己编内容：真实会话的提示就长这样，量出来的数才有意义。
    """
    parts: list[str] = []
    preset = _read_json(DATA / "presets" / "p_builtin_rp.json") or {}
    for group in preset.get("prompts", []) or []:
        if str(group.get("role") or "") == "system":
            content = str(group.get("content") or "").strip()
            if content:
                parts.append(content)

    card = _read_json(DATA / "cards" / card_id / "card.json") or {}
    payload = card.get("data") or {}
    char_bits = [str(payload.get(k) or "").strip() for k in ("name", "identity", "behavior", "speech")]

    world = ""
    for book_path in sorted((DATA / "world_books").glob("*.json")):
        book = _read_json(book_path) or {}
        for entry in book.get("entries", []) or []:
            if str(entry.get("kind") or "") == "region":
                content = str(entry.get("content") or "").strip()
                if content:
                    world += content + "\n"
    if not world:
        world = "（无世界书）"

    system = "\n".join(parts + [bit for bit in char_bits if bit] + [world]).strip()
    return system, "（你又想起刚才门口那个人）"


def _timings(body: dict[str, Any]) -> dict[str, Any]:
    """不同推理栈的计时字段名不一样，统一成同一组键。

    自写服务端用的是 gen_per_second / gen_ms / prompt_per_second。
    """
    t = body.get("timings") or {}
    if not isinstance(t, dict):
        t = {}
    prompt_ms = t.get("prompt_ms", t.get("timings_prompt_ms"))
    prompt_per_s = t.get("prompt_per_second", t.get("timings_prompt_per_second"))
    predicted_n = t.get("predicted_n", t.get("timings_predicted_n"))
    predicted_per_s = t.get("predicted_per_second", t.get("timings_predicted_per_second"))
    per_token_ms = t.get("timings_per_token_ms")
    if per_token_ms and predicted_per_s is None and per_token_ms > 0:
        predicted_per_s = round(1000.0 / per_token_ms, 2)
    return {
        "prompt_ms": prompt_ms,
        "prompt_per_s": prompt_per_s,
        "predicted_n": predicted_n,
        "predicted_per_s": predicted_per_s,
        "raw_keys": sorted(t.keys()),
    }


def _chat(base: str, system: str, user: str, max_tokens: int = 96) -> dict[str, Any]:
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }
    started = time.perf_counter()
    resp = httpx.post(base + "/v1/chat/completions", json=payload, timeout=600.0)
    elapsed = time.perf_counter() - started
    resp.raise_for_status()
    body = resp.json()
    usage = body.get("usage") or {}
    out: dict[str, Any] = {
        "elapsed_s": round(elapsed, 3),
        "usage_keys": sorted(usage.keys()) if isinstance(usage, dict) else [],
        "prompt_tokens": (usage.get("prompt_tokens") if isinstance(usage, dict) else None),
        "completion_tokens": (usage.get("completion_tokens") if isinstance(usage, dict) else None),
        "timings": _timings(body),
    }
    choices = body.get("choices") or []
    if choices:
        out["text_len"] = len(str((choices[0].get("message") or {}).get("content") or ""))
    return out


def _diff_concurrency(base: str, system_a: str, system_b: str) -> dict[str, Any]:
    """两条**不同前缀**的请求同时发。

    前缀相同的话，后一条会命中同一条槽的缓存，测不出多槽位是真并行还是排队。
    """
    payload_a = {
        "messages": [
            {"role": "system", "content": system_a},
            {"role": "user", "content": "（你先开口）"},
        ],
        "max_tokens": 96,
        "temperature": 0.7,
        "stream": False,
    }
    payload_b = dict(payload_a)
    payload_b["messages"] = [
        {"role": "system", "content": system_b},
        {"role": "user", "content": "（你先开口）"},
    ]
    started = time.perf_counter()
    with httpx.Client(timeout=600.0) as client:
        r1 = client.post(base + "/v1/chat/completions", json=payload_a)
        r2 = client.post(base + "/v1/chat/completions", json=payload_b)
    wall = time.perf_counter() - started
    out: dict[str, Any] = {"wall_s": round(wall, 3)}
    for key, resp in (("a", r1), ("b", r2)):
        try:
            out[key] = _timings(resp.json())
        except Exception:
            out[key] = {"status": resp.status_code}
    return out


def _capture_sse(base: str, system: str, user: str) -> dict[str, Any]:
    """抓原始 SSE 前几行，看上游给的是什么形状。"""
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 24,
        "temperature": 0.7,
        "stream": True,
    }
    lines: list[str] = []
    try:
        with httpx.stream("POST", base + "/v1/chat/completions", json=payload, timeout=120.0) as resp:
            for raw in resp.iter_lines():
                if raw:
                    lines.append(raw[:160])
                if len(lines) >= 8:
                    break
    except Exception as exc:
        return {"error": type(exc).__name__, "lines": lines}
    return {
        "has_event_lines": any(line.startswith("event:") for line in lines),
        "has_data_lines": any(line.startswith("data:") for line in lines),
        "lines": lines,
    }


def _endpoints(base: str) -> dict[str, Any]:
    """探测各端点能不能用、返回什么形状。这是适配层要改的地方。"""
    out: dict[str, Any] = {}
    for name, path, body in (
        ("health", "/health", None),
        ("extras_tokenize_count", "/extras/tokenize/count", {"input": "你好 world"}),
        ("tokenize", "/tokenize", {"content": "你好 world"}),
        ("props", "/props", None),
        ("slots", "/slots", None),
        ("models", "/v1/models", None),
    ):
        try:
            if body is None:
                resp = httpx.get(base + path, timeout=20.0)
            else:
                resp = httpx.post(base + path, json=body, timeout=20.0)
            try:
                data = resp.json()
            except Exception:
                data = None
            shape = ""
            if isinstance(data, dict):
                shape = ",".join(sorted(data.keys())[:8])
            out[name] = {"status": resp.status_code, "keys": shape}
        except Exception as exc:
            out[name] = {"error": type(exc).__name__}
    return out


def run(base: str, label: str) -> dict[str, Any]:
    system, user = build_prompt(CARD_A)
    system_b, _ = build_prompt(CARD_B)
    report: dict[str, Any] = {
        "label": label,
        "base": base,
        "system_chars": len(system),
        "samples": {},
    }

    # 只生成一个 token：wall 差不多就是预填 + 首字延迟，把预填单独量出来
    report["samples"]["prefill_only"] = _chat(base, system, user, max_tokens=1)
    report["samples"]["prefill_only_2"] = _chat(base, system, user, max_tokens=1)
    report["samples"]["cold_first"] = _chat(base, system, user)
    report["samples"]["prefix_reuse"] = _chat(base, system, "（你又想起另一个人）")
    report["samples"]["prefix_same"] = _chat(base, system, user)
    report["samples"]["concurrent_diff_prefix"] = _diff_concurrency(base, system, system_b)
    report["sse"] = _capture_sse(base, system, user)
    report["endpoints"] = _endpoints(base)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--label", required=True, help="给这轮测量起个名字，例如 wwc")
    parser.add_argument("--out", default=str(BACKEND / "tmp" / "server_endpoints.json"))
    args = parser.parse_args()

    report = run(args.base, args.label)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    existing = [row for row in existing if row.get("label") != args.label]
    existing.append(report)
    out_path.write_text(json.dumps(existing, ensure_ascii=True, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=1))


if __name__ == "__main__":
    main()
