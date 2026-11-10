"""Resolve machine-local paths for the analysis probes.

Order: environment variable, then local.paths.json next to the package,
then an error naming the key to set. The repository carries no machine path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent
_LOCAL_JSON = _PKG / "local.paths.json"


def _from_file(key: str) -> str:
    if not _LOCAL_JSON.exists():
        return ""
    try:
        data = json.loads(_LOCAL_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def resolve(key: str, env_var: str) -> str:
    value = os.environ.get(env_var, "").strip() or _from_file(key)
    if not value:
        raise SystemExit(
            "%s is not set: export %s, or put %r in %s"
            % (key, env_var, key, _LOCAL_JSON)
        )
    return value


def model_path() -> str:
    return resolve("model", "XWORLD_MODEL")
