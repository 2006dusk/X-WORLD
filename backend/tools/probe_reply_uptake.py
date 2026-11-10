# -*- coding: utf-8 -*-
r"""回应承接对照探针（第二版）：场景进不进历史，到底影响承接还是影响照搬。

第一版结论：把用户输入放到最后、去掉状态提示、显式要求先回应动作，三种改法
承接率都在 0 到 25% 之间，说明问题不在措辞。这一版把「历史里有没有上一轮
场景」作为唯一变量，同时量两件事：动作承接率与场景照搬率。

五个臂：
  current            台词 only + 回合要求 + 状态提示（线上口径）
  no_state           台词 only + 回合要求，无状态提示
  scene_tail         台词 + 场景末句 + 回合要求 + 状态提示
  scene_tail_nostate 台词 + 场景末句 + 回合要求，无状态提示
  full_scene         台词 + 完整场景 + 回合要求 + 状态提示（照搬上限参照）

用法：
    python backend\tools\probe_reply_uptake.py [会话ID] [重复次数]

输出：
    docs\实践情况\data\sweep\reply_uptake.json
    docs\实践情况\data\sweep\reply_uptake.txt
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

INFERENCE = os.environ.get("XW_INFERENCE", "http://127.0.0.1:8080")
MODEL = os.environ.get(
    "XW_MODEL",
    r"E:\X-WORLD\models\hf\ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf",
)
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")
BASE_SEED = 20261107
GRAM = 8

# 每个用户输入对应一组承接关键词：回复里命中即视为接住了这一轮。
CASES: dict[str, list[str]] = {
    "亲吻对方": ["吻", "唇", "亲", "贴近", "抱", "揽", "脸颊", "额头", "呼吸", "肩"],
    "进入卧室": ["卧室", "房门", "床", "关上", "推开", "房间"],
    "有什么喝的吗": ["咖啡", "拿铁", "美式", "茶", "喝", "酒", "饮", "水"],
}

ARMS = (
    "current",
    "no_state",
    "scene_tail",
    "scene_tail_nostate",
    "full_scene",
)


def _rows_upto(session: str, leaf_id: str) -> list[dict[str, Any]]:
    """历史窗口：从会话路径取到指定叶子为止的若干行。"""
    rows = store.session_path(session, CHARACTER, leaf_id)
    keep = pipeline.HISTORY_KEEP_TURNS * 2
    return rows[-keep:]


def _blocks_of(text: str) -> dict[str, str]:
    """从回合文本里切出块。标记与内容可能在同一行。"""
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
    """字符 n 元组集合，用于判定照搬。"""
    return {text[i : i + GRAM] for i in range(0, max(len(text) - GRAM, 0))}


def _overlap(text: str, other: str) -> float:
    """与参照文本的 n 元组重合比例。"""
    mine = _grams(text)
    if not mine:
        return 0.0
    return round(len(mine & _grams(other)) / len(mine), 4)


def _request(user_text: str, session: str) -> ChatRequest:
    """构造与线上一致的生成请求。"""
    return ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_text)],
        session=session,
        stream=False,
        xw=XwOptions(
            character_id=CHARACTER,
            preset_id=PRESET,
            world_book_ids=["w_demo"],
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )


def _history_for(rows: list[dict[str, Any]], mode: str) -> list[dict[str, str]]:
    """按臂构造历史消息。"""
    out: list[dict[str, str]] = []
    for row in rows:
        role = str(row.get("role", ""))
        content = str(row.get("content", ""))
        blocks = row.get("blocks") if isinstance(row.get("blocks"), dict) else {}
        if role != "assistant":
            out.append({"role": role, "content": content})
            continue
        dialogue = str(blocks.get("dialogue") or "").strip()
        scene = str(blocks.get("scene") or "").strip()
        if mode in ("current", "no_state"):
            out.append({"role": role, "content": dialogue or pipeline.NO_SPEECH_MARKER})
            continue
        if mode in ("scene_tail", "scene_tail_nostate"):
            tail = pipeline._scene_result(scene, pipeline.HISTORY_SCENE_CHARS)
            text = tail
            if dialogue:
                text = (tail + " " + dialogue).strip()
            out.append({"role": role, "content": text or pipeline.NO_SPEECH_MARKER})
            continue
        # full_scene
        text = scene
        if dialogue:
            text = (scene + " " + dialogue).strip()
        out.append({"role": role, "content": text or pipeline.NO_SPEECH_MARKER})
    return out


def _sample(client: httpx.Client, payload: list[dict[str, str]], seed: int) -> str:
    """调推理服务取一轮回复。"""
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
    repeat = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    only = sys.argv[3] if len(sys.argv) > 3 else ""

    rows_all = store.session_path(session, CHARACTER, None)
    targets: list[dict[str, Any]] = []
    for row in rows_all:
        if str(row.get("role")) != "user":
            continue
        text = str(row.get("content", ""))
        if text in CASES and (not only or text == only):
            targets.append(row)
    if not targets:
        print("no matched user input")
        return 1
    # 取最后出现的那个匹配输入
    target = targets[-1]
    user_text = str(target.get("content", ""))
    parent = str(target.get("parent_id") or "")

    request = _request(user_text, session)
    ctx = pipeline.assemble(request, leaf_id=parent)
    spec = preset_service.get_preset(PRESET) or {}
    output_spec = spec.get("output_spec") if isinstance(spec, dict) else {}
    output_spec = output_spec if isinstance(output_spec, dict) else {}
    directive = preset_service.render_output_spec(output_spec)
    state_hint = pipeline._state_hint(request.xw)
    prefix = [
        {"role": m.role, "content": m.content}
        for m in ctx.messages
        if m.role == "system"
    ]

    hist_rows = _rows_upto(session, parent)
    prev_scene = ""
    for row in reversed(hist_rows):
        if str(row.get("role")) == "assistant":
            blocks = row.get("blocks") if isinstance(row.get("blocks"), dict) else {}
            prev_scene = str(blocks.get("scene") or "")
            break

    keywords = CASES.get(user_text, [user_text])
    lines: list[str] = []
    lines.append("session=" + session)
    lines.append("user_text=" + user_text)
    lines.append("node=" + str(target.get("id")) + " parent=" + parent)
    lines.append("keywords=" + ",".join(keywords))
    lines.append("prev_scene_len=" + str(len(prev_scene)))
    lines.append("")
    lines.append("=== history rows (last window) ===")
    for row in hist_rows:
        blocks = row.get("blocks") if isinstance(row.get("blocks"), dict) else {}
        lines.append(
            str(row.get("role"))
            + " | dlg="
            + str(blocks.get("dialogue", ""))[:80]
            + " | scene="
            + str(blocks.get("scene", ""))[:80]
        )
    lines.append("")
    lines.append("=== production user message ===")
    lines.append(ctx.messages[-1].content)
    lines.append("")

    results: dict[str, Any] = {}
    with httpx.Client() as client:
        for arm in ARMS:
            samples = []
            for index in range(repeat):
                history = _history_for(hist_rows, arm)
                if arm in ("no_state", "scene_tail_nostate"):
                    user_content = user_text + "\n\n" + directive
                else:
                    user_content = user_text + "\n\n" + directive
                    if state_hint:
                        user_content = user_content + "\n\n" + state_hint
                payload = prefix + history + [{"role": "user", "content": user_content}]
                reply = _sample(client, payload, BASE_SEED + index)
                blocks = _blocks_of(reply)
                scene = blocks.get("场景", "")
                dialogue = blocks.get("对话", "")
                uptake = any(word in reply for word in keywords)
                copy_rate = _overlap(scene, prev_scene) if prev_scene else 0.0
                samples.append(
                    {
                        "seed": BASE_SEED + index,
                        "reply": reply,
                        "chars": len(reply),
                        "scene_chars": len(scene),
                        "dialogue_chars": len(dialogue),
                        "uptake": uptake,
                        "copy_rate": copy_rate,
                    }
                )
                lines.append(
                    "[%s] seed=%d uptake=%s dlg=%d scene=%d copy=%.3f :: %s"
                    % (
                        arm,
                        BASE_SEED + index,
                        uptake,
                        len(dialogue),
                        len(scene),
                        copy_rate,
                        reply.replace("\n", " / ")[:130],
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
                "avg_scene_chars": round(
                    sum(s["scene_chars"] for s in samples) / len(samples), 1
                ),
                "avg_copy_rate": round(
                    sum(s["copy_rate"] for s in samples) / len(samples), 4
                ),
            }
            lines.append(
                "== %-18s uptake=%.2f dlg=%.1f scene=%.1f copy=%.3f"
                % (
                    arm,
                    results[arm]["uptake_rate"],
                    results[arm]["avg_dialogue_chars"],
                    results[arm]["avg_scene_chars"],
                    results[arm]["avg_copy_rate"],
                )
            )
            lines.append("")

    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session": session,
        "node": target.get("id"),
        "parent": parent,
        "user_text": user_text,
        "keywords": keywords,
        "repeat": repeat,
        "production_user_message": ctx.messages[-1].content,
        "results": results,
    }
    (out_dir / "reply_uptake.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "reply_uptake.txt").write_text("\n".join(lines), encoding="utf-8")
    print("written " + str(out_dir / "reply_uptake.json"))
    for arm, res in results.items():
        print(
            "%-18s uptake=%.2f dlg=%.1f scene=%.1f copy=%.3f"
            % (
                arm,
                res["uptake_rate"],
                res["avg_dialogue_chars"],
                res["avg_scene_chars"],
                res["avg_copy_rate"],
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
