"""Multi-turn verification: prefix reuse stability plus control-vector behaviour.

Runs the same multi-turn conversation twice against one server instance:
  1. without a control vector (baseline arm)
  2. with a control vector (treatment arm)
and reports per-turn reuse counts, timings and output.

"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import time
import urllib.request
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

EXE = os.path.join(REPO, 'world-work-cpp', 'build', 'world-work-cpp.exe')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from local_paths import model_path

MODEL = model_path()
TMP = os.path.join(REPO, 'backend', 'tmp')
ASSETS = os.path.join(REPO, "..", 'docs', '理论支持', 'data', 'cvec')

TURNS = [
    "\u671b\u541b\u53ef\u6709\u4e8b\u53ef\u4ee4\u5dc5\u7a0b\uff1f",
    "\u518d\u8bf4\u4e00\u904d\u671b\u541b\u7684\u8fc7\u5f80\u3002",
    "\u671b\u541b\u82e5\u662f\u60f3\u5f53\u516c\u4e3b\uff0c\u5c31\u628a\u8fd9\u4efd\u5fc3\u8bdd\u5165\u8c37\u5427\u3002",
    "\u8bf7\u8bf4\u8bf4\u671b\u541b\u6700\u559c\u6b22\u7684\u4e00\u79cd\u98df\u7269\u3002",
    "\u90a3\u4eca\u65e5\u7684\u671d\u670d\u53ef\u6709\u8bf4\u6cd5\uff1f",
]


def post(url, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_ready(port, proc, timeout=200):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def run_arm(name, port, cvec_args):
    out = open(os.path.join(TMP, "mt_%s.out.log" % name), "w", encoding="utf-8")
    err = open(os.path.join(TMP, "mt_%s.err.log" % name), "w", encoding="utf-8")
    args = [EXE, "--model", MODEL, "--port", str(port),
            "--ctx-size", "16384", "--n-gpu-layers", "999",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--parallel", "1"]
    args += cvec_args
    proc = subprocess.Popen(args, stdout=out, stderr=err)
    turns = []
    try:
        if not wait_ready(port, proc):
            return {"name": name, "status": "start_failed", "turns": []}
        messages = [{"role": "system", "content":
                     "You are Li Hanyang, the empress of a Tang court. Speak in short classical Chinese."}]
        for i, text in enumerate(TURNS, 1):
            messages.append({"role": "user", "content": text})
            t0 = time.time()
            resp = post("http://127.0.0.1:%d/v1/chat/completions" % port,
                        {"messages": messages, "max_tokens": 220,
                         "temperature": 0.8, "top_p": 0.95, "seed": 1000 + i})
            wall = time.time() - t0
            msg = (resp.get("choices") or [{}])[0].get("message", {})
            content = msg.get("content") or ""
            usage = resp.get("usage") or {}
            timings = resp.get("timings") or {}
            turns.append({
                "turn": i, "user": text, "content": content,
                "wall_s": round(wall, 2),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "cached_tokens": usage.get("cached_tokens"),
                "n_prompt_reused": timings.get("n_prompt_reused"),
                "prompt_ms": round(timings.get("prompt_ms", 0), 1),
                "gen_ms": round(timings.get("gen_ms", 0), 1),
            })
            messages.append({"role": "assistant", "content": content})
        slots = get("http://127.0.0.1:%d/slots" % port)
        return {"name": name, "status": "ok", "turns": turns, "slots": slots}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        out.close()
        err.close()


def main():
    arms = [
        ("plain", 8096, []),
        ("cvec", 8097, ["--control-vector", os.path.join(ASSETS, "persona_cvec_r02.f32"),
                       "--cvec-scale", "1.0"]),
    ]
    results = []
    for name, port, cvec_args in arms:
        print("=== arm:", name, "===", flush=True)
        res = run_arm(name, port, cvec_args)
        results.append(res)
        for t in res.get("turns", []):
            print("  turn {turn}: cached={cached_tokens} reused={n_prompt_reused} "
                  "prompt_ms={prompt_ms} wall={wall_s} chars={completion_tokens}".format(**t),
                  flush=True)
    out_path = os.path.join(TMP, "cvec_multiturn.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print("saved:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
