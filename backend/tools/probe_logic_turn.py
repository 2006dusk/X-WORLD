r"""回合逻辑的对照探针。

用一段固定的多轮对话量化三件事：有没有回应本轮输入、有没有照搬上一轮、
有没有把角色演成场景里的工作人员。

对话取自真实使用：用户打招呼、问喝什么、推进、确认、表示喝完、应一声。
每臂跑同一条对话，除了历史形态以外的条件完全相同。

历史形态五个臂：
- a_legacy       旧口径：场景末句 + 对白末句
- b_no_dialogue  不留对白：只留场景末句
- c_user_only    角色回复不进历史：历史里只有用户消息
- d_state_only   角色回复换成状态块：只留键值事实，不留叙述文本
- e_dialogue_only 角色回复只留台词：丢弃场景叙述，保留说过的话

两段对话：
- 默认六轮：打招呼、问喝什么、推进、确认、表示喝完、应一声。
- 上下文依赖段（环境变量 XW_CONVO=ctx）：晚上好、有什么喝的吗、有酒吗、好吧。
  第四轮是依赖上文的回应，模型若没看到自己说过什么就会答错。

用法：
    python backend\tools\probe_logic_turn.py [臂名] [模型路径]
输出：
    docs\实践情况\data\sweep\logic_turn.json
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
from app.state import parse_state_block, render_state_block  # noqa: E402

INFERENCE = os.environ.get("XW_INFERENCE", "http://127.0.0.1:8080")
CHARACTER = "c_demo"
PRESET = "p_builtin_rp"
STRUCTURE = ["scene", "dialogue", "state"]

CONVO = [
    "晚上好",
    "有什么喝的吗",
    "（继续）",
    "好",
    "（喝完了）",
    "嗯哼",
]

# 每轮期望在回复里出现的词。没有期望词的轮次只算重复与漂移。
EXPECT: list[list[str]] = [
    [],
    ["喝", "咖啡", "酒", "威士忌", "茶", "水"],
    [],
    [],
    ["杯", "空瓶", "喝完", "放下", "收"],
    [],
]

# 上下文依赖段：第四轮只说「好吧」，模型必须接住上文才有意义。
CONVO_CTX = [
    "晚上好",
    "有什么喝的吗",
    "有酒吗",
    "好吧",
]

EXPECT_CTX: list[list[str]] = [
    [],
    ["喝", "咖啡", "酒", "茶", "水", "威士忌"],
    ["酒", "没有", "不", "威士忌"],
    ["好", "嗯", "明白", "行", "知道了", "喝", "酒", "咖啡", "不"],
]

# 重复发问：模型把已经答过的问题再问一遍，说明它没接住上文。
REASK = ("喝点什么", "要来点", "想喝点", "需要喝", "要喝点", "喝点什么")

# 店员行为词：角色被演成场景里的工作人员时会出现。
STAFF_WORDS = (
    "招呼客人",
    "招呼",
    "点单",
    "擦杯",
    "收银",
    "订单",
    "为您服务",
    "做给客人",
    "端给客人",
    "递向顾客",
    "后厨",
)


def ngrams(text: str, size: int = 4) -> set[str]:
    """字符 n-gram 集合。"""
    flat = "".join((text or "").split())
    return {flat[i : i + size] for i in range(max(0, len(flat) - size + 1))}


def contain_ratio(left: str, right: str) -> float:
    """两个文本的 n-gram 双向包含率。"""
    a = ngrams(left)
    b = ngrams(right)
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return round(max(shared / len(a), shared / len(b)), 3)


def shared_phrase(left: str, right: str, size: int = 10) -> str:
    """找出两段文本里长度不少于 size 的公共连续片段。"""
    flat_left = "".join((left or "").split())
    flat_right = "".join((right or "").split())
    for size_now in range(min(len(flat_left), len(flat_right)), size - 1, -1):
        for start in range(len(flat_left) - size_now + 1):
            piece = flat_left[start : start + size_now]
            if piece and piece in flat_right:
                return piece
    return ""


def arm_b_history_reply(content: str, blocks: dict[str, Any] | None) -> str:
    """臂 B：只留场景末句，不要对白。"""
    if not isinstance(blocks, dict) or not blocks:
        return pipeline._last_sentence(content)
    scene = str(blocks.get("scene") or "").strip()
    if not scene:
        return pipeline._last_sentence(content)
    return pipeline._scene_result(scene, pipeline.HISTORY_SCENE_CHARS)


def arm_c_shape_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """臂 C：角色回复整条不进历史。"""
    shaped: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        row.pop("blocks", None)
        if str(row.get("role", "")) == "assistant":
            continue
        shaped.append(row)
    return shaped


def arm_d_shape_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """臂 D：角色回复换成状态块，只留键值事实。"""
    shaped: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        blocks = row.pop("blocks", None)
        if str(row.get("role", "")) == "assistant":
            fields = {}
            if isinstance(blocks, dict):
                fields = parse_state_block(str(blocks.get("state") or ""))
            row["content"] = render_state_block(fields) or "（上一轮没有状态）"
        shaped.append(row)
    return shaped


def arm_a_history_reply(content: str, blocks: dict[str, Any] | None) -> str:
    """臂 A：旧口径，场景末句 + 对白末句。

    这套形态已经在生产里跑过：模型把自己写过的句子当素材，顺着自己的话走。
    这里的实现写成局部函数，不再引用管线里的当前实现，否则历史形态改了
    这个臂就跟着变，对照失去意义。
    """
    if not isinstance(blocks, dict) or not blocks:
        return pipeline._last_sentence(content)
    scene = str(blocks.get("scene") or "").strip()
    dialogue = str(blocks.get("dialogue") or "").strip()
    if not scene:
        return pipeline._last_sentence(content)
    parts = [pipeline._scene_result(scene, pipeline.HISTORY_SCENE_CHARS)]
    if dialogue:
        parts.append(pipeline._last_sentence(dialogue))
    return chr(10).join(parts)


def arm_e_history_reply(content: str, blocks: dict[str, Any] | None) -> str:
    """臂 E：角色回复只留台词，丢弃场景叙述。

    实现写成局部函数，不引用管线里的当前实现，否则历史形态一改
    这个臂就跟着变，对照失去意义。
    """
    if isinstance(blocks, dict):
        dialogue = str(blocks.get("dialogue") or "").strip()
        if dialogue:
            return dialogue
    quoted = pipeline._last_quoted(content)
    if quoted:
        return quoted
    return "（这一轮没有台词）"


ARMS: dict[str, tuple[Callable[..., Any], Callable[..., Any]]] = {
    "a_legacy": (arm_a_history_reply, pipeline._shape_history),
    "b_no_dialogue": (arm_b_history_reply, pipeline._shape_history),
    "c_user_only": (pipeline._history_reply, arm_c_shape_history),
    "d_state_only": (pipeline._history_reply, arm_d_shape_history),
    "e_dialogue_only": (arm_e_history_reply, pipeline._shape_history),
}


def run_arm(
    client: httpx.Client,
    arm: str,
    model: str,
    tag: str = "",
) -> list[dict[str, Any]]:
    """跑一个臂的六轮对话。"""
    reply_patcher, shape_patcher = ARMS[arm]
    saved_reply = pipeline._history_reply
    saved_shape = pipeline._shape_history
    pipeline._history_reply = reply_patcher
    pipeline._shape_history = shape_patcher
    session = "s_logic_" + arm + tag
    try:
        store.ensure_session(session, CHARACTER, PRESET)
        # 清空上一轮探针留下的会话，保证每次采样从同样的起点开始
        store.truncate_messages(session, CHARACTER, 0)
        rows: list[dict[str, Any]] = []
        for prompt in CONVO:
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
                    "model": model,
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
                    "body": scene + chr(10) + dialogue,
                    "body_chars": len(scene) + len(dialogue),
                    "elapsed": elapsed,
                }
            )
        return rows
    finally:
        pipeline._history_reply = saved_reply
        pipeline._shape_history = saved_shape


def measure(arm: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """统计一个臂的逻辑指标。"""
    answered = 0
    answerable = 0
    repeated = 0
    staff = 0
    reask = 0
    ratios: list[float] = []
    details: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        expect = EXPECT[index]
        hit = False
        if expect:
            answerable += 1
            hit = any(word in row["body"] for word in expect)
            if hit:
                answered += 1
        phrase = ""
        if index > 0:
            phrase = shared_phrase(row["body"], rows[index - 1]["body"])
            if phrase:
                repeated += 1
            ratios.append(contain_ratio(row["scene"], rows[index - 1]["scene"]))
        staff_words = [word for word in STAFF_WORDS if word in row["body"]]
        if staff_words:
            staff += 1
        reask_words = [word for word in REASK if word in row["body"]]
        if reask_words:
            reask += 1
        details.append(
            {
                "prompt": row["prompt"],
                "expect_hit": hit,
                "copied_phrase": phrase,
                "staff_words": staff_words,
                "reask_words": reask_words,
                "scene_copy_ratio": ratios[-1] if ratios else 0.0,
                "body_chars": row["body_chars"],
                "elapsed": row["elapsed"],
                "body": row["body"],
            }
        )
    return {
        "arm": arm,
        "turns": len(rows),
        "answerable": answerable,
        "answered": answered,
        "answer_rate": round(answered / answerable, 3) if answerable else 0.0,
        "repeated_turns": repeated,
        "staff_turns": staff,
        "reask_turns": reask,
        "avg_scene_copy_ratio": round(sum(ratios) / len(ratios), 3) if ratios else 0.0,
        "max_scene_copy_ratio": max(ratios) if ratios else 0.0,
        "avg_body_chars": round(sum(r["body_chars"] for r in rows) / len(rows), 1),
        "avg_elapsed": round(sum(r["elapsed"] for r in rows) / len(rows), 2),
        "details": details,
    }


def main() -> int:
    """跑完所选臂并落盘。"""
    global CONVO, EXPECT
    model = sys.argv[2] if len(sys.argv) > 2 else os.environ.get(
        "XW_MODEL",
        r"E:\X-WORLD\models\hf\ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf",
    )
    use_ctx = os.environ.get("XW_CONVO", "") == "ctx"
    if use_ctx:
        CONVO = CONVO_CTX
        EXPECT = EXPECT_CTX
    wanted = sys.argv[1].split(",") if len(sys.argv) > 1 else []
    arms = [name for name in wanted if name in ARMS] or list(ARMS)
    repeat = int(os.environ.get("XW_REPEAT", "1") or "1")
    results: dict[str, Any] = {}
    with httpx.Client() as client:
        for arm in arms:
            per_round = []
            for index in range(repeat):
                tag = "" if repeat == 1 else "_r%d" % (index + 1)
                rows = run_arm(client, arm, model, tag)
                summary = measure(arm, rows)
                per_round.append(summary)
                print(
                    "%-14s 第%d次 回应=%d/%d 照搬=%d 店员轮=%d 重问=%d 场景复制度均值=%.3f 最大=%.3f 均长=%.1f %.2fs"
                    % (
                        arm,
                        index + 1,
                        summary["answered"],
                        summary["answerable"],
                        summary["repeated_turns"],
                        summary["staff_turns"],
                        summary["reask_turns"],
                        summary["avg_scene_copy_ratio"],
                        summary["max_scene_copy_ratio"],
                        summary["avg_body_chars"],
                        summary["avg_elapsed"],
                    ),
                    flush=True,
                )
                for item in summary["details"]:
                    print(
                        "   输入=%s 命中=%s 照搬=%s 店员词=%s"
                        % (
                            item["prompt"],
                            item["expect_hit"],
                            item["copied_phrase"][:24],
                            ",".join(item["staff_words"]),
                        ),
                        flush=True,
                    )
            results[arm] = {
                "arm": arm,
                "repeats": repeat,
                "runs": per_round,
                "answered": sum(r["answered"] for r in per_round),
                "answerable": sum(r["answerable"] for r in per_round),
                "repeated_turns": sum(r["repeated_turns"] for r in per_round),
                "staff_turns": sum(r["staff_turns"] for r in per_round),
                "reask_turns": sum(r["reask_turns"] for r in per_round),
                "turns": sum(r["turns"] for r in per_round),
                "avg_scene_copy_ratio": round(
                    sum(r["avg_scene_copy_ratio"] for r in per_round) / len(per_round), 3
                ),
                "max_scene_copy_ratio": max(r["max_scene_copy_ratio"] for r in per_round),
                "avg_body_chars": round(
                    sum(r["avg_body_chars"] for r in per_round) / len(per_round), 1
                ),
                "avg_elapsed": round(
                    sum(r["avg_elapsed"] for r in per_round) / len(per_round), 2
                ),
            }
            summary = results[arm]
            print(
                "汇总 %-14s 回应=%d/%d 照搬=%d/%d 店员轮=%d 重问=%d 场景复制度均值=%.3f 最大=%.3f 均长=%.1f %.2fs"
                % (
                    arm,
                    summary["answered"],
                    summary["answerable"],
                    summary["repeated_turns"],
                    summary["turns"],
                    summary["staff_turns"],
                    summary["reask_turns"],
                    summary["avg_scene_copy_ratio"],
                    summary["max_scene_copy_ratio"],
                    summary["avg_body_chars"],
                    summary["avg_elapsed"],
                ),
                flush=True,
            )

    tag = Path(model).stem.split("-")[0].lower()
    suffix = "_ctx" if use_ctx else ""
    if len(arms) != len(ARMS):
        # 只跑部分臂时另存，避免覆盖全量数据
        suffix = suffix + "_" + "-".join(arms)
    out = (
        ROOT.parent
        / "docs"
        / "实践情况"
        / "data"
        / "sweep"
        / ("logic_turn_" + tag + suffix + ".json")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"model": model, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("written " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
