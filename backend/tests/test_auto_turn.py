"""自动回合测试：用户不说话时 AI 自己往下走。

覆盖：
- 自动回合的指令进 user 段，且不带上一轮的用户原话；
- describe 模式要求不写对白，并保留状态提示；
- describe 模式豁免对白承接闸门；
- 场景推进轮丢掉对白块，气泡与落盘一致；
- 自动回合不落空的用户消息。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import presets as preset_service
from app import store
from app.assembly.pipeline import assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions
from app.main import create_app
from app.routers import chat as chat_router

from conftest import FakeAdapter

SCENE_TEXT = "他缓缓抬起头，目光落在窗外的雨幕上，指尖在桌沿敲了两下。"
DIALOGUE_TEXT = "他压低声音：“你终于来了。”他顿了顿，又补了一句：“路上小心，别急着赶回去。”"
STATE_TEXT = (
    "当前位置：地址U二楼"
    + chr(10)
    + "人物物品：老花镜"
    + chr(10)
    + "场景物品：推子"
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

# 上一轮用户说过的话。自动回合时它不该再出现在本轮输入里。
STALE_USER_TEXT = "上一轮用户说的话"




@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录并播种内置预设与一张角色卡。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    preset_service.ensure_builtin_preset()
    store.save_card(
        {
            "id": "c_demo",
            "name": "角色K",
            "spec_version": "v2",
            "data": {
                "name": "角色K",
                "description": "地址N地址U的调音师，手边是推子与落地监听音箱，台灯照着控制台",
                "personality": "沉默、观察力强",
            },
        }
    )
    return tmp_path


def _assemble_request(mode: str, session: str = "") -> ChatRequest:
    """构造一个自动回合的装配请求。

    messages 里留着上一轮的用户原话：自动回合时它不该被当成本轮输入。
    """
    return ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=STALE_USER_TEXT)],
        session=session,
        stream=False,
        xw=XwOptions(
            character_id="c_demo",
            preset_id="p_builtin_rp",
            world_book_ids=[],
            memory_mode="hybrid",
            max_context_tokens=8192,
            auto_turn=mode,
        ),
    )


def _api_request(mode: str, session: str, prompt: str = STALE_USER_TEXT) -> dict:
    """构造一个自动回合的会话生成请求。"""
    return {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": "p_builtin_rp",
            "world_book_ids": [],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
            "auto_turn": mode,
        },
    }


def _user_message(ctx) -> str:
    """取装配结果里的本轮输入。"""
    return next(msg.content for msg in ctx.messages if msg.role == "user")


def test_auto_turn_user_segment_carries_directive(data_dir: Path) -> None:
    """自动回合的指令进 user 段，且不带上一轮的用户原话。

    带了会被模型当成用户刚说过一遍，对话就接错了。
    """
    ctx = assemble(_assemble_request("continue"))
    content = _user_message(ctx)
    assert "本轮用户没有说话" in content
    assert STALE_USER_TEXT not in content


def test_describe_turn_directive_forbids_dialogue(data_dir: Path) -> None:
    """场景推进轮的指令写明不写对白块。"""
    ctx = assemble(_assemble_request("describe"))
    content = _user_message(ctx)
    assert "本轮用户没有说话" in content
    assert "本轮只写【场景】块与【状态】块" in content


def test_continue_turn_directive_forbids_scene(data_dir: Path) -> None:
    """对话推进轮的指令写明不写场景块。

    那一轮是推进语言交互：环境描写不在这一轮里。
    先写出来再丢掉是白花 token，要在生成时就拦掉。
    动作块留着：纯台词的回合读起来像角色在自言自语。
    """
    ctx = assemble(_assemble_request("continue"))
    content = _user_message(ctx)
    assert "本轮只写【动作】块与【对话】块与【状态】块" in content
    # 提示里不提场景：提了等于叫模型写它。
    assert "【场景】" not in content


def test_continue_turn_requirement_omits_scene_block(data_dir: Path) -> None:
    """对话推进轮的回合要求里不出现场景块，但有动作块。

    提示里没有场景块，模型就不会顺手写一段环境。
    动作块要有：这一轮用户没说话，角色要主动开口，
    没有动作的台词读起来就是自言自语。
    """
    ctx = assemble(_assemble_request("continue"))
    content = _user_message(ctx)
    assert "【场景】" not in content
    assert "【动作】" in content
    assert "【对话】" in content


def test_describe_turn_requirement_omits_dialogue_block(data_dir: Path) -> None:
    """场景推进轮的回合要求里不出现对白块。"""
    ctx = assemble(_assemble_request("describe"))
    content = _user_message(ctx)
    assert "【对话】" not in content
    assert "【场景】" in content


def test_normal_turn_requirement_omits_scene_block(data_dir: Path) -> None:
    """普通回合的提示里不出现场景块，但有动作块。

    场景块一旦写进提示，模型几乎每轮都写一段环境，场景与对白一比一。
    动作块是这轮补回来的：回合要有一到两句角色此刻的动作或反应，
    只有台词的回合读起来像角色在自言自语。
    解析结构仍然认场景块：模型自己想交代的那句环境仍会落进去。
    """
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content="你还在吗？")],
        stream=False,
        xw=XwOptions(
            character_id="c_demo",
            preset_id="p_builtin_rp",
            world_book_ids=[],
            max_context_tokens=8192,
        ),
    )
    ctx = assemble(request)
    content = _user_message(ctx)
    assert "【场景】" not in content
    assert "【动作】" in content
    assert "【对话】" in content
    # 解析用的结构含动作、场景、对白与状态四块。
    assert preset_service.BUILTIN_OUTPUT_SPEC["structure"] == [
        "action",
        "scene",
        "dialogue",
        "state",
    ]


def test_turn_requirement_speaks_to_user(data_dir: Path) -> None:
    """回合要求写明台词是对用户说的。

    只写「回应用户」时模型常写成自说自话的旁观感慨；
    把「对用户说，像在跟对方搭话」写进回合要求（附在本轮输入末尾），
    台词才会像一段来回的对话。
    """
    ctx = assemble(_assemble_request("continue"))
    content = _user_message(ctx)
    assert "对用户说" in content
    assert "不要自言自语" in content
    # 用户没说话的那一轮要求角色主动开口。
    assert "角色要主动开口" in content


def test_auto_turn_keeps_state_hint(data_dir: Path) -> None:
    """自动回合也要带当前状态，场景才接得上。"""
    store.save_state(
        {
            "location": "地址N_地址U",
            "position": "控制台前",
            "items": ["老花镜"],
            "scene_items": ["推子"],
            "map": "地址N",
            "time": "雨夜",
        },
        "c_demo",
    )
    ctx = assemble(_assemble_request("continue"))
    content = _user_message(ctx)
    assert "当前状态" in content
    assert "控制台前" in content


def test_dialogue_gate_skipped_for_describe_turn() -> None:
    """场景推进轮豁免对白承接闸门。

    那一轮本就不该有对白，闸门会把模型按回「补两句台词」。
    """
    blocks = {"dialogue": "“走吧。”"}
    structure = ["scene", "dialogue", "state"]
    assert chat_router._dialogue_uptake_short(blocks, structure) is True
    assert chat_router._dialogue_uptake_short(blocks, structure, "describe") is False


def test_drop_dialogue_removes_dialogue_block() -> None:
    """丢对白只动 dialogue 块，场景与状态原样留下。"""
    blocks = {"scene": "场景", "dialogue": "台词", "state": "状态"}
    cleaned = chat_router._drop_dialogue(blocks)
    assert cleaned == {"scene": "场景", "state": "状态"}
    assert chat_router._drop_dialogue({"scene": "场景"}) == {"scene": "场景"}


def test_auto_turn_persists_no_empty_user_message(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自动回合不落空的用户消息。"""
    adapter = FakeAdapter([STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_api_request("continue", "s_auto")
        )
    assert response.status_code == 200
    assert adapter.calls == 1

    rows = store.get_messages("s_auto", "c_demo")
    assert [row["role"] for row in rows] == ["assistant"]
    assert rows[0]["content"]


def test_auto_turn_directive_reaches_model(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型收到的本轮输入是自动回合指令。"""
    adapter = FakeAdapter([STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_api_request("continue", "s_auto2")
        )
    assert response.status_code == 200
    sent = adapter.seen[0]
    last = sent[-1]
    assert last.role == "user"
    assert "本轮用户没有说话" in last.content
    assert STALE_USER_TEXT not in last.content


def test_describe_turn_drops_dialogue_block(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景推进轮丢掉对白块，气泡与落盘一致。

    模型有时仍会写一句台词，那一句不能出现在气泡与流水里。
    """
    adapter = FakeAdapter([STRUCTURED_TEXT])
    monkeypatch.setattr(chat_router, "_get_adapter", lambda: adapter)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/chat/completions", json=_api_request("describe", "s_desc")
        )
    assert response.status_code == 200
    assert adapter.calls == 1

    rows = store.get_messages("s_desc", "c_demo")
    blocks = rows[-1].get("blocks") or {}
    assert "dialogue" not in blocks
    assert DIALOGUE_TEXT not in rows[-1]["content"]
    assert SCENE_TEXT in rows[-1]["content"]
    # 流式增量里也不该出现对白块：这一轮本来就不写对白。
    assert '"block": "dialogue"' not in response.text
    frames = response.text.split("event: blocks")
    assert len(frames) > 1
    payload = json.loads(frames[1].split("data: ", 1)[1].split(chr(10), 1)[0])
    assert "dialogue" not in payload["blocks"]
