r"""本地 RP 输出模型速率基准。

对每个模型配置：拉起自写推理服务端（world-work-cpp），用真实 RP 提示词做流式生成，
记录首字延迟、生成速率与提示词评估速率，随后关闭服务释放显存。

用法：
    python backend\tools\bench_local.py
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# 推理端只有自写服务端一套，起停与命令行拼装都交给公共模块。
from wwc_server import start_server, stop_server  # noqa: E402
from local_paths import model_file  # noqa: E402


HOST = "127.0.0.1"

# 真实 RP 场景提示：系统指令 + 角色卡片段 + 世界书片段 + 用户输入
RP_SYSTEM = (
    "你正在扮演角色K，在地址N经营地址U的调音师，沉默、观察力强。"
    "回复必须保持角色一致，不解释设定，不使用旁白总结。"
    "中文回答，以动作与对白推进场景。"
)
RP_CONTEXT = (
    "【场景】深夜的地址U只剩一盏台灯，雨点敲在铁皮屋顶上。\n"
    "【世界设定】地址N的雨夜很安静，霓虹灯在水洼里碎成一片；"
    "角色K收藏着许多无人认领的磁带，只在深夜才一张张放来听。\n"
    "【对话示例】用户：这盘磁带还能修吗？\n角色K：先让我听听它还记得什么。"
)
RP_USER = (
    "我推开门，手里攥着一盘无人认领的磁带。磁带标签上写着一个陌生的名字。"
)

# 长上下文版：把设定重复到接近长会话规模
RP_CONTEXT_LONG = (
    RP_CONTEXT
    + "\n"
    + ("\n【补充设定】地址U的设备老旧，主控台常年带电；窗外是货运车道，"
       "偶尔有夜班卡车驶过，雨声因此忽近忽远。\n" * 40)
)

SHORT_PROMPT = {"system": RP_SYSTEM, "user": RP_CONTEXT + "\n" + RP_USER}
LONG_PROMPT = {"system": RP_SYSTEM, "user": RP_CONTEXT_LONG + "\n" + RP_USER}

CASES = [
    {
        "name": "lumi_q4",
        "model": model_file("ms/lumi_q4.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "全显存，f16 KV 缓存",
    },
    {
        "name": "qwen2.5-14b-roleplay-zh_q4_k_m",
        "model": model_file("hf/qwen2.5-14b-roleplay-zh-q4_k_m.gguf"),
        "args": [
            "--ctx-size", "4096",
            "--n-gpu-layers", "36",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
        ],
        "note": "36 层卸载，q8_0 KV 缓存，上下文 4096",
    },
    {
        "name": "qwen2.5-14b-roleplay-zh_q4_k_m",
        "model": model_file("hf/qwen2.5-14b-roleplay-zh-q4_k_m.gguf"),
        "args": [
            "--ctx-size", "8192",
            "--n-gpu-layers", "36",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
        ],
        "note": "36 层卸载，q8_0 KV 缓存，上下文 8192",
    },
    {
        "name": "qwen2.5-14b-roleplay-zh_q4_k_m",
        "model": model_file("hf/qwen2.5-14b-roleplay-zh-q4_k_m.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "0"],
        "note": "纯 CPU 回退，基准下限",
    },
    # 候选模型：不指定模板，自写服务端固定用权重自带的对话模板。
    {
        "name": "ArityFlow-Qwen3-4B-RolePlay",
        "model": model_file("hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "候选交互档，全显存，自带对话模板",
    },
    {
        "name": "Tifa-7B-Qwen2-v0.1",
        "model": model_file("hf/tifa-7b-qwen2-v0.1.q4_k_m.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "候选均衡档，全显存，自带对话模板",
    },
    {
        "name": "NQLSG-Qwen2.5-14B-MegaFusion-v5-roleplay",
        "model": model_file("hf/NQLSG-Qwen2.5-14B-MegaFusion-v5-roleplay.i1-Q4_K_M.gguf"),
        "args": ["--ctx-size", "8192", "--n-gpu-layers", "36", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        "note": "候选长上下文档，36 层卸载，自带对话模板",
    },
]

# 本轮待测名单里本机已有的档位。
BENCH_CASES = [
    {
        "name": "arityflow-4b_q5_km",
        "model": model_file("hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "当前默认档",
    },
    {
        "name": "qwen3_5-9b_ud",
        "model": model_file("local/qwen3.5-9b/Qwen3.5-9B-UD-Q4_K_XL.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "名单候选，九档 Q4",
    },
    {
        "name": "qwen3_5-9b_q6_k",
        "model": model_file("local/qwen3.5-9b/Qwen3.5-9B-Q6_K.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        "note": "名单候选，九档 Q6_K",
    },
    {
        "name": "qwen3_5-2b_ud",
        "model": model_file("local/qwen3.5-2b/Qwen3.5-2B-UD-Q4_K_XL.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "轻量档",
    },
    {
        "name": "qwen3_5-0.8b_ud",
        "model": model_file("local/qwen3.5-0.8b/Qwen3.5-0.8B-UD-Q4_K_XL.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "极小档下限参照",
    },
    {
        "name": "qwen2-7b-multilingual-rp_q6_k",
        "model": model_file("hf/Qwen2-7B-Multilingual-RP.Q6_K.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "名单候选：多语言角色扮演高精量化",
    },
    {
        "name": "aura_v2_7b_iq3_m",
        "model": model_file("hf/Aura_v2_7B-IQ3_M-imat.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "名单候选：Uncensored 低比特档",
    },
    {
        "name": "arli-rpmax-12b_q3_km",
        "model": model_file("hf/ArliAI-RPMax-12B-v1.1-Q3_K_M.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        "note": "名单候选：Mistral Nemo 12B 低比特档",
    },
    {
        "name": "gemma4-e4b-minifantasy_q4_km",
        "model": model_file("hf/Gemma4-E4B-MiniFantasy-V1.Q4_K_M.gguf"),
        "args": ["--ctx-size", "4096", "--n-gpu-layers", "999"],
        "note": "名单候选：Gemma4 混合专家小显存档",
    },
]

MAX_TOKENS = 300


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def wait_ready(base: str, proc: subprocess.Popen, timeout: float = 300.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(f"{base}/v1/models", timeout=2.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def measure(base: str, prompt: dict[str, str]) -> dict[str, object]:
    """流式生成一次，记录首字延迟与速率。"""
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.8,
        "stream": True,
    }
    started = time.perf_counter()
    ttft = 0.0
    chunks = 0
    text_chars = 0
    with httpx.Client(timeout=600.0) as client:
        with client.stream("POST", f"{base}/v1/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    break
                try:
                    obj = json.loads(body)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content") or ""
                if content:
                    if ttft == 0.0:
                        ttft = time.perf_counter() - started
                    chunks += 1
                    text_chars += len(content)
    total = time.perf_counter() - started
    return {
        "ttft": ttft,
        "total": total,
        "chunks": chunks,
        "chars": text_chars,
    }


def gpu_used_mib() -> int:
    """当前显存占用，单位 MiB。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return int(out.stdout.strip() or "0")
    except Exception:
        return -1


