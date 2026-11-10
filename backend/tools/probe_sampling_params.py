"""采样旋钮生效性探针：查本机推理栈到底认哪些采样参数。

本机推理栈（自写服务端 world-work-cpp）认哪些采样旋钮，以它的实现为准：
请求字段要么进采样链，要么被静默忽略。本探针用**固定种子**做判定：

固定种子下，同样的请求必然生成同样的文本。于是「加一个参数后文本变了」
就说明这个参数真的进了采样链；文本没变就说明它被忽略了。

跑法：
  python backend/tools/probe_sampling_params.py

只打印到终端，不落持久化数据。
"""
import json, urllib.request
URL = "http://127.0.0.1:8080/v1/chat/completions"
def ask(extra, tag):
    body = {"messages": [{"role": "user", "content": "用两句话写：她把茶盏搁在榻沿，指尖敲了敲床柱。"}],
            "max_tokens": 80, "temperature": 1.0, "top_p": 0.95, "seed": 424242}
    body.update(extra)
    req = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    t = data["choices"][0]["message"]["content"]
    print(tag, json.dumps(t, ensure_ascii=True))
    return t
base = ask({}, "base    ")
minp = ask({"min_p": 0.2}, "min_p.2 ")
typ  = ask({"typical_p": 0.5}, "typical ")
miro = ask({"mirostat_mode": 2, "mirostat_tau": 5.0}, "mirostat")
topk = ask({"top_k": 40}, "top_k40 ")
print("SUMMARY", json.dumps({
  "base_deterministic": base == ask({}, "base2   "),
  "min_p_effective": base != minp,
  "typical_p_effective": base != typ,
  "mirostat_effective": base != miro,
  "top_k_effective": base != topk,
}, ensure_ascii=True))
