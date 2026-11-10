r"""逻辑一致性探针。

质量裁判看的是单轮形态，这里补一条逻辑轴：多轮推进中事实是否自洽、
是否回应用户动作、有没有把已确立的设定推翻。

两层检查：
1. 确定性规则：用户每轮给出的关键事实，模型在后续轮次里是否与之矛盾；
   用户动作关键词是否被回应；本轮是否复述上一轮。
2. 本地裁判打分：逻辑连贯、设定一致、因果回应三项各 0 到 10 分。

已有四轮对话直接复用 multi_turn.json，缺哪一档就现场跑一遍。

用法：
    python backend\tools\probe_logic.py [配置名子串 ...]
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

from bench_local import free_port  # noqa: E402
from multi_turn import (  # noqa: E402
    JUDGE_ARGS,
    JUDGE_MODEL,
    OUT_DIR,
    start_server,
    stop_server,
    wait_ready,
)
from sweep_models import SWEEP_CASES  # noqa: E402

MULTI_TURN_FILE = OUT_DIR / "multi_turn.json"
OUT_FILE = OUT_DIR / "logic.json"

# 用户每轮给的关键事实：后续轮次不得与之矛盾
CONTRADICTIONS = [
    (3, r"没有妹妹|没有姐姐|我妹妹是谁|不可能是你妹妹|你认错人了",
     "推翻第 3 轮确立的妹妹设定"),
    (2, r"这盘磁带里没有人声|磁带里没有人声|这盘磁带是空白的|磁带是空白的|什么声音都没有",
     "推翻第 2 轮确立的磁带有人声"),
    (4, r"只有一个声音|没有第二个声音|并没有第二个声音|听错了", "推翻第 4 轮确立的第二个声音"),
]

# 每轮用户动作，回复里应当有回应
EXPECT_KEYWORDS = {
    1: ["磁带", "调音台", "控制台", "控制室"],
    2: ["停止", "键", "按钮", "手指", "停"],
    3: ["便签", "字", "笔迹", "名字", "妹妹"],
    4: ["名字", "声音", "妹妹", "女孩", "女人"],
}

LOGIC_RUBRIC = """你是中文角色扮演的逻辑评审。下面给你一段四轮角色扮演对话的完整记录。

只判断逻辑，不要评价文采和文风。
- coherence：前后因果是否连贯，后一轮是否接得住前一轮的因果链，0 到 10 分
- consistency：是否与已经确立的设定和事实一致（人物关系、时间、物件、磁带里的声音），0 到 10 分
- response：是否回应了用户每一轮给出的具体动作与信息，而不是各说各话，0 到 10 分

如果出现明确的事实矛盾（例如用户说妹妹三年前失踪，回复却说她昨天才失踪），
在 contradictions 里写出来；没有就写空数组。

