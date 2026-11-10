"""核对每块字数下限与实测输出长度，判断长度闸门是否过严。"""

from __future__ import annotations

import json
import os
import pathlib
import sys

# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
BACKEND = os.path.join(REPO, 'backend')
ROOT = REPO
sys.path.insert(0, BACKEND)

from app import presets  # noqa: E402

data = json.loads(
    pathlib.Path(ROOT, "..", "docs", "实践情况", "data", "sweep", "sse_sequence.json").read_text(
        encoding="utf-8"
    )
)
print("每块下限:", presets.structure_minimums(presets.BUILTIN_OUTPUT_SPEC, 150))
for index, row in enumerate(data):
    blocks = (row.get("final_blocks") or {}).get("blocks") or {}
    lengths = {key: len(value) for key, value in blocks.items()}
    body = sum(value for key, value in lengths.items() if key != "state")
    print(
        "T%d" % index,
        lengths,
        "body=%d" % body,
        "replace=%s" % row.get("replace_reasons"),
    )
