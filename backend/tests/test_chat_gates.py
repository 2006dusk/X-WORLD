"""复读闸门、长度闸门与结构闸门测试。

覆盖：
- 照搬上一轮整句时换种子重采样，并发 replace 事件；
- 回合短于预设下限时重写一次，并发 replace 事件；
- 回合缺块时按块重写一次，并发 replace 事件；
- 闸门重写后的正文被采用并落盘；
- 结构化回合的块事件与落盘块内容。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import presets as preset_service
from app import store
from app.contracts import ChatMessage
from app.main import create_app
from app.routers import chat as chat_router

from conftest import FakeAdapter

LONG_TEXT = "他缓缓抬起头，目光落在来人身上，眼神里闪过一丝警觉。" + "窗外的雨声淅沥不息。" * 20
PREVIOUS = "他突然抬头看向门口，眼神里闪过一丝警惕和不安。"

# 结构化回合的正文：三块齐全、长度达标。
SCENE_TEXT = "他缓缓抬起头，目光落在来人身上，眼神里闪过一丝警觉。" + "窗外的雨声淅沥不息。" * 20
DIALOGUE_TEXT = "他压低声音：“你终于来了。”他顿了顿，又补了一句：“路上小心，别急着赶回去。”"
STATE_TEXT = (
    "当前位置：地址U二楼"
    + chr(10)
    + "人物物品：磁带、老花镜"
    + chr(10)
    + "场景物品：推子、落地监听音箱"
    + chr(10)
    + "地图：地址N"
    + chr(10)
    + "时间：雨夜"
)
STRUCTURED_TEXT = (
    "【场景】" + SCENE_TEXT
    + chr(10)
    + "【对话】" + DIALOGUE_TEXT
    + chr(10)
    + "【状态】" + STATE_TEXT
)
PLAIN_STRUCTURED_TEXT = SCENE_TEXT + chr(10) + DIALOGUE_TEXT
# 状态块有内容但解析不出字段，等同于缺块。
UNPARSABLE_STATE_TEXT = (
    "【场景】" + SCENE_TEXT
    + chr(10)
    + "【对话】" + DIALOGUE_TEXT
    + chr(10)
    + "【状态】他此刻站在地址U二楼。"
)




@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录并播种内置预设与一张角色卡。

    状态行里的场景陈设按角色卡与世界书过滤，卡片要写清场景里的物件，
    否则状态块里的推子与台灯会被当成模型编的丢掉。
    """
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    preset_service.ensure_builtin_preset()
    store.save_card(
        {
            "id": "c_demo",
            "name": "角色K",
            "spec_version": "v2",
            "data": {
                "name": "角色K",
                "description": "地址N地址U的调音师，手边是推子与落地监听音箱，台灯照着控制台；楼下地址V的吧台与咖啡机也归他调",
                "personality": "沉默、观察力强",
            },
        }
    )
    return tmp_path


def _request(prompt: str, session: str, preset_id: str = "p_builtin_rp") -> dict:
    """构造一轮会话生成请求。"""
    return {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": preset_id,
            "world_book_ids": [],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }


def test_repeat_note_points_out_copied_fragment() -> None:
    """复读重采样时把照搬的那一段点出来，让模型知道要避开哪一段。"""
    messages = [ChatMessage(role="user", content="外面雨还没停")]
    updated = chat_router._with_repeat_note(messages, "抬起头来看着我，记住这张脸")
    assert "抬起头来看着我，记住这张脸" in updated[-1].content
    assert "不要重复上一轮" in updated[-1].content


