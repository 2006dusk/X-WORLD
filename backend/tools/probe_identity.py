"""身份识别探针：角色有没有被认成别人。

角色A这张卡里有两个身份在打架：她是皇后，「坐在那把椅子上的皇帝」是她丈夫，
「你」是她挑中的人。模型写她的自称时如果滑到「朕」，就是认成了皇帝；
叙述里把「她」写成「他」也是同一个毛病。

要查的是两件事，全部按生产链路取证（后端 HTTP 接口、装配、落盘）：

1. 台词里的自称对不对：她自称「我」，不该出现「朕 / 寡人 / 孤」。
2. 叙述里角色的性别代词对不对：动作块写的是她，不该用「他」指代她。

做法：把角色卡复制成临时角色，复制它的世界状态，建临时会话，逐轮发用户输入。
全程只动自己造的东西，收尾删干净。

跑法：
  F:/python/python.exe backend/tools/probe_identity.py run
  F:/python/python.exe backend/tools/probe_identity.py cleanup

只打印到终端，不落新的持久化数据表。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402

WEB = os.environ.get("XWORLD_WEB", "http://127.0.0.1:8760")
PRESET = "p_builtin_rp"
TEMP_PREFIX = "TMP_IDENT"
# 临时卡在 creator_notes 里留的标记。崩掉的那一轮不会走收尾，临时卡就留在
# 数据目录里；它的内部名字与真实卡一样，界面上分不出来，只能按标记找。
TEMP_MARKER = TEMP_PREFIX + " 临时探针卡，跑完自动清理"

# 皇帝的自称。皇后说「朕」就是认成了皇帝。
EMPEROR_SELF = ("朕", "寡人", "朕家", "孤家")
# 后宫位份的女子自称。
COURT_SELF = ("本宫", "本后")
# 角色自己的第一人称。
PLAIN_SELF = ("我",)

TURNS = [
    "（她把手里的茶盏搁在榻沿，指尖敲了敲床柱）",
    "（她从榻边取过那副带锁链的项圈，垂眼看你）",
    "我没有说话，只是看着她。",
    "（殿门外的两名宫女推门进来，把我按在榻上）",
    "（她亲手把那只项圈扣回我的颈上，锁扣咔哒一声合上）",
    "（她松开手，我才发现自己一直跪着，膝盖早就麻了）",
]


def seed() -> dict[str, str]:
    """造临时角色卡与临时会话，并复制原角色的世界状态。"""
    source = store.get_card("c_card_01")
    if not isinstance(source, dict):
        return {"error": "card_not_found"}
    payload = dict(source)
    data = payload.get("data")
    payload["data"] = dict(data) if isinstance(data, dict) else {}
    payload["name"] = TEMP_PREFIX + "_角色A"
    payload.pop("id", None)
    # 只标 creator_notes，不动内部 name 与 identity：名字和身份要保持真实，
    # 否则测的就不是这张卡了。
    payload["data"]["creator_notes"] = TEMP_MARKER
    card = store.save_card(payload)
    card_id = str(card["id"])

    state = store.get_state("c_card_01")
    if isinstance(state, dict) and state:
        store.save_state(dict(state), card_id)

    session_id = "s_tmp_ident_" + card_id.replace("-", "")[-8:]
    store.ensure_session(session_id, card_id, PRESET)
    return {"card_id": card_id, "session_id": session_id}


def ask(
    card_id: str,
    session_id: str,
    user_input: str,
    attempts: int = 2,
) -> dict[str, Any]:
    """走真实 HTTP 接口生成一轮，拿回结构化块。

    失败时把后端返回的错误体打出来：只看到一个 502 没法判断是推理超时、
    连不上上游还是别的错，重试一次是因为后端由后台任务托管，重启瞬间会断。
    """
    body = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": user_input}],
        "session": session_id,
        "stream": False,
        "xw": {
            "character_id": card_id,
            "preset_id": PRESET,
            "world_book_ids": ["w_a6154bb9b4b8"],
            "region_id": "e_eaff8133bd29",
            "memory_mode": "hybrid",
            "max_context_tokens": 16384,
        },
    }
    payload = json.dumps(body).encode("utf-8")
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            WEB + "/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            print(json.dumps(
                {"turn_error": exc.code, "body": detail, "attempt": attempt},
                ensure_ascii=True,
            ))
            if exc.code == 502 and attempt < attempts:
                time.sleep(5)
                continue
            raise
        except urllib.error.URLError as exc:
            print(json.dumps(
                {"turn_error": "urlerror", "detail": repr(exc), "attempt": attempt},
                ensure_ascii=True,
            ))
            if attempt < attempts:
                time.sleep(5)
                continue
            raise
        message = (data.get("choices") or [{}])[0].get("message") or {}
        blocks = message.get("blocks") or {}
        return {
            "action": str(blocks.get("action") or ""),
            "dialogue": str(blocks.get("dialogue") or ""),
        }
    raise RuntimeError("不可达")


def run() -> dict[str, Any]:
    """跑六轮，统计自称与性别代词。"""
    ids = seed()
    if "error" in ids:
        return ids
    card_id = ids["card_id"]
    session_id = ids["session_id"]
    rows: list[dict[str, Any]] = []
    for index, user_input in enumerate(TURNS):
        started = time.perf_counter()
        result = ask(card_id, session_id, user_input)
        elapsed = round(time.perf_counter() - started, 1)
        action = result["action"]
        dialogue = result["dialogue"]
        rows.append({
            "turn": index + 1,
            "elapsed_s": elapsed,
            "action": action,
            "dialogue": dialogue,
            "emperor_self": [w for w in EMPEROR_SELF if w in dialogue],
            "court_self": [w for w in COURT_SELF if w in dialogue],
            "plain_self": [w for w in PLAIN_SELF if w in dialogue],
            "male_pronoun_in_action": ("他" in action),
        })
        print(json.dumps(rows[-1], ensure_ascii=True))

    summary = {
        "summary": True,
        "turns": len(rows),
        "elapsed_s": [r["elapsed_s"] for r in rows],
        "emperor_self_turns": sum(1 for r in rows if r["emperor_self"]),
        "court_self_turns": sum(1 for r in rows if r["court_self"]),
        "plain_self_turns": sum(1 for r in rows if r["plain_self"]),
        "male_pronoun_action_turns": sum(1 for r in rows if r["male_pronoun_in_action"]),
        "ids": ids,
    }
    print(json.dumps(summary, ensure_ascii=True))
    return summary


def sweep() -> dict[str, int]:
    """删掉所有带临时标记的角色卡，连同它们的会话与状态。

    崩掉的那一轮不会走收尾，临时卡就留在数据目录里。删卡会连带删掉这个角色
    名下的会话、记忆、实体与世界状态，不留孤儿聊天记录。
    """
    done = {"cards": 0}
    for item in store.list_cards():
        card_id = str(item.get("id") or "")
        if not card_id:
            continue
        card = store.get_card(card_id)
        if not isinstance(card, dict):
            continue
        data = card.get("data")
        notes = str(data.get("creator_notes", "")) if isinstance(data, dict) else ""
        if not notes.startswith(TEMP_PREFIX):
            continue
        if store.delete_card(card_id):
            done["cards"] += 1
    return done


def cleanup(ids: dict[str, str]) -> dict[str, int]:
    """删掉临时会话、临时角色卡与临时世界状态。"""
    done = {"session": 0, "card": 0, "state": 0}
    card_id = ids.get("card_id", "")
    session_id = ids.get("session_id", "")
    if not card_id:
        return done
    for scope in (card_id, store.GLOBAL_SCOPE):
        path = store.sessions_dir(scope) / (session_id + ".jsonl")
        if path.exists():
            path.unlink()
            done["session"] += 1
    state_file = store.state_path(card_id)
    if state_file.exists():
        state_file.unlink()
        done["state"] += 1
    if store.delete_card(card_id):
        done["card"] += 1
    done["swept"] = sweep()["cards"]
    return done


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="角色身份识别探针")
    parser.add_argument("action", choices=["run", "cleanup"])
    args = parser.parse_args()
    if args.action == "run":
        summary: dict[str, Any] = {}
        try:
            summary = run()
        finally:
            # 跑崩了也要收尾，否则临时卡与临时会话留在数据目录里
            print(json.dumps({"cleanup": cleanup(summary.get("ids", {}))}, ensure_ascii=True))
        return 0
    print(json.dumps({"cleanup": cleanup({})}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
