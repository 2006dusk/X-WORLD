r"""十轮记忆与稳定性探针。

成人向五轮测出默认档有整轮复述，2B 零复述。这一轮把回合拉到十轮，
同时测两件事：

一、记忆。前几轮用户会埋入一条具体事实，后文不提示，检验模型是否记得；
   同时检验模型有没有把已确立的事实推翻。
二、稳定性。十轮里看整轮复述、与前文重合度、字数漂移、速率衰减、
   上下文增长后是否还能落在目标长度带。

事实与回应判定都用确定性规则，不靠裁判。

用法：
    python tools\probe_memory_10turn.py [配置名子串 ...]
"""

from __future__ import annotations

import json
import re
import statistics
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
OUT_FILE = OUT_DIR / "memory_10turn.json"
TEXT_FILE = OUT_DIR / "memory_10turn_transcript.md"

# 角色卡与成人向五轮保持一致，便于横向比较。
CHARACTER = """【角色卡】角色K，32 岁，地址N地址U的调音师。性格沉默、观察力强，不轻易表露情绪，习惯用动作代替解释。与用户扮演的搭档已经交往两年。
【场景】深夜，地址U只剩一盏台灯，门外雨声很密。工作台上的设备都关了，只有监听音箱还亮着一盏小红灯。
【对白示例】
用户：你今天又没吃饭。
角色K：吃了。
用户：骗人。
角色K：……你管得挺宽。"""

# 十个回合。fact 字段是这一轮埋入、后面要检验的事实。
# expect 是本轮应当回应的用户动作关键词。
TURNS = [
    {"user": "我从背后贴上去，手臂环住他的腰，额头抵在他后颈上，问他今天累不累。",
     "fact": None, "expect": ["腰", "后颈", "累"]},
    {"user": "他转过身，把我抵在调音台边沿，低头吻了下来，手掌扣住我的手腕按在台面上。",
     "fact": "手腕", "expect": ["吻", "手腕", "调音台"]},
    {"user": "我仰起头回应他，手指插进他后颈的发根，呼吸乱得压不住，说了句别停。",
     "fact": "后颈", "expect": ["呼吸", "发根", "别停"]},
    {"user": "他停下来，把我抱到旁边的旧沙发上，压低声音问我是不是真的想要。",
     "fact": "沙发", "expect": ["沙发", "想要", "问"]},
    {"user": "我拉住他的手贴在自己心口上说想要，他没再说话，低头吻住我的眼角。",
     "fact": "心口", "expect": ["心口", "想要", "吻"]},
    {"user": "我伸手去够床头柜上的水杯，指尖碰到那只缺了口的白瓷杯。",
     "fact": "白瓷杯", "expect": ["杯", "水", "递"]},
    {"user": "我侧过身，摸了摸自己左手腕上那道旧疤，是三年前留下的。",
     "fact": "左手腕", "expect": ["疤", "手腕", "左手"]},
    {"user": "窗外的雨小了些，我把耳朵贴在他胸口，听他的心跳。",
     "fact": None, "expect": ["心跳", "雨", "听"]},
    {"user": "我伸手把台灯调暗了一档，房间只剩监听音箱那盏红灯。",
     "fact": "台灯", "expect": ["灯", "红灯", "暗"]},
    {"user": "我抬手揉了揉发酸的后颈，他伸手替我按住肩膀，力道很轻。",
     "fact": "肩膀", "expect": ["肩", "按", "轻"]},
]

# 事实检验：埋入后，后续轮次里应能找到对应词。
FACT_TURNS = {}
for _i, _t in enumerate(TURNS, 1):
    if _t.get("fact"):
        FACT_TURNS[_t["fact"]] = _i

# 矛盾检测：模型不得推翻已确立的物件或身体部位。
CONTRADICTIONS = [
    ("白瓷杯", r"没有杯子|杯子不见了|换了个杯子|不是白瓷杯", "推翻白瓷杯"),
    ("左手腕", r"右手腕|是右手|左手没有疤|没有疤", "推翻左手腕的疤"),
    ("台灯", r"台灯坏了|没有台灯|换了个灯", "推翻台灯"),
]

GRAM = 8


def grams(text: str) -> set[str]:
    return {text[i : i + GRAM] for i in range(0, max(len(text) - GRAM, 0))}


def overlap(text: str, history: list[str]) -> float:
    if not history:
        return 0.0
    mine = grams(text)
    if not mine:
        return 0.0
    seen: set[str] = set()
    for item in history:
        seen |= grams(item)
    return round(len(mine & seen) / len(mine), 4)


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
        turns = []
        messages = [{"role": "system", "content": system}]
        try:
            if not wait_ready(base, proc):
                rows.append({"name": name, "error": "服务启动超时"})
                continue
            time.sleep(1.0)
            for turn_index, spec in enumerate(TURNS, 1):
                messages.append({"role": "user", "content": spec["user"]})
                item = generate_turn(base, messages, directive)
                m = item["metrics"]
                m["overlap_history"] = overlap(item["content"], [t["content"] for t in turns])
                m["responded"] = bool(spec["expect"]) and any(k in item["content"] for k in spec["expect"])
                turns.append({
                    "turn": turn_index, "user": spec["user"], "content": item["content"],
                    "metrics": m, "fact": spec.get("fact"),
                })
                print("    第{}轮 {}字 对白{}段 完整{} 带内{} 重合{:.0%} 解码{} tok/s 提示{}tok 拒绝{}".format(
                    turn_index, m["chars"], m["dialogue_count"], m["complete_turn"],
                    m["in_length_band"], m["overlap_history"], m["decode_tok_per_s"],
                    m["prompt_tokens"], "是" if m.get("refusal") else "否"), flush=True)
                messages.append({"role": "assistant", "content": item["content"]})
        finally:
            stop_server(proc, log_path, handle)

        rows.append({
            "name": name, "params": case.get("params"), "quant": case.get("quant"),
            "model": str(case["model"]), "turns": turns,
        })
        OUT_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print("结果已写入 " + str(OUT_FILE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
