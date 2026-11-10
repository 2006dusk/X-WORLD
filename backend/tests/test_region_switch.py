"""对话里换区域的行为测试。

覆盖：
- 角色换区域时场景陈设随人走，随身物品与时间留着；
- 换区域写进会话头，下一轮装配注入新区域；
- 区域条目按条目 id 定位，地点标签是「世界书名_区域名」。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import presets as preset_service
from app import store
from app.assembly.pipeline import assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions
from app.main import create_app
from app.worldbooks import find_region

CHARACTER_ID = "c_move"
PRESET_ID = "p_builtin_rp"


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
                {
                    "id": "e_studio",
                    "kind": "region",
                    "name": "地址U",
                    "content": "调音台上那盏台灯是唯一的光源。",
                },
                {
                    "id": "e_store",
                    "kind": "region",
                    "name": "夜班便利店",
                    "content": "冰柜嗡嗡响，收银台上有半杯凉咖啡。",
                },
            ],
        },
    )


def test_relocate_moves_scene_and_keeps_items(data_dir: Path) -> None:
    """角色换区域：场景陈设清空，随身物品与时间留着。"""
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
        response = client.post(
            "/v1/state/relocate",
            json={"character_id": CHARACTER_ID, "region_id": "e_store"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["location"] == "地址N_夜班便利店"
    assert payload["map"] == "地址N_夜班便利店"
    assert payload["position"] == ""
    assert payload["scene_items"] == []
    assert payload["items"] == ["磁带"]
    assert payload["time"] == "0217"


def test_relocate_without_region_is_rejected(data_dir: Path) -> None:
    """没给区域时报错，不写坏状态。"""
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/state/relocate", json={"character_id": CHARACTER_ID}
        )
    assert response.status_code == 400


def test_find_region_resolves_label(data_dir: Path) -> None:
    """区域按条目 id 定位，标签是「世界书名_区域名」。"""
    _seed_book()
    found = find_region("e_store")
    assert found is not None
    assert found["book_id"] == "w_city"
    assert found["name"] == "夜班便利店"
    assert found["label"] == "地址N_夜班便利店"
    assert find_region("e_none") is None


def test_session_region_switch_updates_head_and_assembly(data_dir: Path) -> None:
    """换区域写进会话头，下一轮装配注入新区域。"""
    _seed_book()
    with TestClient(create_app()) as client:
        created = client.post(
            "/v1/sessions/",
            json={
                "character_id": CHARACTER_ID,
                "world_book_ids": ["w_city"],
                "region_id": "e_studio",
            },
        ).json()
        session_id = created["id"]
        assert created["region_id"] == "e_studio"

        updated = client.put(
            "/v1/sessions/" + session_id,
            json={"world_book_ids": ["w_city"], "region_id": "e_store"},
        )
        assert updated.status_code == 200
        assert updated.json()["region_id"] == "e_store"

    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="有水吗")],
        session=session_id,
        stream=False,
        xw=XwOptions(
            character_id=CHARACTER_ID,
            preset_id=PRESET_ID,
            world_book_ids=["w_city"],
            region_id="e_store",
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )
    ctx = assemble(request)
    segment = next(seg for seg in ctx.report.segments if seg.name == "world_info")
    assert "e_store" in (segment.entries or [])
    joined = chr(10).join(item.content for item in ctx.messages)
    assert "当前所在区域：夜班便利店" in joined
    assert "地址U" not in joined
