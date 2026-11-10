"""世界状态测试。

覆盖：
- 状态块解析：固定键名、别名、分号分隔、未知键；
- 状态合并、渲染与对外形状；
- 状态落盘与读回；
- 装配时状态进 memory 段回灌给模型；
- 状态块解析不出字段时按缺块处理。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import presets as preset_service
from app import store
from app.assembly.pipeline import assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions
from app.state import merge_state, parse_state_block, render_state_block, state_payload

STATE_LINES = [
    "当前位置：地址U二楼",
    "人物位置：控制台前",
    "人物物品：磁带、老花镜",
    "场景物品：推子、落地监听音箱",
    "地图：地址N",
    "时间：雨夜",
]
STATE_TEXT = chr(10).join(STATE_LINES)


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录并播种内置预设。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    preset_service.ensure_builtin_preset()
    return tmp_path


def test_parse_state_block_reads_six_fields() -> None:
    """六行固定键名解析成六个字段。"""
    fields = parse_state_block(STATE_TEXT)
    assert fields["location"] == "地址U二楼"
    assert fields["position"] == "控制台前"
    assert fields["items"] == ["磁带", "老花镜"]
    assert fields["scene_items"] == ["推子", "落地监听音箱"]
    assert fields["map"] == "地址N"
    assert fields["time"] == "雨夜"


def test_parse_state_block_splits_person_and_scene_items() -> None:
    """人物物品与场景物品分开落，不混在一栏。"""
    fields = parse_state_block(
        "人物物品：外套、钥匙" + chr(10) + "场景物品：咖啡机、抹布"
    )
    assert fields["items"] == ["外套", "钥匙"]
    assert fields["scene_items"] == ["咖啡机", "抹布"]


def test_parse_state_block_strips_paren_notes() -> None:
    """物品栏里的括号注释不算物件名，也不粘住后面那件物品。

    实测模型写「金笼（隐喻、实指）」这种带注释的物件名，注释留在状态里
    会被界面当成物品显示；括号后紧跟的另一件物品还会被粘成一串。
    """
    fields = parse_state_block(
        "人物物品：假阳具（手持）、丝袜（穿着）" + chr(10)
        + "场景物品：贞操锁、金笼（隐喻、实指）、龙床"
    )
    assert fields["items"] == ["假阳具", "丝袜"]
    assert fields["scene_items"] == ["贞操锁", "金笼", "龙床"]


def test_parse_state_block_empty_item_word_clears_list() -> None:
    """物品栏写成「无」时落成空列表，合并时据此清掉旧物品。"""
    fields = parse_state_block("人物物品：无" + chr(10) + "场景物品：咖啡机")
    assert fields["items"] == []
    assert fields["scene_items"] == ["咖啡机"]
    merged = merge_state({"items": ["旧外套"]}, fields)
    assert merged["items"] == []


def test_parse_state_block_accepts_aliases() -> None:
    """别名键归一到同一字段。"""
    fields = parse_state_block(
        "场景：地址S" + chr(10) + "站位：靠窗" + chr(10) + "随身物品：钥匙"
    )
    assert fields["location"] == "地址S"
    assert fields["position"] == "靠窗"
    assert fields["items"] == ["钥匙"]


def test_parse_state_block_splits_on_semicolon() -> None:
    """分号也算一条状态的边界。"""
    fields = parse_state_block("当前位置：地址S；装备物品：钥匙")
    assert fields["location"] == "地址S"
    assert fields["items"] == ["钥匙"]


def test_parse_state_block_ignores_unknown_keys() -> None:
    """识别不到的键忽略，认识的键照常解析。"""
    fields = parse_state_block("天气：雨" + chr(10) + "时间：雨夜")
    assert "weather" not in fields
    assert fields["time"] == "雨夜"


def test_parse_state_block_prose_returns_empty() -> None:
    """整段散文解析不出字段时返回空字典。"""
    assert parse_state_block("他站在地址U二楼，手里还攥着那盘磁带。") == {}
    assert parse_state_block("") == {}


def test_clean_state_fields_drops_unsupported_scene_items(data_dir: Path) -> None:
    """场景陈设按角色卡与世界书过滤：模型编的物件不进状态行。

    实测模型会在状态行里写隐喻代称与凭空家具（「金笼」「龙床」），
    消息里的状态行要跟落盘状态与右侧面板显示一致。
    """
    from app.assembly.pipeline import clean_state_fields

    store.save_card(
        {
            "id": "c_clean_001",
            "name": "角色K",
            "spec_version": "v2",
            "data": {
                "name": "角色K",
                "description": "地址N地址U的调音师",
                "personality": "克制、沉稳",
            },
        }
    )
    book = store.save_world_book(
        {
            "name": "地址N",
            "entries": [
                {
                    "keys": ["地址U"],
                    "content": "地址U只剩控制台那盏台灯亮着",
                    "kind": "entry",
                }
            ],
        }
    )
    fields = {
        "location": "地址N_地址U",
        "scene_items": ["控制台", "台灯", "金笼", "龙床"],
    }
    cleaned = clean_state_fields(
        fields,
        XwOptions(character_id="c_clean_001", world_book_ids=[book["id"]]),
    )
    assert cleaned["scene_items"] == ["控制台", "台灯"]


def test_filter_state_items_cleans_message_state_block(data_dir: Path) -> None:
    """消息里的状态行按依据过滤，括号注释与编造的物件都不留。
    """
    from app.routers.chat import _filter_state_items

    store.save_card(
        {
            "id": "c_clean_002",
            "name": "角色K",
            "spec_version": "v2",
            "data": {
                "name": "角色K",
                "description": "地址N地址U的调音师",
                "personality": "克制、沉稳",
            },
        }
    )
    book = store.save_world_book(
        {
            "name": "地址N",
            "entries": [
                {
                    "keys": ["地址U"],
                    "content": "地址U只剩控制台那盏台灯亮着",
                    "kind": "entry",
                }
            ],
        }
    )
    blocks = {
        "state": "当前位置：地址N_地址U" + chr(10)
        + "场景物品：控制台、金笼（隐喻、实指）、龙床" + chr(10)
        + "时间：雨夜",
    }
    out = _filter_state_items(
        blocks,
        XwOptions(character_id="c_clean_002", world_book_ids=[book["id"]]),
    )
    assert out is not None
    assert "控制台" in out["state"]
    assert "金笼" not in out["state"]
    assert "龙床" not in out["state"]
    assert "（" not in out["state"]


def test_merge_state_keeps_old_values() -> None:
    """模型没写的字段沿用旧值，写了的覆盖。"""
    old = {"location": "地址U", "items": ["磁带"], "map": "地址N", "time": "雨夜"}
    merged = merge_state(old, {"location": "地址S", "items": ["磁带", "钥匙"]})
    assert merged["location"] == "地址S"
    assert merged["items"] == ["磁带", "钥匙"]
    assert merged["map"] == "地址N"
    assert merged["time"] == "雨夜"


def test_render_state_block_writes_six_lines() -> None:
    """渲染成六行，键名与解析表一致。"""
    text = render_state_block(
        {
            "location": "地址U二楼",
            "position": "控制台前",
            "items": ["磁带", "老花镜"],
            "scene_items": ["推子", "落地监听音箱"],
            "map": "地址N",
            "time": "雨夜",
        }
    )
    assert text == STATE_TEXT


def test_state_payload_has_fixed_shape() -> None:
    """对外形状五个字段齐备，物品恒为列表。"""
    payload = state_payload({"location": "地址S"})
    assert payload["character_name"] == ""
    assert payload["location"] == "地址S"
    assert payload["position"] == ""
    assert payload["items"] == []
    assert payload["scene_items"] == []
    assert payload["map"] == ""
    assert payload["time"] == ""
    assert payload["updated_at"] is None


def test_state_payload_carries_character_name(data_dir: Path) -> None:
    """状态按角色存，对外形状带出角色卡里的名字。

    界面要显示是谁在这儿，名字由后端按角色 id 查角色卡得出。
    """
    store.save_card(
        {
            "id": "c_named_001",
            "name": "角色A",
            "spec_version": "v2",
            "data": {"name": "角色A", "description": "标签B"},
        }
    )
    payload = state_payload({"location": "地址A"}, "c_named_001")
    assert payload["character_name"] == "角色A"
    # 状态文件里记了角色 id 时不用传参也能查到
    payload2 = state_payload({"character_id": "c_named_001"})
    assert payload2["character_name"] == "角色A"


def test_store_round_trip(data_dir: Path) -> None:
    """状态落盘后能读回，并带上更新时间。"""
    store.save_state({"location": "地址S", "items": ["钥匙"]}, "c_demo")
    loaded = store.get_state("c_demo")
    assert loaded is not None
    assert loaded["location"] == "地址S"
    assert loaded["items"] == ["钥匙"]
    assert loaded["updated_at"]
    # 角色之间隔离
    assert store.get_state("c_other") is None


def test_assemble_injects_state_into_memory_segment(data_dir: Path) -> None:
    """状态进 memory 段回灌给模型，段数仍是八段。"""
    store.save_state(
        {"location": "地址U二楼", "items": ["磁带", "老花镜"], "map": "地址N", "time": "雨夜"},
        "c_demo",
    )
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="你在哪？")],
        stream=False,
        xw=XwOptions(character_id="c_demo", preset_id="p_builtin_rp"),
    )
    ctx = assemble(request)
    assert len(ctx.report.segments) == 10
    memory = [seg for seg in ctx.report.segments if seg.name == "memory"][0]
    assert memory.tokens > 0
    system_text = chr(10).join(
        msg.content for msg in ctx.messages if msg.role == "system"
    )
    assert "当前位置：地址U二楼" in system_text
    assert "人物物品：磁带、老花镜" in system_text


def test_assemble_without_state_keeps_memory_empty(data_dir: Path) -> None:
    """没有状态时 memory 段为空。"""
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="你在哪？")],
        stream=False,
        xw=XwOptions(character_id="c_demo", preset_id="p_builtin_rp"),
    )
    ctx = assemble(request)
    memory = [seg for seg in ctx.report.segments if seg.name == "memory"][0]
    assert memory.tokens == 0