def test_repetition_gate_resamples(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """照搬上一轮整句时换种子重采样一次。"""
    store.append_message("s_gate", "c_demo", {"role": "user", "content": "你在看什么"})
    store.append_message("s_gate", "c_demo", {"role": "assistant", "content": PREVIOUS})

    adapter = FakeAdapter([PREVIOUS, STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("外面雨还没停", "s_gate")
        )
    assert response.status_code == 200
    body = response.text
    assert "event: replace" in body
    assert '"reason": "repetition"' in body
    assert adapter.calls == 2

    rows = store.get_messages("s_gate", "c_demo")
    assert rows[-1]["role"] == "assistant"
    assert rows[-1]["content"] == PLAIN_STRUCTURED_TEXT


class _CapturingAdapter(FakeAdapter):
    """记录每次调用的推理参数。"""

    def __init__(self, texts: list[str]) -> None:
        super().__init__(texts)
        self.params: list = []

    async def stream_chat(self, messages, params, profile=None):
        """流式补全，并记下这一次的推理参数。"""
        self.params.append(params)
        async for event in super().stream_chat(messages, params, profile):
            yield event


def test_repetition_retry_raises_temperature(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """复读重采样要把温度提上去。

    温度比正文还低时，模型在概率质量集中处只会把上一轮照搬得更稳，
    实测出现过两次连续回合逐字相同。
    """
    store.append_message("s_temp", "c_demo", {"role": "user", "content": "你在看什么"})
    store.append_message("s_temp", "c_demo", {"role": "assistant", "content": PREVIOUS})

    adapter = _CapturingAdapter([PREVIOUS, STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("外面雨还没停", "s_temp")
        )
    assert response.status_code == 200
    assert len(adapter.params) == 2
    first, second = adapter.params[0], adapter.params[1]
    assert first.temperature == chat_router.SAMPLING_TEMPERATURE
    assert second.temperature == chat_router.REPETITION_RETRY_TEMPERATURE
    assert second.temperature > first.temperature


def test_length_gate_rewrites_short_turn(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回合短于预设下限时重写一次。

    长度闸门对声明了下限的预设仍然生效。内置预设不声明字数，回合长度交给
    模型自己判断，所以这里用一份自建预设来验证闸门本身。
    """
    store.save_preset(
        {
            "id": "p_min",
            "name": "带下限的预设",
            "prompts": [],
            "output_spec": {
                "min_chars": 150,
                "max_chars": 300,
                "structure": ["scene", "dialogue", "state"],
                "advance_plot": True,
            },
        }
    )
    adapter = FakeAdapter(["太短了。", STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions",
            json=_request("这盘带子是谁的？", "s_len", preset_id="p_min"),
        )
    assert response.status_code == 200
    body = response.text
    assert "event: replace" in body
    assert '"reason": "length"' in body
    assert adapter.calls == 2

    rows = store.get_messages("s_len", "c_demo")
    assert rows[-1]["content"] == PLAIN_STRUCTURED_TEXT


def test_optional_blocks_missing_does_not_rewrite(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景块与对白块是可选的，缺块不触发重写。

    LONG_TEXT 不带任何标记：正文整体落进第一个块，场景块与对白块都不算缺失。
    只有状态块写了却解析不出字段时才重写，见
    test_unparsable_state_block_triggers_rewrite。
    """
    adapter = FakeAdapter([LONG_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("这盘带子是谁的？", "s_struct")
        )
    assert response.status_code == 200
    assert "event: replace" not in response.text
    assert adapter.calls == 1

    rows = store.get_messages("s_struct", "c_demo")
    assert rows[-1]["role"] == "assistant"
    assert rows[-1]["content"] == LONG_TEXT.strip()


def test_role_owner_gate_rewrites_second_person_scene(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景里替用户写了动作时重写一次。

    口径借 SillyTavern 的角色归属做法：AI 只写角色本人，不替用户决定
    说什么或做什么。「你」当动作主语时越位；「他看向你」不越位。
    """
    bad_scene = "你缓缓抬起头，伸手扶住门框，眼神里闪过一丝警觉。" + "窗外的雨声淅沥不息。" * 20
    bad_text = (
        "【场景】" + bad_scene
        + chr(10)
        + "【对话】" + DIALOGUE_TEXT
        + chr(10)
        + "【状态】" + STATE_TEXT
    )
    adapter = FakeAdapter([bad_text, STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("外面雨还没停", "s_owner")
        )
    assert response.status_code == 200
    assert "event: replace" in response.text
    assert '"reason": "role_owner"' in response.text
    assert adapter.calls == 2

    rows = store.get_messages("s_owner", "c_demo")
    assert rows[-1]["content"] == PLAIN_STRUCTURED_TEXT


def test_role_owner_gate_rewrites_scene_with_dialogue(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景块里写了台词时重写一次。

    场景块只该写环境与角色本人的动作。台词混进场景，模型就是在替双方
    开口：实测旧会话里出现过模型自己写出用户问句并让角色回答。
    """
    bad_scene = "他推开门，低声问了句「谁？」，手停在门框上。" + "窗外的雨声淅沥不息。" * 20
    bad_text = (
        "【场景】" + bad_scene
        + chr(10)
        + "【对话】" + DIALOGUE_TEXT
        + chr(10)
        + "【状态】" + STATE_TEXT
    )
    adapter = FakeAdapter([bad_text, STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("外面雨还没停", "s_owner2")
        )
    assert response.status_code == 200
    assert "event: replace" in response.text
    assert '"reason": "role_owner"' in response.text
    assert adapter.calls == 2

    rows = store.get_messages("s_owner2", "c_demo")
    assert rows[-1]["content"] == PLAIN_STRUCTURED_TEXT


def test_role_owner_gate_covers_action_block(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """动作块里替用户写了动作时同样重写一次。

    叙述有两块：动作块与场景块。两块都要查：只查场景块的话，
    动作块里的越位文本没人查，闸门等于失效。
    """
    bad_action = "你伸手推开门，探头往里看了一眼。" + "窗外的雨声淅沥不息。" * 20
    bad_text = (
        "【动作】" + bad_action
        + chr(10)
        + "【对话】" + DIALOGUE_TEXT
        + chr(10)
        + "【状态】" + STATE_TEXT
    )
    adapter = FakeAdapter([bad_text, STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("外面雨还没停", "s_owner3")
        )
    assert response.status_code == 200
    assert "event: replace" in response.text
    assert '"reason": "role_owner"' in response.text
    assert adapter.calls == 2

    rows = store.get_messages("s_owner3", "c_demo")
    assert rows[-1]["content"] == PLAIN_STRUCTURED_TEXT


def test_state_leak_into_dialogue_triggers_rewrite(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """状态行漏进对白块时重写一次。

    模型把六行状态写在【对话】块里时，状态块空着，正文多出一堆状态文本，
    对白因此变长，还会骗过对白长度闸门。
    """
    leaked = (
        "【场景】" + SCENE_TEXT
        + chr(10)
        + "【对话】" + DIALOGUE_TEXT
        + chr(10)
        + "[“当前位置：地址U二楼”]"
        + chr(10)
        + "[“人物位置：厨房”]"
        + chr(10)
        + "[“时间：雨夜”]"
    )
    adapter = FakeAdapter([leaked, STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("这盘带子是谁的？", "s_leak")
        )
    assert response.status_code == 200
    assert "event: replace" in response.text
    assert '"reason": "structure"' in response.text
    assert adapter.calls == 2

    rows = store.get_messages("s_leak", "c_demo")
    assert rows[-1]["content"] == PLAIN_STRUCTURED_TEXT


def test_role_owner_gate_accepts_second_person_object(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景里「他看向你」不触发重写。

    角色对用户做动作时用「你」是正常写法。判据按「你」是不是动作主语
    来定，不是见到「你」就拦。
    """
    scene_with_you = "他缓缓抬起头，目光落在你身上，眼神里闪过一丝警觉。" + "窗外的雨声淅沥不息。" * 20
    text = (
        "【场景】" + scene_with_you
        + chr(10)
        + "【对话】" + DIALOGUE_TEXT
        + chr(10)
        + "【状态】" + STATE_TEXT
    )
    adapter = FakeAdapter([text])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("这盘带子是谁的？", "s_owner3")
        )
    assert response.status_code == 200
    assert "event: replace" not in response.text
    assert adapter.calls == 1


def test_no_gate_when_turn_is_long(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回合长度达标、块齐全且不照搬时不做重采样。"""
    adapter = FakeAdapter([STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("这盘带子是谁的？", "s_ok")
        )
    assert response.status_code == 200
    assert "event: replace" not in response.text
    assert adapter.calls == 1


def test_structured_turn_emits_blocks(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """结构化回合发块事件，块内容与落盘一致。"""
    adapter = FakeAdapter([STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("这盘带子是谁的？", "s_blocks")
        )
    assert response.status_code == 200
    body = response.text
    assert "event: blocks" in body
    assert '"scene"' in body
    assert '"dialogue"' in body
    # 标记不进正文
    assert "【场景】" not in body
    # usage 事件带生成速率
    assert '"generation_tps"' in body

    rows = store.get_messages("s_blocks", "c_demo")
    assert rows[-1]["content"] == PLAIN_STRUCTURED_TEXT
    assert rows[-1]["blocks"]["scene"] == SCENE_TEXT
    assert rows[-1]["blocks"]["dialogue"] == DIALOGUE_TEXT
    assert rows[-1]["blocks"]["state"] == STATE_TEXT


def test_state_block_is_parsed_and_persisted(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """状态块解析后落盘，并发 state 事件推给客户端面板。"""
    adapter = FakeAdapter([STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("这盘带子是谁的？", "s_state")
        )
    assert response.status_code == 200
    body = response.text
    assert "event: state" in body
    assert "地址U二楼" in body

    # 状态按角色存：每个人一份位置与随身物品
    state = store.get_state("c_demo")
    assert state is not None
    assert state["location"] == "地址U二楼"
    assert state["items"] == ["磁带", "老花镜"]
    assert state["map"] == "地址N"
    assert state["time"] == "雨夜"


def test_state_lines_without_marker_leave_the_body(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型漏写【状态】标记时，状态行要从正文里摘出去。

    状态行留在对白块里会直接出现在对话气泡上，右侧面板也更新不了。
    """
    text = (
        "【场景】他站在吧台前。"
        + chr(10)
        + "【对话】“老样子。”"
        + chr(10)
        + "当前位置：地址N_地址V-内"
        + chr(10)
        + "人物位置：靠窗"
        + chr(10)
        + "人物物品：外套、钱包"
        + chr(10)
        + "场景物品：吧台、咖啡机"
        + chr(10)
        + "地图：地址N"
        + chr(10)
        + "时间：0625"
    )
    adapter = FakeAdapter([text])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("喝点什么？", "s_no_marker")
        )
    assert response.status_code == 200

    rows = store.get_messages("s_no_marker", "c_demo")
    blocks = rows[-1]["blocks"]
    # 正文里不再有状态行
    assert "当前位置" not in blocks["dialogue"]
    assert "当前位置" not in blocks["scene"]
    # 状态行进了状态块
    assert "当前位置：地址N_地址V-内" in blocks["state"]
    assert "人物物品：外套、钱包" in blocks["state"]
    assert "场景物品：吧台、咖啡机" in blocks["state"]
    # 右侧面板拿到的是拆开后的两个物品栏
    state = store.get_state("c_demo")
    assert state is not None
    assert state["items"] == ["外套", "钱包"]
    assert state["scene_items"] == ["吧台", "咖啡机"]


def test_unparsable_state_block_triggers_rewrite(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """状态块有内容但解析不出字段时按缺块重写一次。"""
    adapter = FakeAdapter([UNPARSABLE_STATE_TEXT, STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_request("这盘带子是谁的？", "s_bad_state")
        )
    assert response.status_code == 200
    assert '"reason": "structure"' in response.text
    assert adapter.calls == 2
    state = store.get_state("c_demo")
    assert state is not None
    assert state["location"] == "地址U二楼"
