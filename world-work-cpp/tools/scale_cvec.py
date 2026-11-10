"""Scale a control vector relative to the residual stream it is added to.

The upstream control vector is added straight to the residual stream:
  residual[il] += v[il]
So the magnitude of v must be comparable to the residual magnitude, otherwise
the injection overwhelms the model and the output degenerates.

This tool measures the residual norm per layer from an activation dump and
rescales the control vector so that, per layer,
  |v[il]| = ratio * |residual[il]|

"""

from __future__ import annotations

import argparse
import json
import math
import struct


def read_floats(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return struct.unpack("<%df" % (len(raw) // 4), raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cvec", required=True)
    ap.add_argument("--acts", required=True)
    ap.add_argument("--n-embd", type=int, required=True)
    ap.add_argument("--n-layers", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ratio", type=float, default=0.05)
    args = ap.parse_args()

    n_seg = args.n_layers - 1
    acts = read_floats(args.acts)
    vec = list(read_floats(args.cvec))
    n_prompts = len(acts) // (n_seg * args.n_embd)
    if n_prompts < 1:
        raise SystemExit("activation dump too small")

    out = []
    report = []
    for k in range(n_seg):
        base = k * args.n_embd
        norms = []
        for pi in range(n_prompts):
            off = pi * n_seg * args.n_embd + base
            norms.append(math.sqrt(sum(x * x for x in acts[off:off + args.n_embd])))
        r_norm = sum(norms) / len(norms)
        v_norm = math.sqrt(sum(x * x for x in vec[base:base + args.n_embd]))
        target = args.ratio * r_norm
        factor = (target / v_norm) if v_norm > 0 else 0.0
        out.extend(x * factor for x in vec[base:base + args.n_embd])
        report.append({"layer": k + 1, "residual_norm": round(r_norm, 4),
                       "cvec_norm": round(v_norm, 4), "factor": round(factor, 6)})

    with open(args.out, "wb") as fh:
        fh.write(struct.pack("<%df" % len(out), *out))

    print(json.dumps({"out": args.out, "ratio": args.ratio,
                      "n_prompts": n_prompts, "layers": report},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
