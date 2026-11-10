"""Multi turn chat probe: checks the KV prefix reuse in the real chat pattern.

Turn N sends [system, u1, a1, u2, ...] - the cached "messages" part of the
previous turn is a prefix of the new one, so the KV cells stay valid and only
the new tail has to be decoded.
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def post(base: str, path: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8082")
    ap.add_argument("--turns", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=48)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    messages = [
        {"role": "system", "content": "You are Li Hanyang, the empress. Answer in short classical Chinese."},
    ]
    turns = [
        "Who are you?",
        "What do you want?",
        "Will you punish me?",
        "Then let us go.",
    ]

    report = {"base": args.base, "turns": []}

    for i in range(args.turns):
        messages.append({"role": "user", "content": turns[i % len(turns)]})
        body = {
            "messages": messages,
            "max_tokens": args.max_tokens,
            "temperature": 0.8,
            "top_p": 0.95,
            "seed": 7,
            "stream": False,
        }
        resp = post(args.base, "/v1/chat/completions", body)
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        timings = resp.get("timings") or {}
        entry = {
            "turn": i + 1,
            "prompt_tokens": (resp.get("usage") or {}).get("prompt_tokens"),
            "n_prompt_reused": timings.get("n_prompt_reused"),
            "prompt_ms": round(float(timings.get("prompt_ms") or 0), 1),
            "gen_ms": round(float(timings.get("gen_ms") or 0), 1),
            "content": str(msg.get("content") or "")[:160],
            "reasoning": str(msg.get("reasoning_content") or "")[:160],
        }
        report["turns"].append(entry)
        messages.append({"role": "assistant", "content": str(msg.get("content") or "")})

    try:
        with urllib.request.urlopen(args.base + "/slots", timeout=30) as resp:
            report["slots"] = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        report["slots"] = {"error": "%s: %s" % (type(exc).__name__, exc)}

    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
