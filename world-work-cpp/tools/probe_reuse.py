"""Compare KV reuse semantics of a llama.cpp compatible server.

Sends three requests in a row:
  1. system + userA            (cold)
  2. system + userA            (identical prompt)
  3. system + userB            (same system, different tail)

and dumps the server side timings plus /slots, so the reuse rules of the
server under test become visible.
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


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8083")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    system = "You are Li Hanyang, the empress of a northern court. Answer in short classical Chinese."
    user_a = "Who are you?"
    user_b = "What do you want, exactly?"

    report = {"base": args.base}

    def chat(user: str, tag: str) -> None:
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 1,
            "temperature": 0.7,
            "stream": False,
        }
        try:
            resp = post(args.base, "/v1/chat/completions", body)
        except Exception as exc:
            report[tag] = {"error": "%s: %s" % (type(exc).__name__, exc)}
            return
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        report[tag] = {
            "timings": resp.get("timings"),
            "usage": resp.get("usage"),
            "text": str(msg.get("content") or "")[:120],
            "reasoning": str(msg.get("reasoning_content") or "")[:120],
        }

    chat(user_a, "cold")
    chat(user_a, "same_prompt")
    chat(user_b, "same_system_other_tail")
    try:
        report["slots"] = get(args.base, "/slots")
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
