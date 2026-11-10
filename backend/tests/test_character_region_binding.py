"""人物与地区的绑定测试。

地区绑在人身上：换人时对话的位置跟着他走，换地区时人搬过去。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import presets as preset_service
from app import store
from app.main import create_app


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


def _bind(character_id: str, region_id: str) -> None:
    """给角色绑定一个地区。"""
    labels = {"e_studio": "地址N_地址U", "e_store": "地址N_夜班便利店"}
    store.save_state(
        {
            "location": labels[region_id],
            "position": "",
            "items": ["磁带"],
            "scene_items": ["台灯"],
            "map": labels[region_id],
            "time": "0217",
            "region_id": region_id,
        },
        character_id,
    )


def test_state_payload_exposes_bound_region(data_dir: Path) -> None:
    """状态里有角色绑定的地区。"""
    _seed_book()
    _bind("c_a", "e_store")
    with TestClient(create_app()) as client:
        payload = client.get("/v1/state/?character_id=c_a").json()
    assert payload["region_id"] == "e_store"


def test_switch_character_follows_his_region(data_dir: Path) -> None:
    """换人时对话的位置跟着他走：不是在这个地区看到那个人。"""
    _seed_book()
    _bind("c_a", "e_studio")
    _bind("c_b", "e_store")
    with TestClient(create_app()) as client:
        created = client.post(
            "/v1/sessions/",
            json={"character_id": "c_a", "world_book_ids": ["w_city"], "region_id": "e_studio"},
        ).json()
        updated = client.put(
            "/v1/sessions/" + created["id"], json={"character_id": "c_b"}
        ).json()
    assert updated["character_id"] == "c_b"
    assert updated["region_id"] == "e_store"
    assert updated["world_book_ids"] == ["w_city"]


def test_switch_region_moves_owner_and_binding(data_dir: Path) -> None:
    """换地区时主人搬家，他绑定的地区也跟着换。"""
    _seed_book()
    _bind("c_a", "e_studio")
    with TestClient(create_app()) as client:
        created = client.post(
            "/v1/sessions/",
            json={"character_id": "c_a", "world_book_ids": ["w_city"], "region_id": "e_studio"},
        ).json()
        updated = client.put(
            "/v1/sessions/" + created["id"],
            json={"world_book_ids": ["w_city"], "region_id": "e_store"},
        ).json()
    assert updated["region_id"] == "e_store"
    state = store.get_state("c_a")
    assert state is not None
    assert state["region_id"] == "e_store"
    assert state["location"] == "地址N_夜班便利店"
    assert state["scene_items"] == []
    assert state["items"] == ["磁带"]


def test_relocate_endpoint_binds_region(data_dir: Path) -> None:
    """搬迁接口同时把角色绑定到新地区。"""
    _seed_book()
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/state/relocate", json={"character_id": "c_a", "region_id": "e_store"}
        )
    assert response.status_code == 200
    assert response.json()["region_id"] == "e_store"
    assert response.json()["location"] == "地址N_夜班便利店"
