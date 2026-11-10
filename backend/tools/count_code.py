import pathlib
import os
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
root = pathlib.Path(os.path.join(REPO, 'backend'))
for sub in ("app", "tests", "tools"):
    d = root / sub
    files = sorted(p for p in d.rglob("*.py"))
    lines = 0
    for p in files:
        try:
            lines += len(p.read_text(encoding="utf-8").splitlines())
        except Exception:
            pass
    print(sub, len(files), lines)
