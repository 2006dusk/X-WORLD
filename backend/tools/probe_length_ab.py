r"""长度闸门多变量消融探针：自然输出长度分布 + 分组回合实测。

背景（要验证的三条假设）：
  H1 后端长度闸门的重写救不回短回合 —— backend/app/routers/chat.py 的
     _retry_params 只换 seed（RETRY_SEED_BASE + attempt）与温度
     （0.7 -> LENGTH_RETRY_TEMPERATURE 0.75），同量级参数下输出长度分布不变，
     「不足 min_chars 就丢弃重写」的达标概率约等于自然重采样的达标概率，
     代价是耗时接近翻倍、客户端先收到 replace 闪一下。
  H2 模型自然输出的正文字数集中在 100 到 170 字，单次不足 150 的概率不低。
  H3 max_tokens=768 不是瓶颈（finish_reason 基本不是 length）。

子命令：
  make-presets  复制内置预设成 min_chars=120 / 100 两份消融预设，走后端
                 POST /v1/presets/，id 为 p_ab_120 / p_ab_100（不碰 backend/app）。
  direct        绕过后端闸门，直连 http://127.0.0.1:8080/v1/chat/completions
                 连续采样，统计每次正文字数（场景块 + 对白块）与 150 字达标率。
                 --variant backend     与后端逐字一致的提示，经
                                        app.assembly.pipeline.assemble 装配
                                        （system = BUILTIN_SYSTEM_PROMPT + 角色卡段，
                                        user = 输入 + _output_directive(
                                        render_output_spec(BUILTIN_OUTPUT_SPEC))
                                        + _state_hint）。
                 --variant system_spec 按任务口径手工拼：system =
                                        BUILTIN_SYSTEM_PROMPT +
                                        render_output_spec(BUILTIN_OUTPUT_SPEC)，
                                        user = 输入 + _output_directive + _state_hint。
  turns         走真实后端连跑 6 轮（与 probe_scene_continuity 同一组动作），
                 统计逐轮正文字数、replace 触发（按 reason 分类）与耗时。

正文字数口径与闸门一致：split_blocks 拆块后场景块 + 对白块字符数相加，
状态块是元数据不计入（同 routers/chat.py 的 _body_chars）。

用法（cmd.exe，本机解释器 python）：
    python backend\tools\probe_length_ab.py make-presets
    python backend\tools\probe_length_ab.py direct --variant backend --samples 12
    python backend\tools\probe_length_ab.py direct --variant system_spec --samples 6
    set XW_PRESET=p_ab_120& python backend\tools\probe_length_ab.py turns --preset p_ab_120 --session s_ab_120
结果默认只打印 JSON 到标准输出；设了 XW_OUT（文件名）才落盘到
docs/实践情况/data/sweep/ 下。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import statistics
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import presets as preset_service  # noqa: E402
from app import store  # noqa: E402
from app.assembly import pipeline as pipeline_mod  # noqa: E402
from app.assembly.pipeline import assemble  # noqa: E402
from app.contracts import ChatRequest, XwOptions  # noqa: E402
from app.inference.structure import split_blocks  # noqa: E402

import probe_scene_continuity as psc  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
INFER = os.environ.get("XW_INFER", "http://127.0.0.1:8080")
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
WORLD_BOOKS = ["w_demo"]
STRUCTURE = ["scene", "dialogue", "state"]
BASELINE_PRESET = "p_builtin_rp"
DEFAULT_PROMPT = psc.TURNS[0]

# 与 backend/app/routers/chat.py 的 _build_params 一致；top_k/min_p 来自
# profile p_default（_build_payload 会把这两项并进请求体）。
SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 768,
    "repeat_penalty": 1.1,
    "top_k": 40,
    "min_p": 0.05,
}

# 消融预设：只改 output_spec.min_chars，其余字段照抄内置预设。
AB_PRESETS = {"p_ab_120": 120, "p_ab_100": 100}

# 达标线：闸门看的是 output_spec.min_chars，报告口径统一按 150 字。
FLOOR = 150


def _xw(preset_id: str) -> XwOptions:
    """装配选项，与 probe_scene_continuity 的请求参数对齐。"""
    return XwOptions(
        character_id=CHARACTER,
        preset_id=preset_id,
        world_book_ids=list(WORLD_BOOKS),
        memory_mode="hybrid",
        max_context_tokens=8192,
    )


def _system_text(preset: dict) -> str:
    """预设的 system 提示组拼接，口径同 pipeline._build_system。"""
    groups = preset.get("prompts") or []
    return "\n".join(
        str(item.get("content", ""))
        for item in groups
        if item.get("role") == "system" and str(item.get("content", "")).strip()
    )


def build_messages(
    variant: str, preset_id: str, user_text: str
) -> tuple[list[dict], dict]:
    """构造直连采样用的消息序列，返回 (messages, 构造说明)。

    backend     —— 走生产装配管线，提示与后端闸门看到的逐字一致；
                   system = BUILTIN_SYSTEM_PROMPT(+角色卡等段)，回合要求由
                   render_output_spec(BUILTIN_OUTPUT_SPEC) 经 _output_directive
                   附在 user 段末尾，再接 _state_hint。
    system_spec —— 任务口径手工拼：system = BUILTIN_SYSTEM_PROMPT +
                   render_output_spec(BUILTIN_OUTPUT_SPEC)，user = 输入 +
                   _output_directive + _state_hint（回合要求因此出现两次）。
    """
    xw = _xw(preset_id)
    preset = preset_service.get_preset(preset_id) or {}
    spec = preset.get("output_spec") if isinstance(preset.get("output_spec"), dict) else {}
    directive = pipeline_mod._output_directive(xw)
    hint = pipeline_mod._state_hint(xw)
    pieces = {
        "variant": variant,
        "preset_id": preset_id,
        "min_chars": int(spec.get("min_chars") or 0),
        "directive_chars": len(directive),
        "state_hint_chars": len(hint),
        "directive_head": directive[:60],
        "state_hint_head": hint[:40],
    }

    if variant == "system_spec":
        system = _system_text(preset)
        spec_text = preset_service.render_output_spec(spec)
        if spec_text:
            system = system + "\n" + spec_text
        user = user_text
        if directive:
            user = user + "\n\n" + directive
        if hint:
            user = user + "\n\n" + hint
        pieces["system_chars"] = len(system)
        pieces["user_chars"] = len(user)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}], pieces

    request = ChatRequest(
        model="xw-local-default",
        messages=[{"role": "user", "content": user_text}],
        session=None,
        stream=True,
        xw=xw,
    )
    ctx = assemble(request)
    messages = [{"role": m.role, "content": m.content} for m in ctx.messages]
    pieces["system_chars"] = len(messages[0]["content"]) if messages else 0
    pieces["user_chars"] = len(messages[-1]["content"]) if messages else 0
    pieces["segments"] = [seg.name for seg in ctx.report.segments]
    return messages, pieces


def _body_stats(text: str) -> dict:
    """按闸门口径拆块并算正文字数。"""
    blocks = split_blocks(text, STRUCTURE)
    scene = str(blocks.get("scene") or "")
    dialogue = str(blocks.get("dialogue") or "")
    state = str(blocks.get("state") or "")
    return {
        "scene_chars": len(scene),
        "dialogue_chars": len(dialogue),
        "state_chars": len(state),
        "body_chars": len(scene) + len(dialogue),
        "full_chars": len(text),
    }


def _stats(values: list[int]) -> dict:
    """一组正文字数的描述统计。"""
    if not values:
        return {}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "stdev": round(statistics.pstdev(values), 1),
        "ge150": sum(1 for v in values if v >= FLOOR),
        "ge150_ratio": round(sum(1 for v in values if v >= FLOOR) / len(values), 3),
        "lt150": sum(1 for v in values if v < FLOOR),
        "in_band_150_300": sum(1 for v in values if 150 <= v <= 300),
    }


def _maybe_write(payload: dict) -> None:
    """设了 XW_OUT 才落盘，默认只打印，避免在仓库里多建文件。"""
    name = os.environ.get("XW_OUT", "").strip()
    if not name:
        return
    out = os.path.join(ROOT, "..", "docs", "实践情况", "data", "sweep", name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print("written " + out)


# --------------------------------------------------------------------------- #
# make-presets：复制内置预设成消融预设
# --------------------------------------------------------------------------- #

def cmd_make_presets(args: argparse.Namespace) -> int:
    """读 p_builtin_rp，改 output_spec.min_chars，走后端 API 存成新 id。"""
    results = []
    with httpx.Client(timeout=60.0) as client:
        response = client.get(f"{BASE}/v1/presets/{BASELINE_PRESET}")
        response.raise_for_status()
        base = response.json()
        for preset_id, min_chars in AB_PRESETS.items():
            payload = copy.deepcopy(base)
            payload["id"] = preset_id
            payload["name"] = f"长度消融 min_chars={min_chars}"
            payload["builtin"] = False
            payload.pop("builtin_version", None)
            payload.setdefault("output_spec", {})["min_chars"] = min_chars
            post = client.post(f"{BASE}/v1/presets/", json=payload)
            post.raise_for_status()
            check = client.get(f"{BASE}/v1/presets/{preset_id}")
            check.raise_for_status()
            saved = check.json()
            row = {
                "id": saved.get("id"),
                "name": saved.get("name"),
                "min_chars": (saved.get("output_spec") or {}).get("min_chars"),
                "max_chars": (saved.get("output_spec") or {}).get("max_chars"),
                "structure": (saved.get("output_spec") or {}).get("structure"),
                "ok": (saved.get("output_spec") or {}).get("min_chars") == min_chars,
            }
            results.append(row)
            print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({"presets": results}, ensure_ascii=False))
    _maybe_write({"presets": results})
    return 0


# --------------------------------------------------------------------------- #
# direct：绕过闸门直连推理服务采样
# --------------------------------------------------------------------------- #

def cmd_direct(args: argparse.Namespace) -> int:
    """同一提示连续采样 N 次，统计正文字数分布与 150 字达标率。"""
    preset_id = args.preset
    prompt = args.prompt
    messages, pieces = build_messages(args.variant, preset_id, prompt)
    rows: list[dict] = []
    started = time.time()
    with httpx.Client(timeout=300.0) as client:
        for index in range(args.samples):
            payload = dict(SAMPLING)
            payload["messages"] = messages
            payload["stream"] = False
            tick = time.time()
            response = client.post(INFER + "/v1/chat/completions", json=payload)
            elapsed = time.time() - tick
            if response.status_code != 200:
                raise RuntimeError(
                    f"inference {response.status_code}: {response.text[:200]}"
                )
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            text = str(choice.get("message", {}).get("content") or "")
            usage = data.get("usage") or {}
            row = {
                "sample": index + 1,
                **_body_stats(text),
                "ge150": 0,
                "finish_reason": str(choice.get("finish_reason") or ""),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "elapsed": round(elapsed, 2),
                "head": text[:60].replace("\n", " "),
            }
            row["ge150"] = int(row["body_chars"] >= FLOOR)
            rows.append(row)
            print(
                "S%02d 正文字数=%d 场景=%d 对白=%d 状态=%d 全文=%d >=150=%s "
                "finish=%s tokens=%d %.1fs"
                % (
                    row["sample"],
                    row["body_chars"],
                    row["scene_chars"],
                    row["dialogue_chars"],
                    row["state_chars"],
                    row["full_chars"],
                    bool(row["ge150"]),
                    row["finish_reason"],
                    row["completion_tokens"],
                    row["elapsed"],
                )
            )

    summary = _stats([row["body_chars"] for row in rows])
    summary.update(
        {
            "finish_length": sum(
                1 for row in rows if row["finish_reason"] == "length"
            ),
            "max_completion_tokens": max(
                (row["completion_tokens"] for row in rows), default=0
            ),
            "max_full_chars": max((row["full_chars"] for row in rows), default=0),
            "elapsed_avg": round(
                sum(row["elapsed"] for row in rows) / max(len(rows), 1), 2
            ),
            "wall_s": round(time.time() - started, 1),
        }
    )
    payload = {
        "mode": "direct",
        "variant": args.variant,
        "preset_id": preset_id,
        "prompt": prompt,
        "sampling": SAMPLING,
        "prompt_pieces": pieces,
        "rows": rows,
        "summary": summary,
    }
    print(json.dumps(summary, ensure_ascii=False))
    _maybe_write(payload)
    return 0


# --------------------------------------------------------------------------- #
# turns：走真实后端连跑 6 轮
# --------------------------------------------------------------------------- #

def run_turn(client: httpx.Client, prompt: str, session: str, preset: str) -> dict:
    """跑一轮流式生成，按事件语义累计块内容，并记下 replace 的 reason。

    与 probe_scene_continuity.run_turn 同口径，另收 replace 原因
    （length / repetition / structure），用于区分闸门类型。
    """
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": CHARACTER,
            "preset_id": preset,
            "world_book_ids": list(WORLD_BOOKS),
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    blocks: dict = {}
    final_blocks: dict = {}
    events: list[str] = []
    replace_reasons: list[str] = []
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
            if name == "replace":
                replace_reasons.append(str(payload.get("reason") or "?"))
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
        "replace_reasons": replace_reasons,
        "elapsed": time.time() - started,
    }


def _fresh_session(session: str) -> str:
    """会话已有历史时自动换 id，保证各组起点干净。"""
    existing = store.get_messages(session, CHARACTER)
    if not existing:
        return session
    for index in range(2, 50):
        candidate = f"{session}_r{index}"
        if not store.get_messages(candidate, CHARACTER):
            print(f"[warn] 会话 {session} 已有 {len(existing)} 条历史，改用 {candidate}")
            return candidate
    raise RuntimeError("没有可用的空会话 id")


def cmd_turns(args: argparse.Namespace) -> int:
    """走后端连跑 6 轮，统计逐轮字数、replace 次数与耗时。"""
    preset = args.preset
    session = _fresh_session(args.session)
    rows: list[dict] = []
    with httpx.Client(timeout=240.0) as client:
        for index, prompt in enumerate(psc.TURNS):
            turn = run_turn(client, prompt, session, preset)
            stats = _body_stats("")
            blocks = turn["blocks"]
            scene = str(blocks.get("scene") or "")
            dialogue = str(blocks.get("dialogue") or "")
            state = str(blocks.get("state") or "")
            stats = {
                "scene_chars": len(scene),
                "dialogue_chars": len(dialogue),
                "state_chars": len(state),
                "body_chars": len(scene) + len(dialogue),
                "full_chars": len(scene) + len(dialogue) + len(state),
            }
            reasons = turn["replace_reasons"]
            row = {
                "turn": index + 1,
                "prompt": prompt,
                **stats,
                "ge150": int(stats["body_chars"] >= FLOOR),
                "in_band": 150 <= stats["body_chars"] <= 300,
                "replace_count": len(reasons),
                "replace_reasons": reasons,
                "elapsed": round(turn["elapsed"], 2),
            }
            rows.append(row)
            print(
                "T%d 正文字数=%d 场景=%d 对白=%d 状态=%d >=150=%s replace=%d%s %.1fs"
                % (
                    row["turn"],
                    row["body_chars"],
                    row["scene_chars"],
                    row["dialogue_chars"],
                    row["state_chars"],
                    bool(row["ge150"]),
                    row["replace_count"],
                    ("(" + ",".join(reasons) + ")") if reasons else "",
                    row["elapsed"],
                )
            )

    values = [row["body_chars"] for row in rows]
    reason_counter: dict[str, int] = {}
    for row in rows:
        for reason in row["replace_reasons"]:
            reason_counter[reason] = reason_counter.get(reason, 0) + 1
    summary = _stats(values)
    summary.update(
        {
            "preset_id": preset,
            "session": session,
            "min_chars": _min_chars(preset),
            "replace_total": sum(row["replace_count"] for row in rows),
            "replace_by_reason": reason_counter,
            "turns_with_replace": sum(1 for row in rows if row["replace_count"]),
            "elapsed_avg": round(sum(row["elapsed"] for row in rows) / max(len(rows), 1), 2),
            "elapsed_total": round(sum(row["elapsed"] for row in rows), 2),
            "ge150_after_gate": sum(row["ge150"] for row in rows),
        }
    )
    payload = {"mode": "turns", "rows": rows, "summary": summary}
    print(json.dumps(summary, ensure_ascii=False))
    _maybe_write(payload)
    return 0


def _min_chars(preset_id: str) -> int:
    """预设声明的字数下限。"""
    try:
        return int((preset_service.get_preset(preset_id) or {}).get("output_spec", {}).get("min_chars") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="长度闸门多变量消融探针")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_make = sub.add_parser("make-presets", help="复制内置预设成 min_chars=120/100")
    p_make.set_defaults(func=cmd_make_presets)

    p_direct = sub.add_parser("direct", help="直连推理服务采样，绕过长度闸门")
    p_direct.add_argument("--variant", choices=["backend", "system_spec"], default="backend")
    p_direct.add_argument("--samples", type=int, default=int(os.environ.get("XW_SAMPLES", "12")))
    p_direct.add_argument("--preset", default=os.environ.get("XW_PRESET", BASELINE_PRESET))
    p_direct.add_argument("--prompt", default=DEFAULT_PROMPT)
    p_direct.set_defaults(func=cmd_direct)

    p_turns = sub.add_parser("turns", help="走真实后端连跑 6 轮")
    p_turns.add_argument("--preset", default=os.environ.get("XW_PRESET", BASELINE_PRESET))
    p_turns.add_argument("--session", default=os.environ.get("XW_SESSION", "s_ab_base"))
    p_turns.set_defaults(func=cmd_turns)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
