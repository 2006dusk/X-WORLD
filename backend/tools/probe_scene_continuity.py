r"""场景承接探针：量化「每轮重新开场」与重复动作。

走真实后端 /v1/chat/completions，连跑多轮带连续动作的输入，
统计：
- 场景是否承接上轮（出现上轮场景关键地点/物件）；
- 是否重复了已经做过的动作（走进地址V、推门而入这类）；
- 对白是否写了对方的回应（模型替对方说话）；
- 整轮逐字复读率；
- 字数带内率与 replace 触发次数。

用法：
    python backend\tools\probe_scene_continuity.py
输出：
    docs\实践情况\data\sweep\scene_continuity.json
"""

from __future__ import annotations

import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app.state import parse_state_block  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SESSION = os.environ.get("XW_SESSION", "s_scene_cont")
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")

# 连续动作：每一轮都该往前推，而不是重新走进同一个地方。
TURNS = [
    "外面雨还没停，你不去看看吗？",
    "我们去看看吧，顺便找个地方坐坐。",
    "前面有家地址V，我们进去吧。",
    "点单，看看喝什么。",
    "看菜单。",
    "你刚才说的那首老歌，叫什么名字？",
]

# 已经在地址V内部之后，不该再出现的动作。
# 只收「进入地址V」这一类明确的入场动作，避免误伤合理的环境描写与回忆。
REENTRY_MARKERS = [
    "走进地址V",
    "走向地址V",
    "推开地址V",
    "推开咖啡厅",
    "迈进地址V",
    "踏进地址V",
    "地址V的门铃",
    "又到了地址V",
    "再次来到地址V",
    "折返地址V",
]

# 对方（用户）的回应被模型代写的标志。
# 只收「代述对方动作」与「代写对方台词」的强信号，角色对用户说话（你说得对）
# 不算代对方说话，因此不放宽泛的「你说」。
USER_VOICE_MARKERS = [
    "她回应道",
    "他回应道",
    "她答道",
    "他答道",
    "她说道",
    "他说道",
    "你点点头",
    "你摇了摇头",
    "你笑了笑",
    "你沉默",
    "你转身",
    "你答应",
    "你心里",
]

# 场景复制：模型照抄上一轮场景文本。
# 用字符 n-gram 重合度衡量，阈值取 0.5：连续四轮几乎逐字相同时必然超过。
SCENE_COPY_NGRAM = 4
SCENE_COPY_THRESHOLD = 0.5


def _ngrams(text: str, size: int) -> set[str]:
    """字符 n-gram 集合。"""
    if len(text) < size:
        return set()
    return {text[index: index + size] for index in range(len(text) - size + 1)}


def _scene_copy_ratio(current: str, previous: str) -> float:
    """当前场景对上一轮场景的复制度。

    取双向 n-gram 包含率的较大值：只查单向会漏掉「上一轮是当前的前缀」
    这种整段照抄。
    """
    if not current or not previous:
        return 0.0
    cur = _ngrams(current, SCENE_COPY_NGRAM)
    prev = _ngrams(previous, SCENE_COPY_NGRAM)
    if not cur or not prev:
        return 0.0
    forward = len(cur & prev) / len(cur)
    backward = len(cur & prev) / len(prev)
    return max(forward, backward)


def run_turn(client, prompt: str) -> dict:
    """跑一轮流式生成，按事件语义累计块内容。"""
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": SESSION,
        "stream": True,
        "xw": {
            "character_id": CHARACTER,
            "preset_id": PRESET,
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    blocks: dict = {}
    final_blocks: dict = {}
    events: list[str] = []
    started = time.time()
    with client.stream("POST", BASE + "/v1/chat/completions", json=request) as stream:
        stream.raise_for_status()
        name = ""
        for line in stream.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
                events.append(name)
                continue
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if name == "blocks":
                final_blocks = payload.get("blocks") or {}
                continue
            content = payload.get("content")
            if not isinstance(content, str) or not content:
                continue
            block = payload.get("block")
            if block:
                blocks[block] = blocks.get(block, "") + content
    return {
        "blocks": final_blocks or blocks,
        "events": events,
        "elapsed": time.time() - started,
    }


def main() -> int:
    """连跑多轮并落盘。"""
    rows: list[dict] = []
    previous_text = ""
    previous_scene = ""
    with httpx.Client(timeout=240.0) as client:
        for index, prompt in enumerate(TURNS):
            turn = run_turn(client, prompt)
            blocks = turn["blocks"]
            scene = str(blocks.get("scene") or "")
            dialogue = str(blocks.get("dialogue") or "")
            state_raw = str(blocks.get("state") or "")
            parsed = parse_state_block(state_raw)
            body = (scene + chr(10) + dialogue).strip()
            chars = len(body)

            reentry = [m for m in REENTRY_MARKERS if m in scene]
            user_voice = [m for m in USER_VOICE_MARKERS if m in dialogue or m in scene]
            identical = bool(previous_text) and body == previous_text
            # 场景复制：模型照抄上一轮的场景文本。
            copy_ratio = _scene_copy_ratio(scene, previous_scene)
            scene_copied = copy_ratio >= SCENE_COPY_THRESHOLD
            # 承接：这一轮的场景里出现上一轮场景的地点或物件。
            carry = ""
            if previous_scene:
                for token in ("地址V", "地址U", "吧台", "菜单", "窗", "桌", "杯"):
                    if token in previous_scene and token in scene:
                        carry = token
                        break

            rows.append({
                "turn": index + 1,
                "prompt": prompt,
                "chars": chars,
                "in_band": 150 <= chars <= 300,
                "reentry_markers": reentry,
                "user_voice_markers": user_voice,
                "identical_to_previous": identical,
                "scene_copy_ratio": round(copy_ratio, 3),
                "scene_copied": scene_copied,
                "carry_token": carry,
                "replace_events": turn["events"].count("replace"),
                "elapsed": round(turn["elapsed"], 2),
                "state": parsed,
                "scene": scene,
                "dialogue": dialogue,
            })
            previous_text = body
            previous_scene = scene
            print(
                "T%d 字数=%d 带内=%s 重复动作=%s 代对方说话=%s 逐字相同=%s 场景复制=%.2f 承接=%s replace=%d %.2fs"
                % (
                    index + 1,
                    chars,
                    150 <= chars <= 300,
                    reentry or "-",
                    user_voice or "-",
                    identical,
                    copy_ratio,
                    carry or "-",
                    turn["events"].count("replace"),
                    turn["elapsed"],
                )
            )

    summary = {
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "identical": sum(1 for row in rows if row["identical_to_previous"]),
        "scene_copied": sum(1 for row in rows if row["scene_copied"]),
        "reentry_turns": sum(1 for row in rows if row["reentry_markers"]),
        "user_voice_turns": sum(1 for row in rows if row["user_voice_markers"]),
        "carry_turns": sum(1 for row in rows if row["carry_token"]),
        "replace_events": sum(row["replace_events"] for row in rows),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    print()
    print(json.dumps(summary, ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep",
        os.environ.get("XW_OUT", "scene_continuity.json"),
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rows": rows}, handle, ensure_ascii=False, indent=2)
    print("written " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
