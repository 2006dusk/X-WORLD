r"""真机探针：分支树的形状。

跑法：python backend/tools/probe_branch_tree.py

链路：
1. 建一个临时角色卡并开一段独立对话，连说三轮 -> 树是一条线，没有分叉。
2. 重新生成最后一轮 -> 节点数不变，节点 id 不变，树上仍没有分叉。
3. 改写第二轮的用户输入 -> 同一个父节点下多出一条线，树上多一处分叉。
4. 切回旧的那条线 -> 当前路径换回旧版本。

临时角色卡与探针会话在结束时删掉，不留在数据目录里。
数据层不落新的持久化表，结果写 JSON 文件。
输出写 UTF-8 文件，终端不回显中文。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 探针是独立脚本，要拿到数据目录就得自己把 backend 放进路径。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

BASE = "http://127.0.0.1:8760"
OUT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "实践情况"
    / "data"
    / "sweep"
    / "branch_tree_probe.json"
)

PRESET_ID = "p_builtin_rp"
CARD_NAME = "分支探针角色"
REGION_ID = "e_recording_studio"
BOOK_ID = "w_demo"


def req(method: str, path: str, payload: Any = None, timeout: int = 120) -> tuple[int, Any]:
    """发一个 JSON 请求，返回状态码与解析后的正文。"""
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def snapshot(session_id: str) -> dict[str, Any]:
    """取会话的路径与整棵分支树。"""
    query = urllib.parse.urlencode({"path": "1", "tree": "1"})
    status, data = req("GET", f"/v1/sessions/{session_id}/messages?{query}")
    return data if isinstance(data, dict) else {}


def forks(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """有两条以上子节点的节点，即分叉点。"""
    return [node for node in tree if len(node.get("children") or []) > 1]


def chat(
    session_id: str,
    character_id: str,
    text: str,
    action: str = "append",
    target_id: str = "",
) -> dict[str, Any]:
    """跑一轮生成。"""
    status, result = req(
        "POST",
        "/v1/chat/completions",
        {
            "model": "xw-local-default",
            "messages": [{"role": "user", "content": text}],
            "session": session_id,
            "stream": False,
            "xw": {
                "character_id": character_id,
                "preset_id": PRESET_ID,
                "world_book_ids": [BOOK_ID],
                "region_id": REGION_ID,
                "memory_mode": "hybrid",
                "max_context_tokens": 16384,
            },
            "turn_action": action,
            "target_id": target_id or None,
        },
        timeout=600,
    )
    return result if isinstance(result, dict) else {"status": status}


def main() -> int:
    """跑完整条链路并落盘结果。"""
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url": BASE,
        "checks": [],
    }

    def check(cid: str, name: str, ok: bool, detail: Any) -> None:
        """记一条断言。"""
        report["checks"].append({"id": cid, "name": name, "ok": bool(ok), "detail": detail})

    # 临时角色卡：一角色一条对话，用临时角色才有独立的探针会话
    status, card = req(
        "POST",
        "/v1/cards/",
        {
            "name": CARD_NAME,
            "spec_version": "v2",
            "data": {
                "name": CARD_NAME,
                "description": "探针用的临时角色，站在地址N地址U",
                "personality": "话少",
                "scenario": "地址N的地址U",
            },
        },
    )
    card_id = str(card.get("id") or "")
    status, session = req(
        "POST",
        "/v1/sessions/",
        {
            "character_id": card_id,
            "world_book_ids": [BOOK_ID],
            "region_id": REGION_ID,
        },
    )
    session_id = str(session.get("id") or "")
    report["card_id"] = card_id
    report["session_id"] = session_id

    # 1. 连说三轮
    for text in ("我来了。", "你还在忙吗？", "外面下雨了。"):
        chat(session_id, card_id, text)
    first = snapshot(session_id)
    check(
        "B1",
        "三轮之后树是一条线，没有分叉",
        len(forks(first.get("tree") or [])) == 0,
        {
            "nodes": len(first.get("tree") or []),
            "forks": len(forks(first.get("tree") or [])),
        },
    )
    items = first.get("items") or []
    check(
        "B2",
        "路径上有三轮问答",
        len(items) == 6,
        {"count": len(items)},
    )
    head_before = str(first.get("head_id") or "")
    last_reply_id = str(items[-1].get("id") or "") if items else ""

    # 2. 重新生成最后一轮
    chat(session_id, card_id, "外面下雨了。", action="regenerate")
    second = snapshot(session_id)
    check(
        "B3",
        "重新生成不新增节点，也不产生分叉",
        len(second.get("tree") or []) == len(first.get("tree") or [])
        and not forks(second.get("tree") or []),
        {
            "nodes_before": len(first.get("tree") or []),
            "nodes_after": len(second.get("tree") or []),
            "forks": len(forks(second.get("tree") or [])),
        },
    )
    check(
        "B4",
        "重新生成后 head 与节点 id 不变",
        str(second.get("head_id") or "") == head_before
        and str((second.get("items") or [{}])[-1].get("id") or "") == last_reply_id,
        {"before": head_before, "after": second.get("head_id")},
    )

    # 3. 改写第二轮的用户输入
    items = second.get("items") or []
    second_user = ""
    for index, node in enumerate(items):
        if node.get("role") == "user" and index == 2:
            second_user = str(node.get("id") or "")
    chat(
        session_id,
        card_id,
        "换个问法：你还在忙吗？",
        action="revise",
        target_id=second_user,
    )
    third = snapshot(session_id)
    fork_nodes = forks(third.get("tree") or [])
    check(
        "B5",
        "改写第二轮输入后树上多出一处分叉",
        len(fork_nodes) == 1,
        {"forks": len(fork_nodes), "fork_ids": [node.get("id") for node in fork_nodes]},
    )
    check(
        "B6",
        "分叉点下有两条线：旧输入与新输入",
        len(fork_nodes[0].get("children") or []) == 2 if fork_nodes else False,
        {"children": fork_nodes[0].get("children") if fork_nodes else []},
    )

    # 4. 切回旧的那条线
    old_line = ""
    if fork_nodes:
        kids = fork_nodes[0].get("children") or []
        by_id = {node.get("id"): node for node in third.get("tree") or []}
        for kid in kids:
            node = by_id.get(kid) or {}
            if str(node.get("summary") or "").strip().startswith("你还在忙吗"):
                old_line = str(kid)
    if old_line:
        status, switched = req(
            "POST",
            f"/v1/sessions/{session_id}/switch",
            {"leaf_id": old_line, "character_id": card_id},
        )
        fourth = snapshot(session_id)
        check(
            "B7",
            "切回旧的那条线后路径回到旧版本",
            status == 200
            and any(
                str(node.get("content") or "").strip() == "你还在忙吗？"
                for node in fourth.get("items") or []
            ),
            {"status": status, "path": len(fourth.get("items") or [])},
        )
    else:
        check("B7", "切回旧的那条线后路径回到旧版本", False, {"old_line": old_line})

    report["summary"] = {
        "passed": sum(1 for item in report["checks"] if item["ok"]),
        "failed": sum(1 for item in report["checks"] if not item["ok"]),
    }

    # 收尾：删掉探针会话与临时角色卡
    status, _ = req("DELETE", "/v1/sessions/" + session_id)
    report["cleanup_session"] = status
    if card_id:
        status, _ = req("DELETE", "/v1/cards/" + card_id)
        report["cleanup_card"] = status
        # 临时角色还会留下一份世界状态，一并清掉，不留在数据目录里
        state_file = config.data_root() / "state" / card_id / "state.json"
        if state_file.exists():
            state_file.unlink()
            report["cleanup_state"] = str(state_file)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("probe written:", OUT)
    print("summary:", json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
