"""Compute a persona control vector as the difference of residual-stream means.

Input: two activation dumps produced by capture_acts.exe, each holding
  n_prompts * (n_layers - 1) * n_embd float32 in layer-major order.

Output: one control vector file in the upstream layout (n_layers - 1 segments
of n_embd float32), ready for --control-vector.

"""

from __future__ import annotations

import argparse
import json
import math
import struct


def read_dump(path: str) -> list:
    """Read a dump into a list of prompts, each a list of layer vectors."""
    with open(path, "rb") as fh:
        raw = fh.read()
    total = len(raw) // 4
    floats = struct.unpack("<%df" % total, raw)
    return list(floats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positive", required=True)
    ap.add_argument("--negative", required=True)
    ap.add_argument("--n-embd", type=int, required=True)
    ap.add_argument("--n-layers", type=int, required=True)
    ap.add_argument("--n-prompts", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--normalize", action="store_true",
                    help="scale each layer vector to unit length before scaling")
    args = ap.parse_args()

    n_seg = args.n_layers - 1
    per_prompt = n_seg * args.n_embd

    pos = read_dump(args.positive)
    neg = read_dump(args.negative)
    if len(pos) != per_prompt * args.n_prompts or len(neg) != per_prompt * args.n_prompts:
        raise SystemExit("dump size mismatch: pos=%d neg=%d expected=%d"
                         % (len(pos), len(neg), per_prompt * args.n_prompts))

    out = []
    layer_norms = []
    for k in range(n_seg):
        base = k * args.n_embd
        vec = []
        for d in range(args.n_embd):
            p = sum(pos[pi * per_prompt + base + d] for pi in range(args.n_prompts)) / args.n_prompts
            n = sum(neg[pi * per_prompt + base + d] for pi in range(args.n_prompts)) / args.n_prompts
            vec.append(p - n)
        norm = math.sqrt(sum(v * v for v in vec))
        layer_norms.append(norm)
        if args.normalize and norm > 0:
            k_norm = norm / math.sqrt(args.n_embd)
            vec = [v / k_norm for v in vec]
        out.extend(vec)

    with open(args.out, "wb") as fh:
        fh.write(struct.pack("<%df" % len(out), *out))

    print(json.dumps({
        "out": args.out,
        "n_segments": n_seg,
        "floats": len(out),
        "bytes": len(out) * 4,
        "mean_layer_norm": round(sum(layer_norms) / len(layer_norms), 6),
        "layer_norms": [round(x, 4) for x in layer_norms],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
