"""会话、角色与世界区域的关系测试。

覆盖：
- @ 换角色后消息仍写进同一段对话，会话作用域不变；
- 世界状态按角色存：每个人一份位置与随身物品，世界是共用的；
- 会话头记录对话所在的世界与区域，会话列表按主角色过滤；
- 对话选定的区域不靠关键词命中，一定注入；
- 世界书条目活引用卡片库角色：卡片改了条目正文跟着更新。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import presets as preset_service
from app import store
from app.assembly.pipeline import assemble
from app.cards import import_card_json
from app.contracts import ChatMessage, ChatRequest, XwOptions
from app.main import create_app
from app.routers import chat as chat_router

from conftest import FakeAdapter

CARD_ID = "c_demo"
PRESET_ID = "p_builtin_rp"

SCENE_TEXT = "他缓缓抬起头，目光落在来人身上。" + "窗外的雨声淅沥不息。" * 20
DIALOGUE_TEXT = "他压低声音：“你终于来了。”他顿了顿：“路上小心。”"


def _structured(location: str) -> str:
    """构造一轮带状态块的结构化回合。"""
    state = (
        "当前位置：" + location
        + chr(10)
        + "人物物品：磁带"
        + chr(10)
        + "场景物品：推子"
        + chr(10)
        + "地图：地址N"
        + chr(10)
        + "时间：雨夜"
    )
    return (
        "【场景】" + SCENE_TEXT
        + chr(10)
        + "【对话】" + DIALOGUE_TEXT
        + chr(10)
        + "【状态】" + state
    )


STUDIO_TEXT = _structured("地址U二楼")
STORE_TEXT = _structured("便利店门口")

CARD_A = {
    "name": "角色K",
    "description": "地址N地址U的调音师",
    "personality": "沉默、观察力强",
    "scenario": "雨夜的地址N",
    "first_mes": "门铃响了一声。",
    "creator": "tester",
    "character_version": "1.0",
}




@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录并播种内置预设。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    preset_service.ensure_builtin_preset()
    return tmp_path


def _request(prompt: str, session: str, character_id: str) -> dict:
    """构造一轮会话生成请求。"""
    return {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": character_id,
            "preset_id": PRESET_ID,
            "world_book_ids": [],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }


def test_session_keeps_one_scope_after_character_switch(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@ 换角色后消息仍写进同一段对话。"""
    adapter = FakeAdapter([STUDIO_TEXT, STORE_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        first = client.post(
            "/v1/chat/completions", json=_request("在吗", "s_switch", CARD_ID)
        )
        second = client.post(
            "/v1/chat/completions", json=_request("还在吗", "s_switch", "c_other")
        )
    assert first.status_code == 200
    assert second.status_code == 200

    assert store.session_scope("s_switch") == CARD_ID
    rows = store.get_messages("s_switch")
    assert len(rows) == 4
    assert rows[0]["content"] == "在吗"
    assert rows[2]["content"] == "还在吗"


def test_world_state_is_stored_per_character(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """世界状态按角色存：每个人一份位置与随身物品。"""
    adapter = FakeAdapter([STUDIO_TEXT, STORE_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        client.post("/v1/chat/completions", json=_request("在吗", "s_scope", CARD_ID))
        client.post(
            "/v1/chat/completions", json=_request("还在吗", "s_scope", "c_other")
        )

    lin = store.get_state(CARD_ID)
    assert lin is not None
    assert lin["location"] == "地址U二楼"
    other = store.get_state("c_other")
    assert other is not None
    assert other["location"] == "便利店门口"
    # 状态不按会话存：没有会话作用域的状态文件
    assert store.get_state("s_scope") is None


def test_session_head_records_world_and_region(data_dir: Path) -> None:
    """会话头记对话所在的世界与区域，会话列表按主角色过滤。"""
    with TestClient(create_app()) as client:
        created = client.post(
            "/v1/sessions/",
            json={
                "character_id": CARD_ID,
                "preset_id": PRESET_ID,
                "world_book_ids": ["w_demo"],
                "region_id": "e_region_001",
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["world_book_ids"] == ["w_demo"]
        assert body["region_id"] == "e_region_001"
        assert body["characters"] == [CARD_ID]

        items = client.get("/v1/sessions/").json()["items"]
        target = [item for item in items if item["id"] == body["id"]][0]
        assert target["world_book_ids"] == ["w_demo"]
        assert target["region_id"] == "e_region_001"

        filtered = client.get("/v1/sessions/?character_id=" + CARD_ID).json()["items"]
        assert any(item["id"] == body["id"] for item in filtered)
        empty = client.get("/v1/sessions/?character_id=c_none").json()["items"]
        assert not any(item["id"] == body["id"] for item in empty)

        by_region = client.get("/v1/sessions/?region_id=e_region_001").json()["items"]
        assert any(item["id"] == body["id"] for item in by_region)

        updated = client.put(
            "/v1/sessions/" + body["id"],
            json={"region_id": "e_region_002", "title": "夜谈"},
        )
        assert updated.status_code == 200
        assert updated.json()["region_id"] == "e_region_002"
        assert updated.json()["title"] == "夜谈"


def test_region_entry_is_injected_without_keyword_hit(data_dir: Path) -> None:
    """对话选定的区域不靠关键词命中，一定注入。"""
    store.write_json(
        store.card_dir(CARD_ID) / "card.json",
        {
            "id": CARD_ID,
            "name": "角色样本",
            "spec_version": "v2",
            "data": {"name": "角色样本", "description": "描述文本"},
        },
    )
    store.write_json(
        store.world_books_dir() / "w_region.json",
        {
            "id": "w_region",
            "name": "城事",
            "enabled": True,
            "entries": [
                {
                    "id": "e_region_001",
                    "kind": "region",
                    "name": "夜班便利店",
                    "keys": [],
                    "content": "街角的夜班便利店，灯管白得发冷。",
                    "priority": 10,
                    "constant": False,
                },
                {
                    "id": "e_plain_001",
                    "kind": "entry",
                    "name": "",
                    "keys": ["咖啡"],
                    "content": "咖啡机的蒸汽声。",
                    "priority": 5,
                    "constant": False,
                },
            ],
        },
    )
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="这里有咖啡吗")],
        session="s_region",
        stream=False,
        xw=XwOptions(
            character_id=CARD_ID,
            preset_id=PRESET_ID,
            world_book_ids=["w_region"],
            region_id="e_region_001",
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )
    ctx = assemble(request)
    segment = next(seg for seg in ctx.report.segments if seg.name == "world_info")
    assert "e_region_001" in (segment.entries or [])
    texts = [item.content for item in ctx.messages]
    joined = chr(10).join(texts)
    assert "当前所在区域：夜班便利店" in joined
    assert "街角的夜班便利店" in joined


def test_regions_endpoint_lists_only_region_entries(data_dir: Path) -> None:
    """区域接口只列区域条目。"""
    store.write_json(
        store.world_books_dir() / "w_region.json",
        {
            "id": "w_region",
            "name": "城事",
            "enabled": True,
            "entries": [
                {"id": "e_region_001", "kind": "region", "name": "夜班便利店", "content": "x"},
                {"id": "e_plain_001", "kind": "entry", "keys": ["咖啡"], "content": "y"},
            ],
        },
    )
    with TestClient(create_app()) as client:
        response = client.get("/v1/world-books/w_region/regions")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == ["e_region_001"]
    assert items[0]["name"] == "夜班便利店"


def test_world_book_entry_follows_card_changes(data_dir: Path) -> None:
    """世界书条目活引用卡片库角色：卡片改了条目正文跟着更新。"""
    card = import_card_json(dict(CARD_A))
    with TestClient(create_app()) as client:
        book = client.post(
            "/v1/world-books/", json={"name": "城事", "enabled": True, "entries": []}
        ).json()
        entry = client.post(
            "/v1/world-books/" + book["id"] + "/entries",
            json={
                "keys": [],
                "content": "",
                "card_ref": {"card_id": card["id"], "name": "角色K"},
            },
        ).json()
        assert "角色K" in entry["content"]
        assert "地址N地址U的调音师" in entry["content"]
        assert entry["keys"] == ["角色K"]

        changed = dict(CARD_A)
        changed["description"] = "地址N新开的调音工作室"
        response = client.put(
            "/v1/cards/" + card["id"],
            json={"name": "角色K", "spec_version": "v2", "data": changed},
        )
        assert response.status_code == 200

        again = client.get("/v1/world-books/" + book["id"]).json()
        target = [item for item in again["entries"] if item["id"] == entry["id"]][0]
        assert "地址N新开的调音工作室" in target["content"]
        assert target["card_ref"]["card_id"] == card["id"]
