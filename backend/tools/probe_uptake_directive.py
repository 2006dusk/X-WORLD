# -*- coding: utf-8 -*-
r"""回合要求对照探针：对白要不要被要求「回应用户这一轮」。

回答的问题：模型不搭理用户，是提示没写清，还是模型能力上限。

做法：真实装配一次，取线上的 system、历史与状态提示，只改最后的回合要求，
跑若干臂，每臂多次采样，量三件事：
  承接率   —— 回复里是否出现用户这一轮动作的关键词
  对白长度 —— 对白块字数与句数
  照搬率   —— 场景块与上一轮场景的字符 n 元组重合比例

用法：
    python backend\tools\probe_uptake_directive.py [会话ID] [重复次数] [用户输入]

输出：
    docs\实践情况\data\sweep\uptake_directive.json
    docs\实践情况\data\sweep\uptake_directive.txt
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app import presets as preset_service  # noqa: E402
from app import store  # noqa: E402
from app.assembly import pipeline  # noqa: E402
from app.contracts import ChatMessage, ChatRequest, XwOptions  # noqa: E402
from local_paths import model_file  # noqa: E402


INFERENCE = os.environ.get("XW_INFERENCE", "http://127.0.0.1:8080")
MODEL = os.environ.get(
    "XW_MODEL",
    model_file("hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"),
)
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")
BASE_SEED = 20261108
GRAM = 8

CASES: dict[str, list[str]] = {
    "亲吻对方": ["吻", "唇", "亲", "贴近", "抱", "揽", "脸颊", "额头", "呼吸", "肩"],
    "晚上好": ["晚上", "夜", "你好", "问候", "回来", "睡", "晚安", "见"],
    "有什么喝的吗": ["咖啡", "拿铁", "美式", "茶", "喝", "酒", "饮", "水"],
}

# 追加到回合要求末尾的要求句。
EXTRA = {
    "base": "",
    "must_reply": "对白必须直接回应用户这一轮说的话或动作，不要写与本轮无关的台词。",
    "must_reply_two": (
        "对白必须直接回应用户这一轮说的话或动作，"
        "并且至少写两句，不要写与本轮无关的台词。"
    ),
}
ARMS = ("base", "must_reply", "must_reply_two")


def _blocks_of(text: str) -> dict[str, str]:
    """从回合文本里切出块，标记与内容可能在同一行。"""
    out: dict[str, str] = {}
    current = ""
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("【") and "】" in stripped:
            end = stripped.find("】")
            if current:
                out[current] = "\n".join(buffer).strip()
            current = stripped[1:end]
            buffer = []
            rest = stripped[end + 1 :].strip()
            if rest:
                buffer.append(rest)
            continue
        if current:
            buffer.append(line)
    if current:
        out[current] = "\n".join(buffer).strip()
    return out


def _grams(text: str) -> set[str]:
    return {text[i : i + GRAM] for i in range(0, max(len(text) - GRAM, 0))}


def _overlap(text: str, other: str) -> float:
    mine = _grams(text)
    if not mine:
        return 0.0
    return round(len(mine & _grams(other)) / len(mine), 4)


def _sentences(text: str) -> int:
    """按中文句末标点计数。"""
    return sum(1 for ch in text if ch in "。！？")


def _sample(client: httpx.Client, payload: list[dict[str, str]], seed: int) -> str:
    response = client.post(
        INFERENCE + "/v1/chat/completions",
        json={
            "model": MODEL,
            "messages": payload,
            "stream": False,
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 768,
            "repeat_penalty": 1.1,
            "seed": seed,
        },
        timeout=300.0,
    )
    response.raise_for_status()
    body = response.json()
    choice = (body.get("choices") or [{}])[0]
    return str(choice.get("message", {}).get("content") or "")


def main() -> int:
    """跑对照。"""
    session = sys.argv[1] if len(sys.argv) > 1 else "s_demo"
    repeat = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    wanted = sys.argv[3] if len(sys.argv) > 3 else ""

    rows = store.session_path(session, CHARACTER, None)
    target = None
    for row in rows:
        if str(row.get("role")) != "user":
            continue
        text = str(row.get("content", ""))
        if text in CASES and (not wanted or text == wanted):
            target = row
    if target is None:
        print("no matched user input")
        return 1
    user_text = str(target.get("content", ""))
    parent = str(target.get("parent_id") or "")

    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_text)],
        session=session,
        stream=False,
        xw=XwOptions(
            character_id=CHARACTER,
            preset_id=PRESET,
            world_book_ids=["w_demo"],
            memory_mode="hybrid",
            max_context_tokens=16384,
        ),
    )
    ctx = pipeline.assemble(request, leaf_id=parent)
    spec = preset_service.get_preset(PRESET) or {}
    output_spec = spec.get("output_spec") if isinstance(spec, dict) else {}
    output_spec = output_spec if isinstance(output_spec, dict) else {}
    directive = preset_service.render_output_spec(output_spec)
    state_hint = pipeline._state_hint(request.xw)

    prefix = [{"role": m.role, "content": m.content} for m in ctx.messages[:-1]]
    keywords = CASES[user_text]

    prev_scene = ""
    for row in reversed(store.session_path(session, CHARACTER, parent)):
        if str(row.get("role")) == "assistant":
            blocks = row.get("blocks") if isinstance(row.get("blocks"), dict) else {}
            prev_scene = str(blocks.get("scene") or "")
            break

    lines: list[str] = []
    lines.append("session=" + session)
    lines.append("user_text=" + user_text)
    lines.append("history_messages=" + str(len(prefix) - 1))
    lines.append("")
    results: dict[str, Any] = {}
    with httpx.Client() as client:
        for arm in ARMS:
            content = user_text + "\n\n" + directive
            extra = EXTRA[arm]
            if extra:
                content = content + extra
            if state_hint:
                content = content + "\n\n" + state_hint
            payload = prefix + [{"role": "user", "content": content}]
            samples = []
            for index in range(repeat):
                reply = _sample(client, payload, BASE_SEED + index)
                blocks = _blocks_of(reply)
                scene = blocks.get("场景", "")
                dialogue = blocks.get("对话", "")
                samples.append(
                    {
                        "seed": BASE_SEED + index,
                        "reply": reply,
                        "uptake": any(word in reply for word in keywords),
                        "dialogue_chars": len(dialogue),
                        "dialogue_sentences": _sentences(dialogue),
                        "scene_chars": len(scene),
                        "copy_rate": _overlap(scene, prev_scene) if prev_scene else 0.0,
                    }
                )
                lines.append(
                    "[%s] seed=%d uptake=%s dlg=%d句=%d scene=%d copy=%.3f :: %s"
                    % (
                        arm,
                        BASE_SEED + index,
                        samples[-1]["uptake"],
                        len(dialogue),
                        samples[-1]["dialogue_sentences"],
                        len(scene),
                        samples[-1]["copy_rate"],
                        reply.replace("\n", " / ")[:120],
                    )
                )
            results[arm] = {
                "samples": samples,
                "uptake_rate": round(
                    sum(1 for s in samples if s["uptake"]) / len(samples), 3
                ),
                "avg_dialogue_chars": round(
                    sum(s["dialogue_chars"] for s in samples) / len(samples), 1
                ),
                "avg_dialogue_sentences": round(
                    sum(s["dialogue_sentences"] for s in samples) / len(samples), 2
                ),
                "avg_scene_chars": round(
                    sum(s["scene_chars"] for s in samples) / len(samples), 1
                ),
                "avg_copy_rate": round(
                    sum(s["copy_rate"] for s in samples) / len(samples), 4
                ),
            }
            lines.append(
                "== %-16s uptake=%.2f dlg=%.1f句=%.2f scene=%.1f copy=%.3f"
                % (
                    arm,
                    results[arm]["uptake_rate"],
                    results[arm]["avg_dialogue_chars"],
                    results[arm]["avg_dialogue_sentences"],
                    results[arm]["avg_scene_chars"],
                    results[arm]["avg_copy_rate"],
                )
            )
            lines.append("")

    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "uptake_directive.json").write_text(
        json.dumps(
            {
                "session": session,
                "user_text": user_text,
                "keywords": keywords,
                "repeat": repeat,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "uptake_directive.txt").write_text("\n".join(lines), encoding="utf-8")
    print("written " + str(out_dir / "uptake_directive.json"))
    for arm, res in results.items():
        print(
            "%-16s uptake=%.2f dlg=%.1f sent=%.2f scene=%.1f copy=%.3f"
            % (
                arm,
                res["uptake_rate"],
                res["avg_dialogue_chars"],
                res["avg_dialogue_sentences"],
                res["avg_scene_chars"],
                res["avg_copy_rate"],
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
