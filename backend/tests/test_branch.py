"""会话分支测试。

覆盖 store 的树读取、旧会话 id 派生、分支写入与 head 移动，路由的 branch
与 switch 接口，以及装配只取当前分支路径。

数据目录一律用 tmp_path + XWORLD_DATA_DIR 隔离，不碰真实用户数据。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import store
from app.assembly.pipeline import assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions
from app.main import create_app

CHARACTER_ID = "c_branch"
SESSION_ID = "s_branch"
PRESET_ID = "p_branch"


def _write_legacy_session() -> None:
    """写一个没有 id 与 parent_id 的旧会话文件。"""
    path = store.sessions_dir(CHARACTER_ID) / (SESSION_ID + ".jsonl")
    rows = [
        {"role": "session", "id": SESSION_ID, "character_id": CHARACTER_ID},
        {"role": "user", "content": "甲"},
        {"role": "assistant", "content": "乙"},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_preset() -> None:
    """写一个声明三块结构的预设，编辑解析要用。"""
    store.write_json(
        store.presets_dir() / (PRESET_ID + ".json"),
        {
            "id": PRESET_ID,
            "name": "分支测试预设",
            "prompts": [],
            "output_spec": {"structure": ["scene", "dialogue", "state"]},
        },
    )


def _seed_branch() -> str:
    """写两轮问答，返回末条角色回复的节点 id。"""
    user1 = store.branch_message(
        SESSION_ID, CHARACTER_ID, "", {"role": "user", "content": "甲"}
    )
    reply1 = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(user1["id"]),
        {"role": "assistant", "content": "乙"},
    )
    user2 = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(reply1["id"]),
        {"role": "user", "content": "丙"},
    )
    reply2 = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(user2["id"]),
        {"role": "assistant", "content": "丁"},
    )
    return str(reply2["id"])


def test_legacy_session_derives_ids(data_dir: Path) -> None:
    """旧会话没有 id 时按文件顺序派生，路径顺序与文件一致。"""
    _write_legacy_session()
    path = store.session_path(SESSION_ID, CHARACTER_ID)
    assert [row["content"] for row in path] == ["甲", "乙"]
    assert path[0]["id"] == "legacy-" + SESSION_ID + "-0"
    assert path[1]["parent_id"] == path[0]["id"]


def test_branch_message_moves_head_and_keeps_old(data_dir: Path) -> None:
    """分支写入把 head 移到新节点，旧节点留在文件里。"""
    _seed_branch()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    user2 = str(tree["nodes"][str(tree["head"])]["parent_id"])
    created = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        user2,
        {"role": "assistant", "content": "戊"},
    )
    assert store.session_tree(SESSION_ID, CHARACTER_ID)["head"] == created["id"]
    contents = [
        str(row.get("content", ""))
        for row in store.get_messages(SESSION_ID, CHARACTER_ID)
    ]
    assert contents == ["甲", "乙", "丙", "丁", "戊"]
    path = [
        str(row.get("content", ""))
        for row in store.session_path(SESSION_ID, CHARACTER_ID)
    ]
    assert path == ["甲", "乙", "丙", "戊"]


def test_snapshot_lists_siblings(data_dir: Path) -> None:
    """快照给出当前路径与整棵树，兄弟分支都在树里。"""
    _seed_branch()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    user2 = str(tree["nodes"][str(tree["head"])]["parent_id"])
    store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        user2,
        {"role": "assistant", "content": "戊"},
    )
    snapshot = store.session_snapshot(SESSION_ID, CHARACTER_ID)
    assert [item["content"] for item in snapshot["items"]] == ["甲", "乙", "丙", "戊"]
    siblings = [
        node
        for node in snapshot["tree"]
        if node["parent_id"] == user2
    ]
    assert len(siblings) == 2


def test_assemble_reads_current_path(data_dir: Path) -> None:
    """装配只取当前分支路径，分支外的旧轮次不进历史。"""
    _seed_branch()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    user2 = str(tree["nodes"][str(tree["head"])]["parent_id"])
    store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        user2,
        {"role": "assistant", "content": "戊"},
    )
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="新问题")],
        session=SESSION_ID,
        stream=False,
        xw=XwOptions(character_id=CHARACTER_ID, max_context_tokens=8192),
    )
    ctx = assemble(request)
    history = [m.content for m in ctx.messages if m.role != "system"]
    assert "丁" not in " ".join(history)
    assert "丙" in " ".join(history)


def test_switch_endpoint_changes_path(data_dir: Path) -> None:
    """切换接口把 head 换到目标叶子。"""
    _seed_branch()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    user2 = str(tree["nodes"][str(tree["head"])]["parent_id"])
    old_reply = str(tree["nodes"][user2]["parent_id"])
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/sessions/" + SESSION_ID + "/switch",
            json={"character_id": CHARACTER_ID, "leaf_id": old_reply},
        )
    assert response.status_code == 200
    body = response.json()
    assert [item["content"] for item in body["items"]] == ["甲", "乙"]
    assert body["head_id"] == old_reply


def test_edit_endpoint_writes_state(data_dir: Path) -> None:
    """编辑定稿带状态块时同步写世界状态，并在父节点下分叉。"""
    _write_preset()
    _seed_branch()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    user2 = str(tree["nodes"][str(tree["head"])]["parent_id"])
    edit_text = (
        "【场景】雨夜的地址U。"
        + chr(10)
        + "【对话】“改过的台词。”"
        + chr(10)
        + "【状态】当前位置：地址N_地址U-内"
        + chr(10)
        + "人物位置：控制台前"
        + chr(10)
        + "人物物品：录音笔"
        + chr(10)
        + "场景物品：控制台"
        + chr(10)
        + "地图：地址N_地址U"
        + chr(10)
        + "时间：0700"
    )
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/edit",
            json={
                "session": SESSION_ID,
                "parent_id": user2,
                "content": edit_text,
                "xw": {"character_id": CHARACTER_ID, "preset_id": PRESET_ID},
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["state"]["time"] == "0700"
    saved = store.get_state(CHARACTER_ID)
    assert saved is not None
    assert saved["time"] == "0700"
    assert body["items"][-1]["content"].endswith("“改过的台词。”")
    # 新回复与旧回复互为兄弟，当前路径走到新回复
    assert body["items"][-1]["parent_id"] == user2
    assert [item["content"] for item in body["items"]][-2:] == ["丙", body["items"][-1]["content"]]


def test_edit_endpoint_rejects_unknown_parent(data_dir: Path) -> None:
    """父节点不存在时报错。"""
    _write_preset()
    _seed_branch()
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/edit",
            json={
                "session": SESSION_ID,
                "parent_id": "m_不存在",
                "content": "甲",
                "xw": {"character_id": CHARACTER_ID, "preset_id": PRESET_ID},
            },
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_node_not_found"
