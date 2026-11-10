"""删分支的测试。

分支是树上的一段支线：删的是这一段节点，不是整段对话。被删节点下面的
幸存子节点重挂到最近的未被删掉的祖先上，分叉点分出去的其他支线照旧留着；
head 若落在被删的那段里，回退到最近的未被删掉的祖先。

会话摘要里的 branch_count 是这段对话的分叉数：列表页显示它，不显示会话 id。

数据目录一律用 tmp_path + XWORLD_DATA_DIR 隔离，不碰真实用户数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import create_app

CHARACTER_ID = "c_del"
SESSION_ID = "s_del"


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    """应用测试客户端。"""
    return TestClient(create_app())


def _seed_two_lines() -> tuple[str, str, str]:
    """写两条支线，返回主线末端、支线末端与分叉点。

    甲乙丙丁是一条线，戊从丙下面分出去挂成兄弟。丙是分叉点。
    """
    user1 = store.branch_message(
        SESSION_ID, CHARACTER_ID, "", {"role": "user", "content": "甲"}
    )
    reply1 = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(user1["id"]),
        {"role": "assistant", "content": "乙"},
    )
    fork = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(reply1["id"]),
        {"role": "user", "content": "丙"},
    )
    main_end = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(fork["id"]),
        {"role": "assistant", "content": "丁"},
    )
    other_end = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(fork["id"]),
        {"role": "assistant", "content": "戊"},
    )
    return str(main_end["id"]), str(other_end["id"]), str(fork["id"])


def _seed_side_branch_with_child() -> tuple[str, str, str]:
    """写一条带子节点的支线，返回支线节点、它的子节点与分叉点。"""
    user1 = store.branch_message(
        SESSION_ID, CHARACTER_ID, "", {"role": "user", "content": "甲"}
    )
    reply1 = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(user1["id"]),
        {"role": "assistant", "content": "乙"},
    )
    fork = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(reply1["id"]),
        {"role": "user", "content": "丙"},
    )
    store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(fork["id"]),
        {"role": "assistant", "content": "丁"},
    )
    side = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(fork["id"]),
        {"role": "assistant", "content": "戊"},
    )
    tail = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(side["id"]),
        {"role": "assistant", "content": "己"},
    )
    return str(side["id"]), str(tail["id"]), str(fork["id"])


def _contents() -> list[str]:
    """会话里剩下的正文，按文件顺序。"""
    return [
        str(row.get("content", ""))
        for row in store.get_messages(SESSION_ID, CHARACTER_ID)
    ]


def test_delete_branch_removes_only_given_nodes(data_dir: Path) -> None:
    """删一条支线只删那条线，另一条线照旧留着。"""
    main_end, other_end, _fork = _seed_two_lines()
    result = store.delete_branch(SESSION_ID, CHARACTER_ID, [other_end])
    assert result["deleted"] == 1
    assert _contents() == ["甲", "乙", "丙", "丁"]
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    assert main_end in tree["nodes"]


def test_delete_branch_rehangs_surviving_children(data_dir: Path) -> None:
    """删掉支线的一段后，它下面的幸存子节点重挂到最近的幸存祖先上。"""
    side, tail, fork = _seed_side_branch_with_child()
    result = store.delete_branch(SESSION_ID, CHARACTER_ID, [side])
    assert result["deleted"] == 1
    assert _contents() == ["甲", "乙", "丙", "丁", "己"]
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    assert str(tree["nodes"][tail]["parent_id"]) == fork


def test_delete_branch_moves_head_to_surviving_ancestor(data_dir: Path) -> None:
    """head 落在被删的那段里时回退到最近的未被删掉的祖先。"""
    _main_end, other_end, fork = _seed_two_lines()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    assert str(tree["head"]) == other_end
    store.delete_branch(SESSION_ID, CHARACTER_ID, [other_end])
    fresh = store.session_tree(SESSION_ID, CHARACTER_ID)
    assert str(fresh["head"]) == fork


def test_delete_branch_endpoint_returns_snapshot(client: TestClient) -> None:
    """接口删分支后返回新的当前路径与分支树。"""
    main_end, other_end, _fork = _seed_two_lines()
    response = client.delete(
        f"/v1/sessions/{SESSION_ID}/branches", params={"nodes": other_end}
    )
    assert response.status_code == 200
    payload = response.json()
    # head 原本落在被删的支线上，回退到分叉点，当前路径因此少一轮
    assert [item["content"] for item in payload["items"]] == ["甲", "乙", "丙"]
    tree_ids = {node["id"] for node in payload["tree"]}
    assert other_end not in tree_ids
    assert main_end in tree_ids


def test_delete_branch_unknown_node_is_404(client: TestClient) -> None:
    """节点不存在时报 404，不动会话。"""
    _seed_two_lines()
    response = client.delete(
        f"/v1/sessions/{SESSION_ID}/branches", params={"nodes": "m_not_there"}
    )
    assert response.status_code == 404
    assert len(store.get_messages(SESSION_ID, CHARACTER_ID)) == 5


def test_delete_branch_without_nodes_is_rejected(client: TestClient) -> None:
    """没有指定要删的节点时报错，不动会话。"""
    _seed_two_lines()
    response = client.delete(f"/v1/sessions/{SESSION_ID}/branches", params={"nodes": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "branch_nodes_required"
    assert len(store.get_messages(SESSION_ID, CHARACTER_ID)) == 5


def test_session_summary_reports_branch_count(client: TestClient) -> None:
    """会话摘要给出分叉数：一条线是 0，两条线是 1。"""
    _seed_two_lines()
    response = client.get("/v1/sessions/")
    items = response.json()["items"]
    assert items
    assert items[0]["branch_count"] == 1
    store.truncate_messages(SESSION_ID, CHARACTER_ID, keep=0)
    response = client.get("/v1/sessions/")
    assert response.json()["items"][0]["branch_count"] == 0
