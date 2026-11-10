import json, httpx, time, pathlib
import os
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
repos = [
    "mradermacher/Qwen2-7B-Multilingual-RP-GGUF",
    "Lewdiculous/Aura_v2_7B-GGUF-IQ-Imatrix",
    "ArliAI/Mistral-Nemo-12B-ArliAI-RPMax-v1.1-GGUF",
    "mradermacher/Gemma4-E4B-MiniFantasy-V1-GGUF",
]
out = []
with httpx.Client(follow_redirects=True, headers={"User-Agent": "xworld/1.0"}) as c:
    for repo in repos:
        for attempt in range(4):
            try:
                r = c.get("https://hf-mirror.com/api/models/" + repo + "/tree/main", params={"recursive": "1"}, timeout=60.0)
                r.raise_for_status()
                data = r.json()
                gg = [(n.get("path"), int(n.get("size") or 0)) for n in data if str(n.get("path","")).lower().endswith(".gguf")]
                out.append({"repo": repo, "files": gg})
                break
            except Exception as exc:
                out.append({"repo": repo, "error": str(exc), "attempt": attempt})
                time.sleep(8 * (attempt + 1))
p = pathlib.Path(os.path.join(REPO, "..", 'docs', '实践情况', 'data', 'sweep', 'hf_trees.json'))
p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
for item in out:
    if "files" in item:
        print(item["repo"])
        for name, size in item["files"]:
            print("   %10d  %s" % (size, name))
    else:
        print(item["repo"], "ERR", item.get("error"))
