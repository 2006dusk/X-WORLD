import pathlib, json
import os
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

root = pathlib.Path(REPO)

def count(folder, exts):
    files = [p for p in folder.rglob("*") if p.suffix in exts and "__pycache__" not in p.parts and "node_modules" not in p.parts]
    lines = 0
    for p in files:
        try:
            lines += len(p.read_text(encoding="utf-8").splitlines())
        except Exception:
            pass
    return len(files), lines

app = count(root / "backend" / "app", {".py"})
tests = count(root / "backend" / "tests", {".py"})
tools = count(root / "backend" / "tools", {".py"})
front = count(root / "frontend" / "src", {".vue", ".ts"})

total_py = app[0] + tests[0] + tools[0]
total_lines = app[1] + tests[1] + tools[1]
print(json.dumps({
    "app": app, "tests": tests, "tools": tools, "front": front,
    "py_total": total_py, "py_lines": total_lines,
}, ensure_ascii=False))
