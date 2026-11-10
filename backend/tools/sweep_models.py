r"""模型与量化的速度加质量扫描。

对每个配置做三件事：
1. 起自写推理服务端（world-work-cpp），测首字延迟、预填充与解码速率；
2. 发一条要求「完整回合」的 RP 提示词，取回整段中文生成；
3. 用确定性指标记录长度、结构、角色一致与重复退化。

质量分另由裁判模型给出，见 docs/实践情况/质量与速度平衡点.md。

用法：
    python backend\tools\sweep_models.py [配置名子串 ...]
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(BACKEND_ROOT))

from bench_local import CASES, HOST, LONG_PROMPT, SHORT_PROMPT, free_port, wait_gpu_free  # noqa: E402
from bench_local_final import measure, measure_ttft  # noqa: E402
from wwc_server import start_server, stop_server, wait_ready  # noqa: E402
from local_paths import local_path, model_file  # noqa: E402


OUT_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
MODELS_DIR = Path(local_path("models_hf_dir"))

# 两档提示词。P1 是默认 RP 行为：模型自己决定写多长。
# P2 显式要求长回合，用来分辨「写不出长文」与「默认写短」。
RP_SYSTEM = (
    "你扮演角色K，在地址N经营地址U的调音师，沉默、观察力强。"
    "回复必须保持角色一致，不解释设定，不使用旁白总结，不提及自己是 AI。"
    "用中文写作。"
)
RP_SCENE = (
    "【场景】深夜，地址U只剩一盏台灯，雨点敲在铁皮屋顶上。\n"
    "【细节】控制台还亮着，桌角压着一张手写便签，上面是一个陌生名字。\n"
    "【你的行动】我推开门，手里攥着一盘无人认领的磁带，走到控制台前，把它放在调音台上。"
)
PROMPTS = {
    "p1": (
        RP_SYSTEM
        + "\n本次回复是一个完整回合：先写场景与环境的短描写，再写人物的动作，"
        "然后写一到三句对白，对白用中文引号，最后收在一个明确的动作或台词上。"
        "不要写提纲，不要重复上一段。",
        RP_SCENE,
    ),
    "p2": (
        RP_SYSTEM,
        RP_SCENE
        + "\n请把这个场景写成一个完整长回合：至少 500 字，分成三段以上，"
        "每段都要有环境或动作描写，并包含至少三句中文引号对白，写到场景自然推进再收尾。",
    ),
    # P3 按客户端实际目标长度写：150 到 300 字。
    "p3": (
        RP_SYSTEM,
        RP_SCENE
        + "\n请写一个完整回合，包含场景描写、人物动作和对白，对白用中文引号，"
        "全文控制在 150 到 300 字之间，写到自然收尾为止。",
    ),
}
QUALITY_KEYWORDS = ("角色K", "地址U", "磁带", "雨", "台灯", "调音台")
MAX_TOKENS = 900

# 模型目录里的既有在役权重不在 hf 子目录，单列出来。
SWEEP_CASES: list[dict[str, object]] = [
    {
        "name": "lumi_q4_km",
        "model": model_file("ms/lumi_q4.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "6.9B",
        "quant": "Q4_K_M",
        "note": "在役交互档",
    },
    {
        "name": "qwen2.5-14b-roleplay-zh_q4_km",
        "model": model_file("hf/qwen2.5-14b-roleplay-zh-q4_k_m.gguf"),
        "args": ["--ctx-size", "8192", "--n-gpu-layers", "36", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        "params": "14B",
        "quant": "Q4_K_M",
        "note": "在役长上下文档",
    },
    {
        "name": "arityflow-4b_q4_km",
        "model": MODELS_DIR / "ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "4B",
        "quant": "Q4_K_M",
        "note": "候选交互档，自带对话模板",
    },
    {
        "name": "arityflow-4b_q5_km",
        "model": MODELS_DIR / "ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "4B",
        "quant": "Q5_K_M",
        "note": "候选交互档，自带对话模板",
    },
    {
        "name": "arityflow-4b_q8_0",
        "model": MODELS_DIR / "ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q8_0.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "4B",
        "quant": "Q8_0",
        "note": "候选交互档，自带对话模板",
    },
    {
        "name": "tifa-7b_q4_km",
        "model": MODELS_DIR / "tifa-7b-qwen2-v0.1.q4_k_m.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "7B",
        "quant": "Q4_K_M",
        "note": "候选均衡档，自带对话模板",
    },
    {
        "name": "tifa-7b_q6_k",
        "model": MODELS_DIR / "tifa-7b-qwen2-v0.1.q6_k.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "7B",
        "quant": "Q6_K",
        "note": "候选均衡档，自带对话模板",
    },
    {
        "name": "peach-9b_q4_km",
        "model": MODELS_DIR / "Peach-9B-8k-Roleplay-Q4_K_M.gguf",
        "args": ["--ctx-size", "8192", "--n-gpu-layers", "999"],
        "params": "9B",
        "quant": "Q4_K_M",
        "note": "候选均衡档，自带对话模板",
    },
    {
        "name": "nqlsg-14b_q4_km",
        "model": MODELS_DIR / "NQLSG-Qwen2.5-14B-MegaFusion-v5-roleplay.i1-Q4_K_M.gguf",
        "args": ["--ctx-size", "8192", "--n-gpu-layers", "36", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        "params": "14B",
        "quant": "Q4_K_M",
        "note": "候选长上下文档，自带对话模板",
    },
    {
        "name": "xiyouji-6b_q5_km",
        "model": MODELS_DIR / "XiYouJi-6b.Q5_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "6B",
        "quant": "Q5_K_M",
        "note": "中文角色扮演，六档候选，自带对话模板",
    },
    {
        "name": "sanguoyanyi-6b_q4_km",
        "model": MODELS_DIR / "SanGuoYanYi-6b.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "6B",
        "quant": "Q4_K_M",
        "note": "中文角色扮演，六档候选第二个样本，自带对话模板",
    },
    {
        "name": "nymphaea-4b_q5_km",
        "model": MODELS_DIR / "Qwen3-4B-Nymphaea-RP.Q5_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "4B",
        "quant": "Q5_K_M",
        "note": "四档角色扮演，与默认档同体积对照",
    },
    {
        "name": "collective-8b_q4_km",
        "model": MODELS_DIR / "collective-v0.1-chinese-roleplay-8b.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "8B",
        "quant": "Q4_K_M",
        "note": "八档中文命名角色扮演，自带对话模板",
    },
    {
        "name": "rpg-roleplay-v2-4b_q5_km",
        "model": MODELS_DIR / "Qwen3-4B-RPG-Roleplay-V2.Q5_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "4B",
        "quant": "Q5_K_M",
        "note": "四档 RPG 角色扮演，与默认档同体积对照",
    },
    {
        "name": "darkidol-8b_q4_km",
        "model": MODELS_DIR / "DarkIdol-Llama-3.1-8B-Instruct-1.2-Uncensored.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "8B",
        "quant": "Q4_K_M",
        "note": "八档 Llama3.1 角色扮演，多语种标签含中文",
    },
    {
        "name": "l3-stheno-8b_q4_km",
        "model": MODELS_DIR / "L3-8B-Stheno-v3.2.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "8B",
        "quant": "Q4_K_M",
        "note": "八档 L3 Stheno 系列，社区点赞最高",
    },
    {
        "name": "rolemo-v3-7b_q6_k",
        "model": MODELS_DIR / "role-mo-v3-7b-q6_k.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "7B",
        "quant": "Q6_K",
        "note": "七档角色扮演，ModelScope 原生仓库 role-mo 系列",
    },
    {
        "name": "bdrp-7b_q4_km",
        "model": MODELS_DIR / "Qwen2-7B-BD-RP.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "7B",
        "quant": "Q4_K_M",
        "note": "七档中文角色扮演，ModelScope 托管，BeyondDialogue 数据集",
    },
    {
        "name": "peach2-9b_q4_km",
        "model": MODELS_DIR / "Peach-2.0-9B-8k-Roleplay.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "9B",
        "quant": "Q4_K_M",
        "note": "九档中文角色扮演，Peach 二代，ModelScope 托管",
    },
    {
        "name": "qwen3_5-2b_ud",
        "model": model_file("local/qwen3.5-2b/Qwen3.5-2B-UD-Q4_K_XL.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "2B",
        "quant": "Q4_K_XL",
        "note": "同族同量化档规模对照，StarHub 现有权重",
    },
    {
        "name": "qwen3_5-4b_ud",
        "model": model_file("local/qwen3.5-4b/Qwen3.5-4B-UD-Q4_K_XL.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "4B",
        "quant": "Q4_K_XL",
        "note": "同族同量化档规模对照，StarHub 现有权重，兼本地裁判模型",
    },
    {
        "name": "qwen3_5-9b_ud",
        "model": model_file("local/qwen3.5-9b/Qwen3.5-9B-UD-Q4_K_XL.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "9B",
        "quant": "Q4_K_XL",
        "note": "同族同量化档规模对照，StarHub 现有权重",
    },
    {
        "name": "qwen2-7b-multilingual-rp_q5_km",
        "model": MODELS_DIR / "Qwen2-7B-Multilingual-RP.Q5_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "7B",
        "quant": "Q5_K_M",
        "note": "多语言角色扮演，自带对话模板",
    },
    {
        "name": "qwen3_5-9b_q6_k",
        "model": model_file("local/qwen3.5-9b/Qwen3.5-9B-Q6_K.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        "params": "9B",
        "quant": "Q6_K",
        "note": "名单候选：九档高精量化，8GB 显存下用 q8 KV 缓存",
    },
    {
        "name": "qwen3_5-0.8b_ud",
        "model": model_file("local/qwen3.5-0.8b/Qwen3.5-0.8B-UD-Q4_K_XL.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "0.8B",
        "quant": "Q4_K_XL",
        "note": "名单候选：极小档，留显存给上下文的下限参照",
    },
    {
        "name": "qwen2-7b-multilingual-rp_q6_k",
        "model": MODELS_DIR / "Qwen2-7B-Multilingual-RP.Q6_K.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "7B",
        "quant": "Q6_K",
        "note": "名单候选：多语言角色扮演高精量化，全显存驻留",
    },
    {
        "name": "aura_v2_7b_iq3_m",
        "model": MODELS_DIR / "Aura_v2_7B-IQ3_M-imat.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "7B",
        "quant": "IQ3_M",
        "note": "名单候选：Uncensored 标签，低比特 imatrix 量化",
    },
    {
        "name": "arli-rpmax-12b_q3_km",
        "model": MODELS_DIR / "ArliAI-RPMax-12B-v1.1-Q3_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        "params": "12B",
        "quant": "Q3_K_M",
        "note": "名单候选：Mistral Nemo 12B 低比特档，官方仓库没有 IQ4_XS",
    },
    {
        "name": "gemma4-e4b-minifantasy_q4_km",
        "model": MODELS_DIR / "Gemma4-E4B-MiniFantasy-V1.Q4_K_M.gguf",
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "params": "E4B",
        "quant": "Q4_K_M",
        "note": "名单候选：Gemma4 混合专家，小显存向设计",
    },
]

# 自写服务端固定用权重自带的对话模板，没有第二个模板变体可比：
# 原先的「强制 chatml」对照档因此撤掉。

DIALOGUE_RE = re.compile(r"[“「『]([^”」』]*)")
ASCII_DIALOGUE_RE = re.compile(r'"([^"\n]{2,200})"')
SENTENCE_SPLIT = re.compile(r"[。！？!?…\n]+")


def heuristic(text: str, finish_reason: str | None, usage: dict | None) -> dict[str, object]:
    """确定性质量指标。"""
    chars = len(text)
    dialogues = DIALOGUE_RE.findall(text)
    ascii_dialogues = ASCII_DIALOGUE_RE.findall(text)
    dialogue_chars = sum(len(d) for d in dialogues) + sum(len(d) for d in ascii_dialogues)
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]
    narration_sentences = [s for s in sentences if not DIALOGUE_RE.fullmatch(s.strip())]
    grams = [text[i : i + 8] for i in range(0, max(len(text) - 8, 0))]
    repeat_rate = (1 - len(set(grams)) / len(grams)) if grams else 0.0
    garbled = sum(1 for ch in text if ch.isprintable() is False)
    hits = [k for k in QUALITY_KEYWORDS if k in text]
    ends_clean = bool(text) and text.rstrip()[-1:] in "。！？!?…”』」"
    paragraphs = [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    return {
        "chars": chars,
        "finish_reason": finish_reason,
        "truncated": finish_reason == "length",
        "in_length_band": 150 <= chars <= 300,
        "over_length": chars > 450,
        "dialogue_count": len(dialogues) + len(ascii_dialogues),
        "cjk_dialogue_count": len(dialogues),
        "ascii_dialogue_count": len(ascii_dialogues),
        "first_person_count": text.count("我"),
        "dialogue_ratio": round(dialogue_chars / chars, 3) if chars else 0.0,
        "narration_sentences": len(narration_sentences),
        "paragraphs": len(paragraphs),
        "ends_clean": ends_clean,
        "complete_turn": bool(
            len(dialogues) >= 1
            and len(narration_sentences) >= 1
            and ends_clean
            and finish_reason != "length"
        ),
        "keyword_hits": len(hits),
        "keyword_missed": [k for k in QUALITY_KEYWORDS if k not in hits],
        "repeat_rate": round(repeat_rate, 4),
        "garbled_chars": garbled,
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens"),
    }


def sample_turn(base: str, label: str) -> dict[str, object]:
    """按指定提示档取一回合生成。"""
    system, user = PROMPTS[label]
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.8,
        "seed": 42,
    }
    started = time.perf_counter()
    resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=1800.0)
    resp.raise_for_status()
    elapsed = time.perf_counter() - started
    body = resp.json()
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = str(message.get("content") or "")
    usage = body.get("usage") or {}
    metrics = heuristic(text, choice.get("finish_reason"), usage)
    completion = int(usage.get("completion_tokens") or 0)
    metrics["wall_s"] = round(elapsed, 2)
    metrics["gen_tok_per_s"] = round(completion / elapsed, 2) if elapsed > 0 else 0.0
    return {"content": text, "metrics": metrics}


def run_case(case: dict[str, object]) -> dict[str, object]:
    model_path = Path(str(case["model"]))
    record: dict[str, object] = {
        "name": case["name"],
        "params": case["params"],
        "quant": case["quant"],
        "note": case["note"],
        "args": " ".join(str(a) for a in case["args"]),
        "model_file": model_path.name,
        "size_mb": round(model_path.stat().st_size / 1048576) if model_path.exists() else None,
    }
    if not model_path.exists():
        record["error"] = "权重文件不存在"
        return record

    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(str(model_path), list(case["args"]), port)
    try:
        if not wait_ready(base, proc):
            record["error"] = "服务启动超时"
            return record
        time.sleep(1.0)
        record["ttft"] = measure_ttft(base)
        record["short"] = measure(base, SHORT_PROMPT)
        record["long"] = measure(base, LONG_PROMPT)
        record["sample_p1"] = sample_turn(base, "p1")
        record["sample_p2"] = sample_turn(base, "p2")
        record["sample_p3"] = sample_turn(base, "p3")
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop_server(proc, log_path, handle)
        wait_gpu_free()
    return record


def main() -> int:
    wanted = [a.lower() for a in sys.argv[1:]]
    cases = [
        case
        for case in SWEEP_CASES
        if not wanted
        or any(token in str(case["name"]).lower() for token in wanted)
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "sweep.json"
    results: list[dict[str, object]] = []
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                results = loaded
        except json.JSONDecodeError:
            results = []

    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['name']}", flush=True)
        item = run_case(case)
        results = [r for r in results if r.get("name") != item.get("name")]
        results.append(item)
        target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        if "error" in item:
            print(f"    失败：{item['error']}", flush=True)
        else:
            m1 = item["sample_p1"]["metrics"]
            m2 = item["sample_p2"]["metrics"]
            m3 = item["sample_p3"]["metrics"]
            print(
                f"    解码 {item['short']['decode_tok_per_s']} tok/s, "
                f"预填充 {item['long']['prefill_tok_per_s']} tok/s, "
                f"P1 {m1['chars']} 字/完整 {m1['complete_turn']}, "
                f"P2 {m2['chars']} 字/完整 {m2['complete_turn']}, "
                f"P3 {m3['chars']} 字/命中目标 {m3['in_length_band']}/完整 {m3['complete_turn']}",
                flush=True,
            )
    print(f"\n结果已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
