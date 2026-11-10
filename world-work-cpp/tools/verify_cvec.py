"""Verify the control-vector loader contract end to end.

Each case starts the server with different --control-vector settings and
compares the generated text against a baseline (no control vector).

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
# Test assets live with the archived experiment data so the suite is rerunnable.
ASSETS = os.path.join(REPO, "..", 'docs', '理论支持', 'data', 'cvec')

PROMPT = [{"role": "system", "content": "You are a helpful assistant."},
           {"role": "user", "content": "Name three colors."}]


def post(url, payload, timeout=180):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def run_case(name, port, cvec_args):
    """Start the server with cvec args, send one prompt, return the text."""
    out = open(os.path.join(TMP, "cvec_%s.out.log" % name), "w", encoding="utf-8")
    err = open(os.path.join(TMP, "cvec_%s.err.log" % name), "w", encoding="utf-8")
    args = [EXE, "--model", MODEL, "--port", str(port),
            "--ctx-size", "16384", "--n-gpu-layers", "999",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--parallel", "1"]
    args += cvec_args
    proc = subprocess.Popen(args, stdout=out, stderr=err)
    try:
        if not wait_ready(port, proc):
            tail = open(err.name, encoding="utf-8", errors="replace").read()[-900:]
            return {"name": name, "status": "start_failed", "stderr_tail": tail}
        body = {"messages": PROMPT, "max_tokens": 48, "temperature": 0.0, "seed": 1234}
        resp = post("http://127.0.0.1:%d/v1/chat/completions" % port, body)
        content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"name": name, "status": "ok", "text": content,
                "usage": resp.get("usage"),
                "timings": resp.get("timings")}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        out.close()
        err.close()


def main():
    cases = [
        ("baseline", 8090, []),
        ("zero", 8091, ["--control-vector", os.path.join(ASSETS, "cvec_zero.f32")]),
        ("ones", 8092, ["--control-vector", os.path.join(ASSETS, "cvec_ones.f32")]),
        ("ramp", 8093, ["--control-vector", os.path.join(ASSETS, "cvec_ramp.f32")]),
        ("badsize", 8094, ["--control-vector", os.path.join(ASSETS, "cvec_badsize.f32")]),
        ("layered", 8095, ["--control-vector", os.path.join(ASSETS, "cvec_ramp.f32"),
                          "--cvec-layer-start", "8", "--cvec-layer-end", "16",
                          "--cvec-scale", "0.5"]),
    ]
    results = []
    for name, port, cvec_args in cases:
        print("=== case:", name, "===", flush=True)
        res = run_case(name, port, cvec_args)
        results.append(res)
        print(json.dumps(res, ensure_ascii=False)[:900], flush=True)
    print()
    print("=== SUMMARY ===")
    base = next((r for r in results if r["name"] == "baseline"), None)
    base_text = (base or {}).get("text", "")
    for r in results:
        if r["status"] != "ok":
            print(r["name"], "->", r["status"])
            continue
        same = (r["text"] == base_text)
        print(r["name"], "-> same_as_baseline:", same, "| chars:", len(r["text"]))
    out_path = os.path.join(TMP, "cvec_verify.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print("saved:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
