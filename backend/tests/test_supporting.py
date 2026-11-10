"""配角（对话中引入的角色）机制测试。

覆盖：
- 卡片角色定位的归一与列表过滤；
- supporting 段：按名字检索、位置在角色段之前、带边界声明、不注入类型标签。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import store
from app.assembly.pipeline import assemble
from app.cards.spec import normalize_role
from app.contracts import ChatMessage, ChatRequest, XwOptions

MAIN_CARD = "c_main_001"
NPC_CARD = "c_npc_001"
PRESET_ID = "p_test_001"


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """预置一张主角卡与一张配角卡。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))

    store.write_json(
        store.card_dir(MAIN_CARD) / "card.json",
        {
            "id": MAIN_CARD,
            "name": "主角甲",
            "spec_version": "v1",
            "data": {
                "name": "主角甲",
                "identity": "主角身份",
                "behavior": "主角行为",
                "speech": "主角说话",
                "tags": ["四爱"],
            },
        },
    )
    store.write_json(
        store.card_dir(NPC_CARD) / "card.json",
        {
            "id": NPC_CARD,
            "name": "配角乙",
            "spec_version": "v1",
            "data": {
                "name": "配角乙",
                "role": "supporting",
                "identity": "配角身份",
                "behavior": "配角行为",
                "speech": "配角说话",
                "relation": "相识",
                "state": {"location": "训练馆"},
                "tags": ["女攻男受"],
            },
        },
    )
    store.write_json(
        store.presets_dir() / f"{PRESET_ID}.json",
        {
            "id": PRESET_ID,
            "name": "测试预设",
            "prompts": [
                {"id": "r1", "name": "a", "content": "系统正文", "role": "system", "order": 10},
            ],
        },
    )
    return tmp_path


def _request(user_input: str) -> ChatRequest:
    return ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_input)],
        stream=False,
        xw=XwOptions(
            character_id=MAIN_CARD,
            preset_id=PRESET_ID,
            world_book_ids=[],
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )


def _system_of(ctx) -> str:
    return next(msg.content for msg in ctx.messages if msg.role == "system")


def test_role_normalizes_to_primary() -> None:
    """没写角色定位的卡按主角处理，认不出来的值也按主角。"""
    assert normalize_role(None) == "primary"
    assert normalize_role("") == "primary"
    assert normalize_role("乱七八糟") == "primary"
    assert normalize_role("SUPPORTING") == "supporting"


def test_card_list_carries_role(data_dir: Path) -> None:
    """卡片列表带角色定位，主角卡与配角卡分得开。"""
    from app.cards import list_cards

    roles = {item["name"]: item["role"] for item in list_cards()}
    assert roles["主角甲"] == "primary"
    assert roles["配角乙"] == "supporting"


def test_cards_endpoint_filters_by_role(data_dir: Path) -> None:
    """卡片接口按角色定位过滤，配角卡单独取得到。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    primary = client.get("/v1/cards/?role=primary").json()["items"]
    supporting = client.get("/v1/cards/?role=supporting").json()["items"]
    assert [item["name"] for item in primary] == ["主角甲"]
    assert [item["name"] for item in supporting] == ["配角乙"]
    assert client.get("/v1/cards/?role=乱写").json()["items"] == []


def test_supporting_injected_when_named(data_dir: Path) -> None:
    """提到配角名字时她的设定进 supporting 段。"""
    ctx = assemble(_request("配角乙在吗？"))
    seg = next(s for s in ctx.report.segments if s.name == "supporting")
    assert seg.tokens > 0
    system = _system_of(ctx)
    assert "配角乙" in system
    assert "配角说话" in system
    assert "与主角的关系：相识" in system
    assert "训练馆" in system


def test_supporting_absent_when_not_named(data_dir: Path) -> None:
    """没提到她时不注入 supporting 段。"""
    ctx = assemble(_request("今天天气不错"))
    seg = next(s for s in ctx.report.segments if s.name == "supporting")
    assert seg.tokens == 0


def test_supporting_before_character(data_dir: Path) -> None:
    """配角段排在角色段之前：配角是弱影响位。"""
    ctx = assemble(_request("配角乙在吗？"))
    system = _system_of(ctx)
    assert system.index("场上还有别人在场") < system.index("角色：主角甲")


def test_supporting_uses_name_not_gender_pronoun(data_dir: Path) -> None:
    """配角段用名字指代，不用「她」。

    配角可能是男性：写死性别代词会把模型引到错的性别上。
    """
    ctx = assemble(_request("配角乙在吗？"))
    system = _system_of(ctx)
    assert "配角乙不是你要扮演的角色" in system
    assert "你用第三人称写配角乙" in system
    assert "配角乙此刻的状态" in system
    assert "她不是你要扮演的角色" not in system
    assert "写她" not in system


def test_supporting_state_line_uses_name(data_dir: Path) -> None:
    """状态行以配角的名字起头，不是「她此刻的状态」。"""
    ctx = assemble(_request("配角乙在吗？"))
    system = _system_of(ctx)
    assert "她此刻的状态" not in system


def test_pending_card_is_not_injected(data_dir: Path) -> None:
    """待完善的卡不进 supporting 段：设定还空着，注进去是噪音。"""
    from app import store

    store.write_json(
        store.card_dir("c_npc_pending") / "card.json",
        {
            "id": "c_npc_pending",
            "name": "配角丙",
            "spec_version": "v1",
            "data": {
                "name": "配角丙",
                "role": "supporting",
                "pending": True,
                "identity": "",
                "behavior": "",
                "speech": "",
            },
        },
    )
    ctx = assemble(_request("配角丙在吗？"))
    seg = next(s for s in ctx.report.segments if s.name == "supporting")
    assert seg.tokens == 0


def test_supporting_has_boundary_and_no_tags(data_dir: Path) -> None:
    """配角段带边界声明，但不注入类型标签。"""
    ctx = assemble(_request("配角乙在吗？"))
    system = _system_of(ctx)
    assert "叙述视角仍然是主角" in system
    assert "配角说话" in system
    assert "女攻男受" not in system