只输出 JSON，不要解释：
{"coherence": 0, "consistency": 0, "response": 0, "contradictions": ["一句话"], "reason": "一句话"}"""

JSON_RE = re.compile(r"\{.*\}", re.S)


def deterministic_checks(turns: list[dict]) -> dict:
    contradictions: list[str] = []
    responded: list[bool] = []
    for turn in turns:
        index = int(turn.get("turn") or 0)
        text = str(turn.get("content") or "")
        for since, pattern, label in CONTRADICTIONS:
            if index >= since and re.search(pattern, text):
                contradictions.append(f"第{index}轮：{label}")
        keys = EXPECT_KEYWORDS.get(index) or []
        responded.append(bool(keys) and any(k in text for k in keys))
    overlaps = [float((t.get("metrics") or {}).get("overlap_history") or 0) for t in turns]
    return {
        "contradictions": contradictions,
        "responded": responded,
        "response_rate": round(sum(responded) / len(responded), 3) if responded else 0.0,
        "max_overlap": max(overlaps) if overlaps else 0.0,
        "verbatim_repeat": any(o >= 0.9 for o in overlaps),
    }


def judge_logic(base: str, turns: list[dict]) -> dict:
    transcript = "\n".join(
        f"第{t['turn']}轮 用户：{t['user']}\n第{t['turn']}轮 回复：{t['content']}"
        for t in turns
    )
    resp = httpx.post(
        f"{base}/v1/chat/completions",
        json={
            "model": "local",
            "messages": [
                {"role": "system", "content": LOGIC_RUBRIC},
                {"role": "user", "content": transcript},
            ],
            "max_tokens": 500,
            "temperature": 0.0,
            "seed": 42,
            "response_format": {"type": "json_object"},
        },
        timeout=1800.0,
    )
    resp.raise_for_status()
    body = resp.json()
    text = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    match = JSON_RE.search(text)
    if not match:
        return {"error": "裁判输出无法解析", "raw": text[:200]}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"error": "裁判输出解析失败", "raw": text[:200]}
    out = {}
    for key in ("coherence", "consistency", "response"):
        try:
            out[key] = float(data.get(key) or 0)
        except (TypeError, ValueError):
            out[key] = 0.0
    out["contradictions"] = [str(x) for x in (data.get("contradictions") or [])][:6]
    out["reason"] = str(data.get("reason") or "")[:200]
    return out


def load_existing() -> dict[str, dict]:
    if not MULTI_TURN_FILE.exists():
        return {}
    data = json.loads(MULTI_TURN_FILE.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else list(data.values())
    return {str(r.get("name")): r for r in rows if isinstance(r, dict) and r.get("turns")}


def rescore() -> int:
    """只重算确定性部分，裁判分沿用上次结果。"""
    if not OUT_FILE.exists():
        print('缺少 logic.json，先跑一次探针')
        return 1
    rows = json.loads(OUT_FILE.read_text(encoding='utf-8'))
    for row in rows:
        turns = row.get('turns') or []
        if not turns:
            continue
        row['deterministic'] = deterministic_checks(turns)
        det = row['deterministic']
        scored = row.get('judge') or {}
        logic_total = sum(float(scored.get(k) or 0) for k in ("coherence", "consistency", "response"))
        print(
            f"    {row.get('name')}: 逻辑 {logic_total:.0f}/30 | 回应率 {det['response_rate']} | "
            f"矛盾 {det['contradictions'] or '无'} | 复述 {det['verbatim_repeat']}",
            flush=True,
        )
    OUT_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n已复算 {OUT_FILE}')
    return 0


def main() -> int:
    if '--rescore' in sys.argv[1:]:
        return rescore()
    wanted = [a.lower() for a in sys.argv[1:] if not a.startswith('--')]
    cases = [
        case for case in SWEEP_CASES
        if not wanted or any(token in str(case["name"]).lower() for token in wanted)
    ]
    if not cases:
        print("没有匹配的档位")
        return 1

    existing = load_existing()
    records: list[dict] = []
    for index, case in enumerate(cases, 1):
        name = str(case["name"])
        if name in existing:
            print(f"[{index}/{len(cases)}] {name} 复用已有四轮记录", flush=True)
            records.append(existing[name])
            continue
        print(f"[{index}/{len(cases)}] {name} 现跑四轮", flush=True)
        from multi_turn import play_turns  # 延迟导入，避免加载即占显存
        record = play_turns(case)
        records.append(record)
        if "error" in record:
            print(f"    失败：{record['error']}", flush=True)

    print("== 逻辑裁判 ==", flush=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc, log_path, handle = start_server(JUDGE_MODEL, JUDGE_ARGS, port)
    results = []
    try:
        if wait_ready(base, proc):
            time.sleep(1.0)
            for record in records:
                turns = record.get("turns") or []
                if not turns:
                    continue
                det = deterministic_checks(turns)
                scored = judge_logic(base, turns)
                results.append({
                    "name": record.get("name"),
                    "params": record.get("params"),
                    "quant": record.get("quant"),
                    "deterministic": det,
                    "judge": scored,
                    "turns": turns,
                })
                logic_total = sum(float(scored.get(k) or 0) for k in ("coherence", "consistency", "response"))
                print(
                    f"    {record.get('name')}: 逻辑 {logic_total:.0f}/30 | 回应率 {det['response_rate']} | "
                    f"矛盾 {det['contradictions'] or '无'} | 复述 {det['verbatim_repeat']}",
                    flush=True,
                )
        else:
            print("裁判服务启动超时", flush=True)
    finally:
        stop_server(proc, log_path, handle)

    OUT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {OUT_FILE}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
