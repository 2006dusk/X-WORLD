"""本地裁判：用本机模型给各候选的 RP 回合打分。

裁判模型为 StarHub 的 Qwen3.5-4B-UD-Q4_K_XL，本地运行，不调用任何云端接口。
裁判模型自身那一档不自评，评分留空，由确定性指标与人工阅读补齐。

用法：
    python backend\tools\judge_local.py
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

from bench_local import HOST, free_port, wait_gpu_free  # noqa: E402
from wwc_server import start_server, stop_server, wait_ready  # noqa: E402

SWEEP_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
JUDGE_MODEL = r"D:\models\qwen3.5-4b\Qwen3.5-4B-UD-Q4_K_XL.gguf"
JUDGE_NAME = "Qwen3.5-4B-UD-Q4_K_XL"
JUDGE_ARGS = ["--ctx-size", "8192", "--n-gpu-layers", "999"]

RUBRIC = """你是中文角色扮演写作的评审。下面给你一段 AI 扮演「角色K」（地址N地址U调音师，沉默、观察力强）的回复，以及它的机械统计。

先按机械统计执行硬性扣分，再给分：
- 引号对白为 0 段时，format 记 2 分；
- 字数不足 100 时，narrative 与 format 都不超过 3 分；
- 字数超过 600 时，narrative 不超过 6 分；
- 回复只是复述用户给的动作或场景、没有新增内容时，narrative 记 2 分、pov 记 2 分；
- 回复替用户行动或说话时，pov 记 2 分；
- 有大段重复、循环句或乱码时，clean 记 2 分。

再按六项打分，每项 1 到 10 分：
- character：角色一致性，像不像沉默观察的调音师
- narrative：叙事完整度，场景、动作、对白是否成段推进，有无半句收尾
- chinese：中文语感是否自然，有无翻译腔、错字、冗余堆砌
- format：场景描写 + 动作 + 中文引号对白 + 字数要求
- clean：无重复退化
- pov：视角正确，扮演角色K而不是替用户行动或说话

只输出 JSON，不要解释：
{"character": 0, "narrative": 0, "chinese": 0, "format": 0, "clean": 0, "pov": 0, "reason": "一句话"}"""

JSON_RE = re.compile(r"\{.*\}", re.S)


def judge_one(base: str, content: str, facts: str = "") -> dict:
    user_text = content
    if facts:
        user_text = f"机械统计：{facts}\n\n回复正文：\n{content}"
    payload = {
        "model": "local",
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": 400,
        "temperature": 0.0,
        "seed": 42,
        "response_format": {"type": "json_object"},
    }
    resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=1800.0)
    resp.raise_for_status()
    body = resp.json()
    text = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    match = JSON_RE.search(text)
    if not match:
        return {"error": "裁判输出无法解析", "raw": text[:400]}
    text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"error": f"裁判输出解析失败: {exc}", "raw": text[:400]}
    scores = {}
    for key in ("character", "narrative", "chinese", "format", "clean", "pov"):
        try:
            scores[key] = float(data.get(key) or 0)
        except (TypeError, ValueError):
            scores[key] = 0.0
    scores["reason"] = str(data.get("reason") or "")[:200]
    return scores


def mechanical_adjust(scores: dict, metrics: dict) -> dict:
    """机械校正：把可本地判定的事实按硬性规则压到对应上限。"""
    out = dict(scores)
    chars = int(metrics.get("chars") or 0)
    dialogues = int(metrics.get("dialogue_count") or 0)
    if dialogues <= 0:
        out["format"] = min(float(out.get("format") or 0), 2.0)
    if chars < 100:
        out["narrative"] = min(float(out.get("narrative") or 0), 3.0)
        out["format"] = min(float(out.get("format") or 0), 3.0)
    if chars > 600:
        out["narrative"] = min(float(out.get("narrative") or 0), 6.0)
    if float(metrics.get("repeat_rate") or 0) > 0.08:
        out["clean"] = min(float(out.get("clean") or 0), 2.0)
    return out


def main() -> int:
    sweep_path = SWEEP_DIR / "sweep.json"
    if not sweep_path.exists():
        print("缺少 sweep.json，先跑 sweep_models.py")
        return 1
    entries = json.loads(sweep_path.read_text(encoding="utf-8"))
    wanted = [a.lower() for a in sys.argv[1:]]
    redo = "--redo" in wanted
    wanted = [a for a in wanted if a != "--redo"]

    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(JUDGE_MODEL, JUDGE_ARGS, port)
    scores: dict[str, object] = {}
    out_path = SWEEP_DIR / "judge.json"
    if out_path.exists() and not redo:
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8")).get("scores") or {}
            scores.update(previous)
        except (OSError, ValueError):
            pass
    try:
        if not wait_ready(base, proc):
            print("裁判服务启动超时")
            return 1
        time.sleep(1.0)
        for entry in entries:
            name = str(entry.get("name") or "")
            if "error" in entry or "sample_p3" not in entry:
                continue
            if wanted and not any(w in name.lower() for w in wanted):
                continue
            if name in scores and not redo:
                print(f"跳过 {name}（已有评分）", flush=True)
                continue
            if JUDGE_NAME in name:
                scores[name] = {"skipped": "裁判模型自身不自评"}
                print(f"跳过 {name}（裁判模型自身）", flush=True)
                continue
            content = str(entry.get("sample_p3", {}).get("content") or "")
            if not content.strip():
                scores[name] = {"error": "样例为空"}
                continue
            metrics = entry.get("sample_p3", {}).get("metrics") or {}
            facts = (
                f"字数 {int(metrics.get('chars') or 0)}，"
                f"引号对白 {int(metrics.get('dialogue_count') or 0)} 段，"
                f"非对白描写句 {int(metrics.get('narration_sentences') or 0)} 句，"
                f"以句末标点收尾 {'是' if metrics.get('ends_clean') else '否'}"
            )
            print(f"评判 {name} ...", flush=True)
            raw = judge_one(base, content, facts)
            if "error" in raw:
                scores[name] = raw
            else:
                scores[name] = mechanical_adjust(raw, metrics)
            print(f"    {scores[name]}", flush=True)
    finally:
        stop_server(proc, log_path, handle)
        wait_gpu_free()

    out = out_path
    out.write_text(
        json.dumps({"judge_model": JUDGE_NAME, "scores": scores}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
