"""Probe the world-work-cpp server: template parity, completions, KV prefix reuse.

Run:  python tools\probe_wwc.py --base http://127.0.0.1:8082
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

from jinja2 import Environment


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post(base: str, path: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_stream(base: str, path: str, payload: dict) -> list:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    events = []
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                events.append({"done": True})
                break
            events.append(json.loads(body))
    return events


def reference_prompt(template: str, messages: list, add_generation_prompt: bool) -> str:
    env = Environment()
    env.policies["json.dumps_kwargs"] = {"ensure_ascii": False}
    tpl = env.from_string(template)
    return tpl.render(messages=messages, add_generation_prompt=add_generation_prompt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8082")
    ap.add_argument("--template-file", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    report = {"base": args.base}

    report["health"] = get(args.base, "/health")
    props = get(args.base, "/props")
    report["props"] = {
        "model_desc": props.get("model_desc"),
        "chat_template_mode": props.get("chat_template_mode"),
        "n_ctx": props.get("n_ctx"),
        "n_ctx_seq": props.get("n_ctx_seq"),
        "n_vocab": props.get("n_vocab"),
    }

    messages = [
        {"role": "system", "content": "You are Li Hanyang, the empress. Speak in short classical Chinese."},
        {"role": "user", "content": "Who are you?"},
        {"role": "assistant", "content": "I am Li Hanyang."},
        {"role": "user", "content": "What do you want?"},
    ]

    # --- template parity against the Jinja2 reference ---
    ours = post(args.base, "/apply-template", {"messages": messages, "add_generation_prompt": True})
    template = props.get("chat_template") or ""
    if args.template_file:
        with open(args.template_file, "r", encoding="utf-8") as fh:
            template = fh.read()
    if not template:
        raise SystemExit("no chat template available: /props returned none and no --template-file given")
    ref = reference_prompt(template, messages, True)
    report["template_parity"] = {
        "identical": ours.get("prompt") == ref,
        "ours_len": len(ours.get("prompt", "")),
        "ref_len": len(ref),
        "ours": ours.get("prompt", ""),
        "ref": ref,
    }

    # --- tokenize ---
    report["tokenize"] = post(args.base, "/tokenize", {"content": "hello world"})

    # --- non streaming completion, run twice to exercise the prefix cache ---
    body = {
        "messages": messages,
        "max_tokens": 48,
        "temperature": 0.8,
        "top_p": 0.95,
        "seed": 42,
    }
    t0 = time.time()
    first = post(args.base, "/v1/chat/completions", body)
    first_wall = time.time() - t0
    second = post(args.base, "/v1/chat/completions", body)
    second_wall = time.time() - t0 - first_wall

    def summarize(resp, wall):
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        return {
            "wall_s": round(wall, 3),
            "content_len": len(msg.get("content") or ""),
            "reasoning_len": len(msg.get("reasoning_content") or ""),
            "finish_reason": choice.get("finish_reason"),
            "usage": resp.get("usage"),
            "timings": resp.get("timings"),
        }

    report["first_call"] = summarize(first, first_wall)
    report["second_call"] = summarize(second, second_wall)
    report["content_sample"] = ((first.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")[:400]

    # --- shared prefix but different tail ---
    body2 = dict(body)
    body2["messages"] = messages[:-1] + [{"role": "user", "content": "What do you want, exactly?"}]
    third = post(args.base, "/v1/chat/completions", body2)
    report["changed_tail"] = summarize(third, 0.0)
    report["slots"] = get(args.base, "/slots")

    # --- streaming ---
    body3 = dict(body)
    body3["stream"] = True
    body3["messages"] = [{"role": "system", "content": "You are Li Hanyang."}, {"role": "user", "content": "Say hello."}]
    events = post_stream(args.base, "/v1/chat/completions", body3)
    chunks = [e for e in events if not e.get("done")]
    contents = "".join(
        (c.get("choices") or [{}])[0].get("delta", {}).get("content", "") for c in chunks
    )
    reasonings = "".join(
        (c.get("choices") or [{}])[0].get("delta", {}).get("reasoning_content", "") for c in chunks
    )
    last = chunks[-1] if chunks else {}
    report["stream"] = {
        "n_events": len(events),
        "n_chunks": len(chunks),
        "content_len": len(contents),
        "reasoning_len": len(reasonings),
        "finish_reason": (last.get("choices") or [{}])[0].get("finish_reason"),
        "usage": last.get("usage"),
    }

    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
