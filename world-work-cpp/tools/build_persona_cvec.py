"""Build a persona control vector by differencing residual-stream means.

Method (contrastive activation addition, no HF weights needed):
  1. run the model on N positive prompts (the persona voice) and on N negative
     prompts (a neutral assistant voice), collecting the residual stream at the
     end of each layer via the llama.cpp eval callback;
  2. v[il] = mean(positive, il) - mean(negative, il);
  3. write the result in the upstream layout: segment k -> model layer k+1,
     n_layers - 1 segments of n_embd float32.

"""

from __future__ import annotations

import argparse
import json
import math
import struct


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-embd", type=int, required=True)
    ap.add_argument("--n-layers", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--mode", default="demo", choices=["demo", "seed"])
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    n_segments = args.n_layers - 1

    if args.mode == "demo":
        values = []
        for k in range(n_segments):
            for d in range(args.n_embd):
                values.append(math.sin(d * 0.0007 + k * 0.11) * 0.02)
    else:
        with open(args.dump, "r", encoding="utf-8") as fh:
            dump = json.load(fh)
        pos = dump["positive"]
        neg = dump["negative"]
        values = []
        for k in range(n_segments):
            for d in range(args.n_embd):
                p = sum(row[k][d] for row in pos) / len(pos)
                n = sum(row[k][d] for row in neg) / len(neg)
                values.append((p - n) * args.scale)

    with open(args.out, "wb") as fh:
        fh.write(struct.pack("<%df" % len(values), *values))

    print(json.dumps({"path": args.out, "mode": args.mode,
                      "n_segments": n_segments, "floats": len(values),
                      "bytes": len(values) * 4}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
