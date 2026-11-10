import json, pathlib
import os
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
s = pathlib.Path(os.path.join(REPO, "..", 'docs', '实践情况', 'data', 'sweep'))
cur = json.loads((s / "multi_turn.json").read_text(encoding="utf-8"))
old = json.loads((s / "multi_turn_run_a.json").read_text(encoding="utf-8"))
llama2 = [r for r in cur if r.get("name") == "aura_v2_7b_iq3_m_llama2"]
base = [r for r in old if r.get("name") == "aura_v2_7b_iq3_m"]
out = []
if llama2 and base:
    a = llama2[0]["turns"]
    b = base[0]["turns"]
    same = len(a) == len(b) and all(x["content"] == y["content"] for x, y in zip(a, b))
    out.append("逐字相同: %s" % same)
    out.append("轮数: %d vs %d" % (len(a), len(b)))
    for x, y in zip(a, b):
        out.append("T%s same=%s len=%d/%d" % (x["turn"], x["content"] == y["content"], len(x["content"]), len(y["content"])))
pathlib.Path(s / "aura_template_ab.txt").write_text("\n".join(out), encoding="utf-8")
print("written")
