"""装配管线测试。

覆盖：
- 数据来源改为文件存储后，9 段顺序与段数不变；
- 给定一个临时 JSON 角色卡与预设，装配结果顺序与手工拼装一致；
- 无世界书命中时 world_info 段为 0 且不报错。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config, store
from app.assembly.pipeline import SEGMENT_ORDER, assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions

CARD_ID = "c_test_001"
PRESET_ID = "p_test_001"
SESSION_ID = "s_test_001"


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，并预置一张角色卡与一份预设。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))

    card_payload = {
        "id": CARD_ID,
        "name": "角色样本",
        "spec_version": "v2",
        "data": {
            "name": "角色样本",
            "description": "描述文本",
            "personality": "人格文本",
            "scenario": "场景文本",
            "tags": ["测试标签A", "测试标签B"],
            "mes_example": "示例对话",
            "post_history_instructions": "后置指令",
        },
    }
    store.write_json(store.card_dir(CARD_ID) / "card.json", card_payload)

    preset_payload = {
        "id": PRESET_ID,
        "name": "测试预设",
        "prompts": [
            {"id": "r2", "name": "b", "content": "用户组", "role": "user", "order": 20},
            {"id": "r1", "name": "a", "content": "系统正文", "role": "system", "order": 10},
            {"id": "r3", "name": "c", "content": "禁用组", "role": "system", "order": 30,
             "enabled": False},
        ],
    }
    store.write_json(store.presets_dir() / f"{PRESET_ID}.json", preset_payload)

    return tmp_path


def _make_request(
    *,
    session: str | None = None,
    world_book_ids: list[str] | None = None,
    user_input: str = "继续",
) -> ChatRequest:
    return ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_input)],
        session=session,
        stream=False,
        xw=XwOptions(
            character_id=CARD_ID,
            preset_id=PRESET_ID,
            world_book_ids=world_book_ids or [],
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )


def test_filtered_state_drops_items_outside_card(data_dir: Path) -> None:
    """状态里的陈设与世界书地区条目对不上时按世界书过滤。

    实测角色K（地址U调音师）被写成在厨房做咖啡，状态里多出咖啡机与牛奶锅，
    而状态提示又要求场景与状态一致，漂移因此每轮被固化。
    场景归世界书的地区条目，角色卡里不再带场景，陈设的依据因此来自世界书。
    """
    from app.assembly.pipeline import _filtered_state

    store.save_card(
        {
            "id": "c_state_001",
            "name": "角色K",
            "spec_version": "v2",
            "data": {
                "name": "角色K",
                "description": "地址N地址U的调音师，三十出头。沉默寡言。",
                "personality": "克制、沉稳。",
            },
        }
    )
    book = store.save_world_book(
        {
            "name": "地址N",
            "entries": [
                {
                    "keys": ["地址U"],
                    "content": "地址U在巷子尽头的二楼，只剩控制台那盏台灯亮着。",
                    "kind": "entry",
                }
            ],
        }
    )
    store.save_state(
        {
            "location": "地址N_地址U-内",
            "position": "厨房",
            "items": ["磁带", "耳机"],
            "scene_items": ["控制台", "台灯", "咖啡机", "牛奶锅", "空杯"],
            "map": "地址N_地址U",
            "time": "0217",
            "character_id": "c_state_001",
        },
        "c_state_001",
    )
    state = _filtered_state(XwOptions(character_id="c_state_001", world_book_ids=[book["id"]]))
    scene_items = state.get("scene_items", [])
    assert "控制台" in scene_items
    assert "台灯" in scene_items
    assert "咖啡机" not in scene_items
    assert "牛奶锅" not in scene_items
    assert "空杯" not in scene_items
    assert state.get("position") == ""
    assert "磁带" not in state.get("scene_items", [])


def test_character_role_extracts_identity() -> None:
    """身份短句取角色卡描述的第一句，用作状态提示里的身份锚点。"""
    from app.assembly.pipeline import _character_role

    assert _character_role({"description": "地址N地址U的调音师，三十出头。"}) == (
        "地址N地址U的调音师"
    )
    assert _character_role({"description": ""}) == ""
    assert _character_role({}) == ""
    assert _character_role(None) == ""


def test_character_role_keeps_dunhao_list_in_first_sentence() -> None:
    """身份锚点不能被顿号截断。

    角色A的身份是「父貈权臣的女儿、当朝皇后」。原来按「，。；、」一起切，
    顿号把「当朝皇后」切掉了，决定自称的那个身份词没了，模型无从判断自己
    不是皇帝，自称就滑到「朕」。
    """
    from app.assembly.pipeline import _character_role

    assert _character_role({"identity": "父貈权臣的女儿、当朝皇后。"}) == (
        "父貈权臣的女儿、当朝皇后"
    )
    assert _character_role(
        {"identity": "父貈权臣的女儿、当朝皇后。她不打算认这个身份。"}
    ) == ("父貈权臣的女儿、当朝皇后")


def test_state_hint_never_calls_the_character_he(data_dir: Path) -> None:
    """状态提示里不用带性别的代词指代角色。

    原来写死「他」：「位置只是他此刻站在哪里，不会改变他是谁」
    「他在这个故事里的身份是……」。角色A是皇后，提示词却称她「他」，
    模型就把她滑向皇帝，自称变成「朕」，甚至把角色与用户的身份对调。
    改成中性写法后，提示里只出现角色名与「这个角色」。
    """
    from app.assembly.pipeline import _state_hint

    store.save_card(
        {
            "id": "c_queen_001",
            "name": "角色A",
            "spec_version": "v1",
            "data": {
                "name": "角色A",
                "identity": "父貈权臣的女儿、当朝皇后。她不打算认这个身份。",
                "behavior": "话少，手边常有茶盏。",
                "speech": "短句。",
            },
        }
    )
    store.save_state(
        {
            "location": "宫室_地址E-内",
            "map": "宫室_地址E",
            "position": "榻边",
            "items": ["茶盏"],
            "scene_items": [],
            "character_id": "c_queen_001",
        },
        "c_queen_001",
    )
    hint = _state_hint(XwOptions(character_id="c_queen_001"))
    assert "扮演的角色始终是角色A" in hint
    assert "位置只说明此刻站在哪里" in hint
    assert "角色A在这个故事里的身份是父貈权臣的女儿、当朝皇后" in hint
    assert "位置只是他此刻站在哪里" not in hint
    assert "他在这个故事里的身份是" not in hint


def test_persona_segment_carries_user_profile(data_dir: Path) -> None:
    """用户档案进 persona 段，并落在 system 消息里。

    SillyTavern 有独立的 Persona Description 段，缺了它模型会替用户发挥。
    """
    store.set_setting("persona", "叫阿岚，二十六岁，进地址U是为了找人。")
    ctx = assemble(_make_request())
    persona = next(s for s in ctx.report.segments if s.name == "persona")
    assert persona.tokens > 0
    system_text = chr(10).join(
        msg.content for msg in ctx.messages if msg.role == "system"
    )
    assert "阿岚" in system_text


def test_persona_segment_empty_without_setting(data_dir: Path) -> None:
    """没写用户档案时 persona 段是空段，不占 token。"""
    ctx = assemble(_make_request())
    persona = next(s for s in ctx.report.segments if s.name == "persona")
    assert persona.tokens == 0


def test_user_mentioned_item_survives_state_filter(data_dir: Path) -> None:
    """用户自己提到的道具不被当作漂移丢掉。

    角色卡与世界书里没有的物件，只要用户在本轮说过就留着；
    模型自己编出来的仍然丢掉并报出来。
    """
    from app.assembly.pipeline import dropped_state_items, _filtered_state

    store.save_card(
        {
            "id": "c_state_002",
            "name": "角色K",
            "spec_version": "v2",
            "data": {
                "name": "角色K",
                "description": "地址N地址U的调音师，三十出头。沉默寡言。",
                "personality": "克制、沉稳。",
                "scenario": "地址U在巷子尽头的二楼，只剩控制台那盏台灯亮着。",
            },
        }
    )
    store.save_state(
        {
            "location": "地址N_地址U-内",
            "position": "控制台旁",
            "items": ["磁带"],
            "scene_items": ["控制台", "台灯", "银色打火机", "咖啡机"],
            "map": "地址N_地址U",
            "time": "0217",
            "character_id": "c_state_002",
        },
        "c_state_002",
    )
    xw = XwOptions(character_id="c_state_002")
    evidence = "我把银色打火机放在控制台上。"
    state = _filtered_state(xw, evidence)
    scene_items = state.get("scene_items", [])
    assert "银色打火机" in scene_items
    assert "咖啡机" not in scene_items
    dropped = dropped_state_items(xw, evidence)
    assert "咖啡机" in dropped
    assert "银色打火机" not in dropped


def test_segment_order_and_count(data_dir: Path) -> None:
    """数据来源改为文件后，段集合长度恒为 10 且顺序不变。"""
    ctx = assemble(_make_request())
    names = [seg.name for seg in ctx.report.segments]
    assert len(names) == 10
    assert names == list(SEGMENT_ORDER)


def test_no_store_import_of_db(data_dir: Path) -> None:
    """装配产出可序列化，且 memory 段恒为 tokens=0 的空段。"""
    ctx = assemble(_make_request())
    payload = ctx.report.model_dump()
    memory_seg = next(s for s in payload["segments"] if s["name"] == "memory")
    assert memory_seg["tokens"] == 0
    assert memory_seg["hits"] == 0
    # 段集合可 JSON 序列化
    json.dumps(payload, ensure_ascii=False)


def test_manual_assembly_matches(data_dir: Path) -> None:
    """装配结果的消息顺序与手工拼装一致。"""
    request = _make_request(user_input="你好")
    ctx = assemble(request)

    # 角色卡不再注入 mes_example：示例对话会被模型当正文素材逐字搬走。
    # 实测原样注入时六轮全部低于 150 字下沿，剥掉标记后仍照抄十句对白。
    # 角色段带身份锚点：角色名 + 「地点变了身份不变」。
    # 实测场景切换后模型把角色重新定义成新场景里的人（地址V员工），锚点为此加。
    # 角色用「扮演的角色」指代，不用「你」：整份提示里「你」只指用户，
    # 这里写「你都是这个人」会让模型把自己当成角色，身份识别跟着滑。
    # 卡片里的 scenario 不进这一段：场景由世界书的地区条目提供，两处地点
    # 不一致时模型会按冲突重新给角色安排身份。
    # 标签进这一段：标签是类型约束，只写在卡片数据里模型看不到。
    # 角色段按内部 v1 三层带标签拼：身份与处境 / 行为与细节 / 说话方式。
    # 旧格式卡（v2）由 to_internal_v1 映射过来，speech 用默认那条兜底。
    character_text = "\n".join(
        [
            "角色：角色样本。无论走到哪里，扮演的角色都是这个人，身份不会变。",
            "身份与处境：描述文本",
            "行为与细节：人格文本",
            "说话方式：短句，贴着此刻发生的事说；不解释自己的心思，不总结道理，不训话，也不给用户下指令。",
            "角色类型标签（必须符合）：测试标签A、测试标签B",
        ]
    )
    expected_system = "\n".join(["系统正文", character_text, "后置指令"])
    manual = [
        ChatMessage(role="system", content=expected_system),
        ChatMessage(role="user", content="用户组"),
        ChatMessage(role="user", content="你好"),
    ]

    assert [(m.role, m.content) for m in ctx.messages] == [
        (m.role, m.content) for m in manual
    ]


def test_character_segment_splits_tags_written_as_one_string(
    data_dir: Path,
) -> None:
    """标签写成顿号串时要逐个拆开并归一，不能当成一个标签。

    原来这里只切逗号：顿号串会变成一个标签，别名也不归一，提示词里就成了
    「角色类型标签（必须符合）：4i、女性支配」一条。
    """
    tag_card_id = "c_test_tags"
    store.write_json(
        store.card_dir(tag_card_id) / "card.json",
        {
            "id": tag_card_id,
            "name": "标签角色",
            "spec_version": "v1",
            "data": {
                "name": "标签角色",
                "identity": "身份文本",
                "behavior": "行为文本",
                "speech": "说话文本",
                "tags": "4i、女性支配",
            },
        },
    )
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="继续")],
        session=None,
        stream=False,
        xw=XwOptions(
            character_id=tag_card_id,
            preset_id=PRESET_ID,
            world_book_ids=[],
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )
    system = assemble(request).messages[0].content
    assert "角色类型标签（必须符合）：四爱、女性支配" in system


def test_character_segment_ignores_scenario_and_injects_tags(data_dir: Path) -> None:
    """角色段不拼卡片的场景，只注入标签。

    场景归世界书的地区条目；标签是类型约束，必须进提示词。
    """
    ctx = assemble(_make_request(user_input="你好"))
    system = ctx.messages[0].content
    assert "初始场景" not in system
    assert "场景文本" not in system
    assert "角色类型标签（必须符合）：测试标签A、测试标签B" in system


def test_world_info_miss_is_empty_not_error(data_dir: Path) -> None:
    """无世界书命中时 world_info 段为 0 且不报错。"""
    ctx = assemble(_make_request(world_book_ids=["w_missing"]))
    wi = next(s for s in ctx.report.segments if s.name == "world_info")
    assert wi.tokens == 0
    assert wi.entries == []


def test_missing_session_uses_empty_history(data_dir: Path, monkeypatch) -> None:
    """session 缺失或不存在时按空历史处理。"""
    ctx_absent = assemble(_make_request())
    hist_absent = next(s for s in ctx_absent.report.segments if s.name == "history")
    assert hist_absent.tokens == 0

    ctx_unknown = assemble(_make_request(session=SESSION_ID))
    hist_unknown = next(s for s in ctx_unknown.report.segments if s.name == "history")
    assert hist_unknown.tokens == 0


def test_truncation_preserves_segment_count(tmp_path: Path, monkeypatch) -> None:
    """超预算裁剪后段数仍为 8，system 段长度不变。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    store.write_json(
        store.card_dir(CARD_ID) / "card.json",
        {"id": CARD_ID, "data": {"description": "x" * 4000}},
    )
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="y" * 2000)],
        stream=False,
        xw=XwOptions(character_id=CARD_ID, max_context_tokens=400),
    )
    ctx = assemble(request)
    assert len(ctx.report.segments) == 10
    character = next(s for s in ctx.report.segments if s.name == "character")
    assert character.tokens > 0
    assert isinstance(ctx.truncated, bool)
