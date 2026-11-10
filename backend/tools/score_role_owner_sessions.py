r"""用同一个角色归属闸门函数，给各轮探针会话重新打分。

闸门口径变了以后，早先跑出来的会话要用同一套判据重算，
否则前后对照不可比。只读会话文件，不生成、不改状态。

用法：
    python backend\tools\score_role_owner_sessions.py

输出：
    docs\实践情况\data\sweep\role_owner_score.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import store  # noqa: E402
from app.inference.structure import block_names, split_blocks  # noqa: E402
from app.routers.chat import _role_owner_violation  # noqa: E402

STRUCTURE = ["scene", "dialogue", "state"]
PREFIX = "s_role_owner"

# 漂移词：角色卡里的地址U调音师不会做咖啡、不会当吧台。
DRIFT_WORDS = ("厨房", "咖啡", "牛奶", "吧台", "店员", "老板", "顾客", "奶泡", "咖啡豆")
# 角色卡场景词：场景写在这里面才算落在卡片的地方。
PLACE_WORDS = ("地址U", "控制台", "台灯", "地址N", "巷子", "录音设备", "耳机")

# 每条输入的相关词：对白里出现就算接住了这一轮。判定很粗，只算下界。
UPTAKE_WORDS = {
    "吻上去": ("吻", "抱", "靠近"),
    "抱住他": ("抱", "推开", "闪", "躲"),
    "推开门走进去": ("门", "进", "进来", "进屋"),
    "把磁带递给他": ("磁带", "带子"),
    "你还记得我吗": ("记得", "忘"),
    "把灯关掉": ("灯", "关", "黑"),
}


def _blocks(row: dict) -> dict[str, str]:
    """取一条消息的场景、对白、状态。"""
    blocks = row.get("blocks")
    if isinstance(blocks, dict):
        return {
            "scene": str(blocks.get("scene", "") or ""),
            "dialogue": str(blocks.get("dialogue", "") or ""),
        }
    parsed = split_blocks(str(row.get("content", "")), block_names(STRUCTURE))
    return {
        "scene": str(parsed.get("scene", "") or ""),
        "dialogue": str(parsed.get("dialogue", "") or ""),
    }


def main() -> int:
    """打分并写对照表。"""
    sessions = [
        str(item.get("id", ""))
        for item in store.list_sessions()
        if str(item.get("id", "")).startswith(PREFIX)
    ]
    sessions.sort()

    rows: list[dict] = []
    for session in sessions:
        path_rows = store.session_path(session, "c_demo", None)
        assistant = [r for r in path_rows if r.get("role") == "assistant"]
        if not assistant:
            continue
        last = assistant[-1]
        blocks = _blocks(last)
        kind = _role_owner_violation(blocks, STRUCTURE, "角色K")
        user_rows = [r for r in path_rows if r.get("role") == "user"]
        prompt = str(user_rows[-1].get("content", "")) if user_rows else ""
        scene = blocks["scene"]
        rows.append(
            {
                "session": session,
                "input": prompt,
                "violation": kind,
                "scene": scene,
                "dialogue": blocks["dialogue"],
                "drift": [word for word in DRIFT_WORDS if word in scene],
                "place_ok": any(word in scene for word in PLACE_WORDS),
                "uptake": any(
                    word in blocks["dialogue"]
                    for word in UPTAKE_WORDS.get(prompt, ())
                ),
            }
        )

    lines: list[str] = ["| 会话 | 输入 | 判定 |", "|---|---|---|"]
    for row in rows:
        lines.append(
            "| %s | %s | %s |" % (row["session"], row["input"], row["violation"] or "ok")
        )
    lines.append("")

    groups: dict[str, list[dict]] = {}
    for row in rows:
        tag = row["session"].replace(PREFIX, "").strip("_").rsplit("_", 1)[0]
        groups.setdefault(tag or "baseline", []).append(row)
    for tag, items in sorted(groups.items()):
        bad = [r for r in items if r["violation"]]
        drift = [r for r in items if r["drift"]]
        place = [r for r in items if r["place_ok"]]
        uptake = [r for r in items if r["uptake"]]
        lines.append(
            "- %s：轮次 %d，越位 %d（%.3f），漂移场景 %d（%.3f），"
            "场景落在卡片地点 %d（%.3f），对白接住关键词 %d（%.3f）"
            % (
                tag,
                len(items),
                len(bad),
                len(bad) / max(len(items), 1),
                len(drift),
                len(drift) / max(len(items), 1),
                len(place),
                len(place) / max(len(items), 1),
                len(uptake),
                len(uptake) / max(len(items), 1),
            )
        )
        for row in items:
            lines.append("  - [%s] %s" % (row["input"], row["violation"] or "ok"))
            lines.append("    scene: " + row["scene"][:120])
            lines.append("    dlg:   " + row["dialogue"][:160])

    # 旧会话路径：用户实际玩过的那条线，按同一判据打分。
    legacy_rows = store.session_path("s_demo", "c_demo", None)
    legacy = []
    for row in legacy_rows:
        if str(row.get("role", "")) != "assistant":
            continue
        blocks = _blocks(row)
        legacy.append(
            {
                "violation": _role_owner_violation(blocks, STRUCTURE, "角色K"),
                "scene": blocks["scene"],
                "dialogue": blocks["dialogue"],
            }
        )
    if legacy:
        bad_legacy = [row for row in legacy if row["violation"]]
        lines.append("")
        lines.append("## 旧会话 s_demo 当前路径")
        lines.append(
            "- 角色回复 %d 条，越位 %d 条（%.3f）"
            % (len(legacy), len(bad_legacy), len(bad_legacy) / max(len(legacy), 1))
        )
        counter_legacy: dict[str, int] = {}
        for row in bad_legacy:
            counter_legacy[row["violation"]] = counter_legacy.get(row["violation"], 0) + 1
        for name, count in sorted(counter_legacy.items(), key=lambda kv: -kv[1]):
            lines.append("  - %s: %d" % (name, count))
        for row in bad_legacy:
            lines.append("    * " + row["violation"] + " | " + row["scene"][:80])

    out = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "role_owner_score.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"scored={len(rows)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
