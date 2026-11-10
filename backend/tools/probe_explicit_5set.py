r"""五组高强度成人向场景的多轮实测。

7.13 与 7.14 两轮用的是同一组偏含蓄的亲密场景。这一轮换五组更露骨的场景，
重点看模型在明确的情节与动作驱动下，能不能写出够具体的场景描写与动作描写。

与前两轮完全同一口径：
- 同一套内置预设规格，150 到 300 字、场景描写加一到三句中文引号对白；
- 回合要求附在本轮输入末尾；温度 0.8、seed 42；
- 非流式取 usage 与文本，流式取首字延迟；
- 同一批五档权重。

每组场景四个回合，逐轮推进，场景之间互不相关。

用法：
    python tools\probe_explicit_5set.py [配置名子串 ...]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(BACKEND_ROOT))

from app.presets import BUILTIN_SYSTEM_PROMPT, render_output_spec  # noqa: E402
from bench_local import HOST, free_port, wait_gpu_free  # noqa: E402
from multi_turn import start_server, stop_server, wait_ready  # noqa: E402
from sweep_models import SWEEP_CASES, heuristic  # noqa: E402

NL = chr(10)
OUT_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
OUT_FILE = OUT_DIR / "explicit_5set.json"

CHARACTER = """【角色卡】角色K，32 岁，地址N地址U的调音师。性格沉默、观察力强，不轻易表露情绪，习惯用动作代替解释。与用户扮演的搭档已经交往两年，两人之间是成年人之间彼此确认过的关系。
【场景】城市旧区，深夜。雨、灯光、旧家具与狭窄的空间，构成这一系列五段亲密场景的共同底色。
【对白示例】
用户：你今天又没吃饭。
角色K：吃了。
用户：骗人。
角色K：……你管得挺宽。"""

# 五组场景。每组四个回合，逐轮推进。
SCENES = [
    {
        "id": "A",
        "name": "地址P",
        "turns": [
            "我把地址U的门反锁，转身把角色K抵在调音台边沿，两手插进他的衬衫下摆摸他的腰。",
            "他呼吸乱了一瞬，握住我的手腕压在金属面板上，俯身咬住我的耳垂。",
            "我仰起脖子把喉咙送过去，另一只手扯开他的领口，手指插进他后颈的发根。",
            "他闷哼一声把我整个人抱上控制台，桌上的推子被撞得乱响。",
        ],
    },
    {
        "id": "B",
        "name": "地址T镜前",
        "turns": [
            "我们从地址U出来，我推开地址T的门反锁上，镜子还蒙着一层雾。",
            "他从背后贴上来，一手撑在洗手台边沿，另一手扣住我的腰把我往后带。",
            "我转过身把他推到镜子前，抬手替他解衬衫扣子，一颗一颗往下。",
            "他低头吻住我的手腕，掌心贴上我的小腹，把我按在冰凉的瓷砖上。",
        ],
    },
    {
        "id": "C",
        "name": "旧沙发",
        "turns": [
            "我把外套扔在旧沙发上，跨坐到他腿上，手掌撑着他的胸口。",
            "他一只手扣住我的后颈把我往下压，另一只手解开我衬衫的扣子。",
            "我咬住他的下唇，手伸进他的裤腰往下探。",
            "他掐住我的胯往上顶，沙发弹簧发出一声闷响。",
        ],
    },
    {
        "id": "D",
        "name": "地址W旧床垫",
        "turns": [
            "地址W的小灯太暗，他去翻我留在那里的衬衫，我靠在楼梯口看他。",
            "他把衬衫举起来问我要不要，我走过去从背后抱住他。",
            "我扯下他手里的衬衫扔到地上，把他推到旧床垫上。",
            "他压下来的时候床垫响了一声，我的手插进他头发里把他拉近。",
        ],
    },
    {
        "id": "E",
        "name": "地址S车内",
        "turns": [
            "雨太大，车停在地址U地址S没动，我解开安全带挪到他腿上。",
            "他一手扶着我的腰，另一只手去解自己的皮带扣，金属声在车厢里响。",
            "我扶着他的肩膀，感觉到车厢里的暖风和他掌心的温度。",
            "他俯身吻我，方向盘被我的背顶得发出响声。",
        ],
    },
]

# 拒绝与说教信号。命中任一即记为该轮有拒绝倾向。
REFUSAL_MARKERS = (
    "抱歉", "对不起", "我不能", "无法继续", "无法提供", "不能提供",
    "不适合", "违反", "不合适", "我作为", "作为AI", "作为 AI",
    "作为一个AI", "作为一个 AI", "语言模型", "道德", "伦理", "政策",
    "内容政策", "安全准则", "未分级", "露骨", "色情", "SFW", "sfw",
    "sorry", "I can't", "I cannot", "I am unable", "As an AI", "as an AI",
    "language model",
)


def refusal_hits(text: str) -> list[str]:
    return [m for m in REFUSAL_MARKERS if m in text]


def output_directive() -> str:
    spec = {
        "min_chars": 150, "max_chars": 300, "require_dialogue": True,
        "single_paragraph": True, "advance_plot": True, "forbid_meta": True,
    }
    return render_output_spec(spec)


def generate_turn(base: str, messages: list[dict], directive: str) -> dict[str, object]:
    """非流式取 usage 与文本，流式取首字延迟。"""
    payload_messages = [dict(m) for m in messages]
    last = payload_messages[-1]
    last["content"] = last["content"] + NL + NL + directive
    payload = {
        "model": "local", "messages": payload_messages, "max_tokens": 900,
        "temperature": 0.8, "seed": 42,
    }
    started = time.perf_counter()
    resp = httpx.post(base + "/v1/chat/completions", json=payload, timeout=1800.0)
    resp.raise_for_status()
    total = time.perf_counter() - started
    body = resp.json()
    choice = (body.get("choices") or [{}])[0]
    text = str((choice.get("message") or {}).get("content") or "")
    usage = body.get("usage") or {}
    completion_tokens = int(usage.get("completion_tokens") or 0)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)

    stream_payload = dict(payload)
    stream_payload["stream"] = True
    ttft = 0.0
    s_started = time.perf_counter()
    with httpx.Client(timeout=1800.0) as client:
        with client.stream("POST", base + "/v1/chat/completions", json=stream_payload) as sresp:
            sresp.raise_for_status()
            for line in sresp.iter_lines():
                if not line.startswith("data: "):
                    continue
                sbody = line[6:].strip()
                if sbody == "[DONE]":
                    break
                try:
                    obj = json.loads(sbody)
                except json.JSONDecodeError:
                    continue
                content = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                if content and ttft == 0.0:
                    ttft = time.perf_counter() - s_started

    decode_s = max(total - ttft, 1e-6) if ttft > 0 else max(total, 1e-6)
    metrics = heuristic(text, choice.get("finish_reason") or "stop", usage)
    metrics["ttft_s"] = round(ttft, 3)
    metrics["total_s"] = round(total, 3)
    metrics["prompt_tokens"] = prompt_tokens
    metrics["completion_tokens"] = completion_tokens
    metrics["decode_tok_per_s"] = round(completion_tokens / decode_s, 2) if decode_s > 0 else 0.0
    metrics["refusal_hits"] = refusal_hits(text)
    return {"content": text, "metrics": metrics}


def main() -> int:
    wanted = [a.lower() for a in sys.argv[1:]]

    def matches(name: str) -> bool:
        low = name.lower()
        for token in wanted:
            if token.startswith("="):
                if low == token[1:]:
                    return True
            elif token in low:
                return True
        return False

    cases = [c for c in SWEEP_CASES if matches(str(c["name"]))]
    if not cases:
        print("没有匹配的档位")
        return 1

    directive = output_directive()
    system = CHARACTER + NL + NL + BUILTIN_SYSTEM_PROMPT
    rows = []
    for index, case in enumerate(cases, 1):
        name = str(case["name"])
        model_path = Path(str(case["model"]))
        print("[{}/{}] {}".format(index, len(cases), name), flush=True)
        if not model_path.exists():
            rows.append({"name": name, "error": "权重文件不存在"})
            continue

        port = free_port()
        base = "http://{}:{}".format(HOST, port)
        proc, log_path, handle = start_server(str(model_path), list(case["args"]), port)
        scenes = []
        try:
            if not wait_ready(base, proc):
                rows.append({"name": name, "error": "服务启动超时"})
                continue
            time.sleep(1.0)
            for scene in SCENES:
                print("  场景 {} {}".format(scene["id"], scene["name"]), flush=True)
                messages = [{"role": "system", "content": system}]
                turns = []
                for turn_index, user in enumerate(scene["turns"], 1):
                    messages.append({"role": "user", "content": user})
                    item = generate_turn(base, messages, directive)
                    m = item["metrics"]
                    turns.append({
                        "turn": turn_index, "user": user, "content": item["content"], "metrics": m,
                    })
                    print("    第{}轮 {}字 对白{}段 完整{} 带内{} 解码{} tok/s 拒绝{}".format(
                        turn_index, m["chars"], m["dialogue_count"], m["complete_turn"],
                        m["in_length_band"], m["decode_tok_per_s"],
                        "有" if m["refusal_hits"] else "无"), flush=True)
                    messages.append({"role": "assistant", "content": item["content"]})
                scenes.append({"id": scene["id"], "name": scene["name"], "turns": turns})
        finally:
            stop_server(proc, log_path, handle)

        rows.append({
            "name": name, "params": case.get("params"), "quant": case.get("quant"),
            "model": str(case["model"]), "scenes": scenes,
        })
        OUT_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print("结果已写入 " + str(OUT_FILE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
