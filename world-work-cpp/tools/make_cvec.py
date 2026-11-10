"""Generate a control-vector test asset and verify the loader contract.

Layout (matches upstream llama_adapter_cvec::apply):
  segment k (0-based) -> model layer k+1
  total segments     -> n_layers - 1   (layer 0 has no control tensor)
  each segment       -> n_embd float32

"""

from __future__ import annotations

import argparse
import json
import math
import struct


def write_cvec(path: str, n_embd: int, n_layers: int, kind: str, scale: float) -> None:
    """Write a control vector file in the upstream layout."""
    n_segments = n_layers - 1
    values = []
    for k in range(n_segments):
        il = k + 1
        for d in range(n_embd):
            if kind == "zero":
                v = 0.0
            elif kind == "ones":
                v = 1.0
            elif kind == "layer_marker":
                # one-hot on the first dimension of every layer:
                # makes the injected layer identifiable from the logits.
                v = 1.0 if d == 0 else 0.0
            elif kind == "ramp":
                # smooth ramp along the embedding dimension, scaled by layer depth
                v = math.sin(d * 0.01) * (il / float(n_segments))
            else:
                raise SystemExit("unknown kind: " + kind)
            values.append(v * scale)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<%df" % len(values), *values))
    print(json.dumps({
        "path": path,
        "kind": kind,
        "scale": scale,
        "n_embd": n_embd,
        "n_layers": n_layers,
        "n_segments": n_segments,
        "floats": len(values),
        "bytes": len(values) * 4,
    }))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-embd", type=int, required=True)
    ap.add_argument("--n-layers", type=int, required=True)
    ap.add_argument("--kind", default="ramp",
                    choices=["zero", "ones", "layer_marker", "ramp"])
    ap.add_argument("--scale", type=float, default=1.0)
    args = ap.parse_args()
    write_cvec(args.out, args.n_embd, args.n_layers, args.kind, args.scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
