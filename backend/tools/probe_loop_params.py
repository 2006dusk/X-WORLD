r"""抗循环采样参数对照实验。

现象：模型在单次生成内把自己的上一轮回复重复 2 到 4 遍。
历史里带着上一轮回复时更明显。本实验对同一段多轮序列，
比较不同采样参数下「单次生成内的重复」与「整轮相似度」。

用法：
    python backend\tools\probe_loop_params.py
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app.inference.repetition import split_sentences  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")

PROMPTS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
]

BASE_PARAMS = {
    "model_path": (
        "E:\\X-WORLD\\models\\hf\\"
        "ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"
    ),
    "n_ctx": 4096,
    "n_gpu_layers": 999,
    "kv_type": "f16",
    "tokenizer_type": "llamacpp",
}

PARAM_SETS = [
    ("base", {"top_k": 40, "min_p": 0.05}),
    ("freq_04", {"top_k": 40, "min_p": 0.05, "frequency_penalty": 0.4}),
    ("freq_08", {"top_k": 40, "min_p": 0.05, "frequency_penalty": 0.8}),
    ("pres_06", {"top_k": 40, "min_p": 0.05, "presence_penalty": 0.6}),
    ("minp_0", {"top_k": 40, "min_p": 0.0}),
    ("typ_095", {"top_k": 40, "min_p": 0.05, "typical_p": 0.95}),
    ("dry_08", {"top_k": 40, "min_p": 0.05, "dry_multiplier": 0.8, "dry_base": 1.75}),
    ("dry_12", {"top_k": 40, "min_p": 0.05, "dry_multiplier": 1.2, "dry_base": 1.75}),
]


def ratio(left, right):
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def internal_dup(text):
    """单次生成内的重复度：重复出现的句子占比。"""
    parts = [s.rstrip("。！？").strip() for s in split_sentences(text)]
    parts = [s for s in parts if len(s) >= 10]
    if len(parts) < 2:
        return 0.0
    return round(1 - len(set(parts)) / len(parts), 3)


def run_turn(client, session, prompt):
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": "p_demo",
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    parts = []
    started = time.time()
    with client.stream("POST", BASE + "/v1/chat/completions", json=request) as stream:
        for line in stream.iter_lines():
            if not line:
                continue
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                content = payload.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
    return "".join(parts), time.time() - started


def main():
    results = {}
    for name, params in PARAM_SETS:
        profile_id = "p_loop_" + name
        session = "s_loop_" + name
        profile = dict(BASE_PARAMS)
        profile.update(params)
        with httpx.Client(base_url=BASE, timeout=60.0) as admin:
            admin.post("/v1/profiles", json={
                "id": profile_id,
                "name": "loop " + name,
                "params": profile,
            })
            admin.post("/v1/models/active", json={
                "model_id": "xw-local-default",
                "profile_id": profile_id,
            })
            try:
                admin.delete("/v1/sessions/" + session + "?character_id=c_demo")
            except Exception:
                pass

        rows = []
        texts = []
        with httpx.Client(timeout=240.0) as client:
            for index, prompt in enumerate(PROMPTS):
                text, elapsed = run_turn(client, session, prompt)
                best_prev = 0.0
                for prev in texts:
                    best_prev = max(best_prev, ratio(text, prev))
                rows.append({
                    "turn": index + 1,
                    "prompt": prompt,
                    "chars": len(text),
                    "internal_dup": internal_dup(text),
                    "repeat_ratio": round(best_prev, 3),
                    "elapsed": round(elapsed, 2),
                    "text": text,
                })
                texts.append(text)

        summary = {
            "chars_max": max(r["chars"] for r in rows),
            "chars_avg": round(sum(r["chars"] for r in rows) / len(rows), 1),
            "internal_dup_max": max(r["internal_dup"] for r in rows),
            "internal_dup_avg": round(sum(r["internal_dup"] for r in rows) / len(rows), 3),
            "repeat_ratio_max": max(r["repeat_ratio"] for r in rows),
            "elapsed_avg": round(sum(r["elapsed"] for r in rows) / len(rows), 2),
        }
        results[name] = {"params": params, "summary": summary, "rows": rows}
        print(name + " " + json.dumps(summary, ensure_ascii=False))

    with httpx.Client(base_url=BASE, timeout=60.0) as admin:
        admin.post("/v1/models/active", json={
            "model_id": "xw-local-default",
            "profile_id": "p_default",
        })

    root = os.path.dirname(os.path.dirname(BACKEND))
    out = os.path.join(root, "..", "docs", "实践情况", "data", "sweep", "loop_params.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
