import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {'dist', 'build', '__pycache__', '.venv'}


def count(sub):
    base = ROOT / sub
    files = [p for p in base.rglob('*.py') if not any(part in SKIP for part in p.parts)]
    lines = 0
    for p in files:
        lines += len(p.read_text(encoding='utf-8', errors='replace').splitlines())
    return len(files), lines


for sub in ('app', 'tests', 'tools'):
    n, ln = count(sub)
    print(f'{sub}: files={n} lines={ln}')
