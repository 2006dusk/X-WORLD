"""对话身份的用例：一个角色一条对话。

覆盖：
- 同一个角色再建一次，回到同一条对话，消息继续累积；
- 会话列表一个角色只出一条，历史遗留的重复会话按消息数取舍；
- 对话换区域时角色跟着搬家，场景陈设清空、随身物品留着。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import presets as preset_service
from app import store
from app.main import create_app

CHARACTER_ID = "c_move"


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录并播种内置预设。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    preset_service.ensure_builtin_preset()
    return tmp_path


def _seed_book() -> None:
    """播种一本带两个区域的世界书。"""
    store.write_json(
        store.world_books_dir() / "w_city.json",
        {
            "id": "w_city",
            "name": "地址N",
            "enabled": True,
            "entries": [
                {"id": "e_studio", "kind": "region", "name": "地址U", "content": "台灯是唯一的光源。"},
                {"id": "e_store", "kind": "region", "name": "夜班便利店", "content": "冰柜嗡嗡响。"},
            ],
        },
    )


def test_same_character_keeps_one_conversation(data_dir: Path) -> None:
    """同一个角色再建一次，回到同一条对话，消息继续累积。"""
    with TestClient(create_app()) as client:
        first = client.post(
            "/v1/sessions/", json={"character_id": CHARACTER_ID}
        ).json()
        store.branch_message(
            first["id"], CHARACTER_ID, "", {"role": "user", "content": "在吗"}
        )
        second = client.post(
            "/v1/sessions/", json={"character_id": CHARACTER_ID}
        ).json()
    assert second["id"] == first["id"]
    assert second["message_count"] == 1


def test_session_list_is_one_per_character(data_dir: Path) -> None:
    """会话列表一个角色只出一条。"""
    with TestClient(create_app()) as client:
        client.post("/v1/sessions/", json={"character_id": "c_a"})
        client.post("/v1/sessions/", json={"character_id": "c_b"})
        items = client.get("/v1/sessions/").json()["items"]
    assert len(items) == 2
    assert sorted(item["character_id"] for item in items) == ["c_a", "c_b"]


def test_legacy_duplicates_collapse_to_the_longest(data_dir: Path) -> None:
    """历史遗留的同一个角色的多条会话只留一条：消息最多的那条。"""
    store.append_jsonl(
        store.sessions_dir("c_c") / "s_legacy.jsonl",
        {"role": "session", "id": "s_legacy", "character_id": "c_c"},
    )
    store.append_jsonl(
        store.sessions_dir("c_c") / "s_legacy.jsonl",
        {"role": "user", "content": "在吗"},
    )
    store.append_jsonl(
        store.sessions_dir("c_c") / "s_empty.jsonl",
        {"role": "session", "id": "s_empty", "character_id": "c_c"},
    )
    with TestClient(create_app()) as client:
        items = client.get("/v1/sessions/").json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == "s_legacy"


def test_region_switch_moves_the_character(data_dir: Path) -> None:
    """对话换区域时角色跟着搬家：场景陈设清空，随身物品留着。"""
    _seed_book()
    store.save_state(
        {
            "location": "地址N_地址U-内",
            "position": "控制台旁",
            "items": ["磁带"],
            "scene_items": ["控制台", "台灯"],
            "map": "地址N_地址U",
            "time": "0217",
        },
        CHARACTER_ID,
    )
    with TestClient(create_app()) as client:
        created = client.post(
            "/v1/sessions/",
            json={
                "character_id": CHARACTER_ID,
                "world_book_ids": ["w_city"],
                "region_id": "e_studio",
            },
        ).json()
        updated = client.put(
            "/v1/sessions/" + created["id"],
            json={"world_book_ids": ["w_city"], "region_id": "e_store"},
        ).json()
    assert updated["region_id"] == "e_store"
    state = store.get_state(CHARACTER_ID)
    assert state is not None
    assert state["location"] == "地址N_夜班便利店"
    assert state["map"] == "地址N_夜班便利店"
    assert state["scene_items"] == []
    assert state["items"] == ["磁带"]
    assert state["time"] == "0217"
