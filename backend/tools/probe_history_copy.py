r"""历史形态与复读的对照探针。

问题：自由回合契约下，历史里原本带着一段固定长度样例，用来给模型当长度
参照。拆掉它之后，历史里的角色回复只剩模型自己写的句子，模型开始逐字
照搬上一轮的对白。这里对照三种历史形态，量化复读率。

三个臂（只改历史形态，其余条件完全相同）：
- A 现状：场景末句 + 对白末句
- B 不要对白：只留场景末句，角色说过的话不进历史
- C 角色回复不进历史：历史里只有用户消息，事实全靠状态块回灌

每个臂跑同一组四轮输入，度量：
- 对白逐字重复：与上一轮对白完全相同的轮数
- 对白开头重复：与上一轮对白前六个字相同的轮数
- 场景复制度：与上一轮场景的字符 4-gram 双向包含率
- 正文长度

用法：
    python backend\tools\probe_history_copy.py
输出：
    docs\实践情况\data\sweep\history_copy.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402
from app.assembly import pipeline  # noqa: E402
from app.contracts import ChatMessage, ChatRequest, XwOptions  # noqa: E402
from app.inference.structure import split_blocks  # noqa: E402

INFERENCE = os.environ.get("XW_INFERENCE", "http://127.0.0.1:8080")
MODEL = os.environ.get(
    "XW_MODEL",
    r"E:\X-WORLD\models\hf\ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf",
)
CHARACTER = "c_demo"
PRESET = "p_builtin_rp"
STRUCTURE = ["scene", "dialogue", "state"]
STATE_FILE = (
    Path(os.environ.get("APPDATA", "")) / "X-WORLD" / "state" / CHARACTER / "state.json"
)

PROMPTS = [
    "外面雨还没停，你不去看看吗？",
    "前面有家地址V，我们进去吧。",
    "点单，看看喝什么。",
    "你好",
]


def ngrams(text: str, size: int = 4) -> set[str]:
    """字符 n-gram 集合，空白去掉。"""
    flat = "".join((text or "").split())
    return {flat[i : i + size] for i in range(max(0, len(flat) - size + 1))}


def contain_ratio(left: str, right: str) -> float:
    """两个文本的 n-gram 双向包含率，取较大的一侧。"""
    a = ngrams(left)
    b = ngrams(right)
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return round(max(shared / len(a), shared / len(b)), 3)


def arm_b_history_reply(content: str, blocks: dict[str, Any] | None) -> str:
    """臂 B：只留场景末句，不要对白。"""
    if not isinstance(blocks, dict) or not blocks:
        return pipeline._last_sentence(content)
    scene = str(blocks.get("scene") or "").strip()
    if not scene:
        return pipeline._last_sentence(content)
    return pipeline._scene_result(scene, pipeline.HISTORY_SCENE_CHARS)


def arm_c_shape_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """臂 C：角色回复整条不进历史，历史里只有用户消息。"""
    shaped: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        row.pop("blocks", None)
        if str(row.get("role", "")) == "assistant":
            continue
        shaped.append(row)
    return shaped


ARMS: dict[str, tuple[Callable[..., Any], Callable[..., Any]]] = {
    "a_current": (pipeline._history_reply, pipeline._shape_history),
    "b_no_dialogue": (arm_b_history_reply, pipeline._shape_history),
    "c_user_only": (pipeline._history_reply, arm_c_shape_history),
}


def reset_state() -> None:
    """把角色级状态恢复成探针开始前的样子，保证三个臂的起点一致。"""
    if not STATE_FILE.exists():
        return
    original = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    store.write_json(STATE_FILE, original)


def run_arm(client: httpx.Client, arm: str) -> list[dict[str, Any]]:
    """跑一个臂的四轮对话，收集每轮的块与指标。"""
    reply_patcher, shape_patcher = ARMS[arm]
    saved_reply = pipeline._history_reply
    saved_shape = pipeline._shape_history
    pipeline._history_reply = reply_patcher
    pipeline._shape_history = shape_patcher
    session = "s_copy_" + arm
    try:
        store.ensure_session(session, CHARACTER, PRESET)
        rows: list[dict[str, Any]] = []
        for prompt in PROMPTS:
            store.append_message(session, CHARACTER, {"role": "user", "content": prompt})
            request = ChatRequest(
                model="xw-local-default",
                messages=[ChatMessage(role="user", content=prompt)],
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
            ctx = pipeline.assemble(request)
            started = time.time()
            response = client.post(
                INFERENCE + "/v1/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": m.role, "content": m.content} for m in ctx.messages
                    ],
                    "stream": False,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_tokens": 768,
                    "repetition_penalty": 1.1,
                },
                timeout=300.0,
            )
            response.raise_for_status()
            elapsed = round(time.time() - started, 2)
            content = response.json()["choices"][0]["message"].get("content") or ""
            blocks = split_blocks(content, STRUCTURE)
            scene = blocks.get("scene", "")
            dialogue = blocks.get("dialogue", "")
            store.append_message(
                session,
                CHARACTER,
                {
                    "role": "assistant",
                    "content": scene + chr(10) + dialogue,
                    "blocks": blocks,
                },
            )
            rows.append(
                {
                    "prompt": prompt,
                    "scene": scene,
                    "dialogue": dialogue,
                    "body_chars": len(scene) + len(dialogue),
                    "elapsed": elapsed,
                }
            )
        return rows
    finally:
        pipeline._history_reply = saved_reply
        pipeline._shape_history = saved_shape


def measure(arm: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """统计一个臂的复读指标。"""
    exact = 0
    prefix = 0
    ratios: list[float] = []
    for index in range(1, len(rows)):
        prev = rows[index - 1]
        cur = rows[index]
        if prev["dialogue"] and cur["dialogue"] == prev["dialogue"]:
            exact += 1
        if prev["dialogue"] and cur["dialogue"].startswith(prev["dialogue"][:6]):
            prefix += 1
        ratios.append(contain_ratio(cur["scene"], prev["scene"]))
    return {
        "arm": arm,
        "turns": len(rows),
        "dialogue_exact_repeat": exact,
        "dialogue_prefix_repeat": prefix,
        "avg_scene_copy_ratio": round(sum(ratios) / len(ratios), 3) if ratios else 0.0,
        "max_scene_copy_ratio": max(ratios) if ratios else 0.0,
        "avg_body_chars": round(sum(r["body_chars"] for r in rows) / len(rows), 1),
        "avg_elapsed": round(sum(r["elapsed"] for r in rows) / len(rows), 2),
    }


def main() -> int:
    """跑完三个臂并落盘。"""
    results: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    with httpx.Client() as client:
        for arm in ARMS:
            reset_state()
            rows = run_arm(client, arm)
            summary = measure(arm, rows)
            results[arm] = summary
            detail[arm] = rows
            print(
                "%-14s 逐字重复=%d 开头重复=%d 场景复制度均值=%.3f 最大=%.3f 均长=%.1f %.2fs"
                % (
                    arm,
                    summary["dialogue_exact_repeat"],
                    summary["dialogue_prefix_repeat"],
                    summary["avg_scene_copy_ratio"],
                    summary["max_scene_copy_ratio"],
                    summary["avg_body_chars"],
                    summary["avg_elapsed"],
                ),
                flush=True,
            )
            for row in rows:
                print("   输入=%s 对白=%s" % (row["prompt"], row["dialogue"]), flush=True)

    out = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "history_copy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"summary": results, "detail": detail}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("written " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
