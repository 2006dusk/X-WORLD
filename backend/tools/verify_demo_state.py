r"""核对演示数据目录的最终状态：五个角色、五条对话、两个世界书。"""
import json
import os
import urllib.request
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

ROOT = r"DATA_ROOT"
BASE = "http://127.0.0.1:8760"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


lines = []
cards = get("/v1/cards/")["items"]
lines.append("cards: %d" % len(cards))
for item in cards:
    lines.append("  %s %s region=%s" % (item["id"], item["name"], item.get("region_id")))

sessions = get("/v1/sessions/")["items"]
lines.append("sessions: %d" % len(sessions))
for item in sessions:
    lines.append(
        "  %s char=%s region=%s books=%s count=%s"
        % (item["id"], item["character_id"], item.get("region_id"), item.get("world_book_ids"), item.get("message_count"))
    )

books = get("/v1/world-books/")["items"]
lines.append("world books: %d -> %s" % (len(books), [b["name"] for b in books]))
for book in books:
    full = get("/v1/world-books/" + book["id"])
    regions = [e for e in full.get("entries", []) if e.get("kind") == "region"]
    lines.append("  %s regions=%s" % (book["name"], [r.get("name") for r in regions]))

files = []
for sub in ("cards", "state", "world_books", "sessions"):
    base = os.path.join(ROOT, sub)
    for dirpath, _dirnames, filenames in os.walk(base):
        for fn in filenames:
            files.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
lines.append("data files: %d" % len(files))
for item in sorted(files):
    lines.append("  " + item)

probe_path = os.path.join(
    REPO,
    "..", "docs", "实践情况", "data", "sweep", "five_characters_probe.json",
)
report = json.loads(open(probe_path, encoding="utf-8").read())
lines.append("probe summary: " + json.dumps(report["summary"], ensure_ascii=False))
lines.append("probe observation: " + json.dumps(report["observation"], ensure_ascii=False))

print("\n".join(lines))
