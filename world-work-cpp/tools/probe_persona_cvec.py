"""Check whether a persona control vector shifts the model behaviour.

Runs the same neutral prompt against three server instances:
  baseline : no control vector
  persona  : the persona vector at a given scale
and prints the outputs side by side.

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

PROMPTS = [
    [{"role": "system", "content": "You are a helpful assistant."},
     {"role": "user", "content": "What is the weather like today?"}],
    [{"role": "system", "content": "You are a helpful assistant."},
     {"role": "user", "content": "Tell me about yourself."}],
]


def post(url, payload, timeout=240):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        # A badly scaled control vector can drive the model into emitting raw
        # bytes. Record it instead of crashing the probe.
        return {"_decode_error": True,
                "_bytes_head": raw[:120].hex(),
                "_bytes_len": len(raw)}


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


def run(name, port, cvec_args):
    out = open(os.path.join(TMP, "pv_%s.out.log" % name), "w", encoding="utf-8")
    err = open(os.path.join(TMP, "pv_%s.err.log" % name), "w", encoding="utf-8")
    args = [EXE, "--model", MODEL, "--port", str(port),
            "--ctx-size", "16384", "--n-gpu-layers", "999",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--parallel", "1"]
    args += cvec_args
    proc = subprocess.Popen(args, stdout=out, stderr=err)
    texts = []
    try:
        if not wait_ready(port, proc):
            tail = open(err.name, encoding="utf-8", errors="replace").read()[-600:]
            return {"name": name, "status": "start_failed", "stderr_tail": tail}
        for msgs in PROMPTS:
            body = {"messages": msgs, "max_tokens": 90, "temperature": 0.0, "seed": 99}
            resp = post("http://127.0.0.1:%d/v1/chat/completions" % port, body)
            if resp.get("_decode_error"):
                texts.append("[decode error] " + resp["_bytes_head"][:80])
            else:
                texts.append((resp.get("choices") or [{}])[0].get("message", {}).get("content", ""))
        return {"name": name, "status": "ok", "texts": texts}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        out.close()
        err.close()


def main():
    cvec = os.path.join(TMP, "persona_cvec_r02.f32")
    arms = [
        ("r02_s05", 8099, ["--control-vector", cvec, "--cvec-scale", "0.5"]),
        ("r02_s10", 8100, ["--control-vector", cvec, "--cvec-scale", "1.0"]),
        ("r02_s20", 8101, ["--control-vector", cvec, "--cvec-scale", "2.0"]),
    ]
    results = []
    for name, port, args in arms:
        print("=== arm:", name, "===", flush=True)
        res = run(name, port, args)
        results.append(res)
        for i, t in enumerate(res.get("texts", [])):
            print("  prompt %d: %s" % (i, t[:220].replace(chr(10), " ")), flush=True)
    path = os.path.join(TMP, "persona_probe.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print("saved:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