def wait_gpu_free(timeout: float = 240.0) -> None:
    """等待显存回落到基线。

    桌面与浏览器本身常驻约 600 至 700 MiB，因此以「回落到基线之上不多」为释放判据，
    而不是连续两次采样不再下降——后者会在显存尚未释放时立刻返回。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        used = gpu_used_mib()
        if used < 0 or used <= 1200:
            return
        time.sleep(2.0)


def run_case(case: dict[str, object]) -> dict[str, object]:
    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(str(case["model"]), list(case["args"]), port)
    result: dict[str, object] = {
        "name": case["name"],
        "note": case["note"],
        "args": " ".join(str(a) for a in case["args"]),
    }
    try:
        if not wait_ready(base, proc):
            result["error"] = "服务启动超时"
            return result
        time.sleep(1.0)
        short = measure(base, SHORT_PROMPT)
        long = measure(base, LONG_PROMPT)
        result["short"] = short
        result["long"] = long
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop_server(proc, log_path, handle)
        wait_gpu_free()
    return result


def main() -> int:
    print("== 本地 RP 输出模型速率基准 ==", flush=True)
    results = []
    for index, case in enumerate(CASES, 1):
        print(f"[{index}/{len(CASES)}] {case['name']} | {case['note']}", flush=True)
        item = run_case(case)
        results.append(item)
        if "error" in item:
            print(f"    失败：{item['error']}", flush=True)
        else:
            short = item["short"]
            long = item["long"]
            print(
                f"    短提示 TTFT {short['ttft']:.2f}s 总耗时 {short['total']:.2f}s "
                f"字符 {short['chars']}",
                flush=True,
            )
            print(
                f"    长提示 TTFT {long['ttft']:.2f}s 总耗时 {long['total']:.2f}s "
                f"字符 {long['chars']}",
                flush=True,
            )
    out = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "实践情况" / "data"
    out.mkdir(parents=True, exist_ok=True)
    target = out / "bench_local.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
