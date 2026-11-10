# -*- coding: utf-8 -*-
"""上下文依赖回合的定点重放探针。

取真实会话里的最后四轮，固定前面三轮，只重放最后一轮。
两种历史形态各生成多次，种子不同，统计矛盾率与重复发问率。

用法：
    python backend\tools\probe_context_replay.py [会话ID] [轮数] [重复次数]
输出：
    docs\实践情况\data\sweep\context_replay.json
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

from app import store  # noqa: E402
from app.assembly import pipeline  # noqa: E402
from app.contracts import ChatMessage, ChatRequest, XwOptions  # noqa: E402
from local_paths import model_file  # noqa: E402


INFERENCE = os.environ.get("XW_INFERENCE", "http://127.0.0.1:8080")
MODEL = os.environ.get(
    "XW_MODEL",
    model_file("hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"),
)
CHARACTER = "c_demo"
PRESET = "p_builtin_rp"
BASE_SEED = 20261106

# 重复发问：模型把已经答过的问题再问一遍，说明它没接住上文。
REASK = ("喝点什么", "要来点", "想喝点", "需要喝", "要喝点")
# 前面轮次否定过的东西，后面又端上来。
DENY = ("没有酒", "没酒", "不卖酒", "不喝酒", "这里没酒", "不提供酒")
OFFER = ("红酒", "威士忌", "白酒", "清酒", "啤酒", "酒瓶", "酒杯", "斟满", "给你倒")


def _rows(session: str) -> list[dict[str, Any]]:
    """读会话流水，去掉会话头。"""
    path = store.sessions_dir(CHARACTER) / (session + ".jsonl")
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("role") == "session":
                continue
            out.append(row)
    return out


def _history_variants(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按两种历史形态构造历史消息。

    state_only：角色回复换成状态块，只留键值事实。
    dialogue_only：角色回复只留台词，丢弃场景叙述。
    """
    variants: dict[str, list[dict[str, Any]]] = {
        "state_only": [],
        "dialogue_only": [],
    }
    for row in rows:
        role = str(row.get("role", ""))
        if role == "user":
            for key in variants:
                variants[key].append({"role": "user", "content": str(row.get("content", ""))})
            continue
        blocks = row.get("blocks") if isinstance(row.get("blocks"), dict) else {}
        dialogue = str(blocks.get("dialogue") or "").strip()
        state = str(blocks.get("state") or "").strip()
        variants["state_only"].append(
            {"role": "assistant", "content": state or "（上一轮没有状态）"}
        )
        variants["dialogue_only"].append(
            {"role": "assistant", "content": dialogue or "（这一轮没有台词）"}
        )
    return variants


def _judge(reply: str, earlier: str) -> dict[str, Any]:
    """判断这一轮有没有和前面的自己说的话矛盾。"""
    reask = [word for word in REASK if word in reply]
    denied = [word for word in DENY if word in earlier]
    offered = [word for word in OFFER if word in reply]
    contradiction = bool(denied) and bool(offered)
    return {
        "reask": reask,
        "denied_earlier": denied,
        "offered_now": offered,
        "contradiction": contradiction,
    }


def main() -> int:
    """重放最后一轮，比较两种历史形态。"""
    session = sys.argv[1] if len(sys.argv) > 1 else "s_demo"
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    repeat = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    rows = _rows(session)
    if len(rows) < turns:
        print("会话消息不足")
        return 1
    # 最后两行是当前这一轮的用户输入与角色回复，只重放其中的用户输入。
    # 历史取它之前的若干轮，不能把当前输入再放一遍。
    prompt = str(rows[-2].get("content", ""))
    history_rows = rows[-2 * turns : -2]

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
    system = [m.content for m in ctx.messages if m.role == "system"]

    variants = _history_variants(history_rows)
    earlier_text = " ".join(
        str(row.get("content", "")) for row in history_rows if row.get("role") == "assistant"
    )
    results: dict[str, Any] = {}
    with httpx.Client() as client:
        for name, history in variants.items():
            samples = []
            for index in range(repeat):
                messages = (
                    [{"role": "system", "content": text} for text in system]
                    + history
                    + [{"role": "user", "content": prompt}]
                )
                response = client.post(
                    INFERENCE + "/v1/chat/completions",
                    json={
                        "model": MODEL,
                        "messages": messages,
                        "stream": False,
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "max_tokens": 768,
                        "repetition_penalty": 1.1,
                        "seed": BASE_SEED + index,
                    },
                    timeout=300.0,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"].get("content") or ""
                verdict = _judge(content, earlier_text)
                samples.append(
                    {
                        "seed": BASE_SEED + index,
                        "reply": content,
                        "chars": len(content),
                        **verdict,
                    }
                )
                print(
                    "%-14s seed=%d 矛盾=%s 重问=%s :: %s"
                    % (
                        name,
                        BASE_SEED + index,
                        verdict["contradiction"],
                        verdict["reask"],
                        content.replace(chr(10), " / ")[:120],
                    ),
                    flush=True,
                )
            results[name] = {
                "prompt": prompt,
                "samples": samples,
                "contradiction_rounds": sum(1 for s in samples if s["contradiction"]),
                "reask_rounds": sum(1 for s in samples if s["reask"]),
                "avg_chars": round(sum(s["chars"] for s in samples) / len(samples), 1),
                "history": history,
            }

    out = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "context_replay.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"session": session, "repeat": repeat, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("written " + str(out))
    for name, res in results.items():
        print(
            "%-14s 矛盾=%d/%d 重问=%d/%d 均长=%.1f"
            % (
                name,
                res["contradiction_rounds"],
                repeat,
                res["reask_rounds"],
                repeat,
                res["avg_chars"],
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
