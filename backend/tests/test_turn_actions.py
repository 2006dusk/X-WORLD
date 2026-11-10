"""轮次动作的落盘语义与块顺序测试。

覆盖 app.routers.chat 的 _branch_point 与 _persist_turn：重新生成原地替换
那条回复，不在树上新增节点，因此不算分支；改写新开一条用户输入，与旧
输入互为兄弟，旧的那条线留在树上可切回。
以及 app.inference.structure.split_blocks 按模型实际写出的块顺序返回字典。

回合形式自由后，场景块与对白块是可选的，缺块不触发重写；_missing_blocks
只认状态块解析失败这一种情况。

数据目录一律用 tmp_path + XWORLD_DATA_DIR 隔离，不碰真实用户数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from app import store
from app.contracts import ChatRequest, XwOptions
from app.inference.structure import split_blocks
from app.routers.chat import _branch_point, _missing_blocks, _persist_turn

CHARACTER_ID = "c_act"
SESSION_ID = "s_act"
STRUCTURE = ["scene", "dialogue", "state"]


def _seed_turns() -> None:
    """往会话流水里写两轮问答，走分支写入以带上节点 id。"""
    user1 = store.branch_message(
        SESSION_ID, CHARACTER_ID, "", {"role": "user", "content": "甲"}
    )
    reply1 = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(user1["id"]),
        {"role": "assistant", "content": "乙"},
    )
    user2 = store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(reply1["id"]),
        {"role": "user", "content": "丙"},
    )
    store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        str(user2["id"]),
        {"role": "assistant", "content": "丁"},
    )


def _request(
    action: Literal["append", "regenerate", "revise"],
    target_id: str = "",
) -> ChatRequest:
    """构造带轮次动作的请求。"""
    return ChatRequest(
        model="xw-local-default",
        messages=[],
        session=SESSION_ID,
        stream=False,
        xw=XwOptions(character_id=CHARACTER_ID),
        turn_action=action,
        target_id=target_id or None,
    )


def _file_contents() -> list[str]:
    """读回会话流水的正文列表，文件顺序。"""
    return [
        str(row.get("content", "")) for row in store.get_messages(SESSION_ID, CHARACTER_ID)
    ]


def _path_contents() -> list[str]:
    """读回当前分支路径的正文列表。"""
    return [
        str(row.get("content", ""))
        for row in store.session_path(SESSION_ID, CHARACTER_ID)
    ]


def test_append_branch_point_is_head(data_dir: Path) -> None:
    """追加动作挂在当前 head 上。"""
    _seed_turns()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    assert _branch_point(_request("append")) == tree["head"]


def test_regenerate_branch_point_is_user_input(data_dir: Path) -> None:
    """重新生成挂在末条角色回复的父节点，也就是那条用户输入。"""
    _seed_turns()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    head = str(tree["head"])
    user_id = str(tree["nodes"][head]["parent_id"])
    assert _branch_point(_request("regenerate")) == user_id


def test_revise_branch_point_is_before_last_user(data_dir: Path) -> None:
    """改写挂在末条用户输入的父节点。"""
    _seed_turns()
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    head = str(tree["head"])
    reply = tree["nodes"][head]
    user = tree["nodes"][str(reply["parent_id"])]
    assert _branch_point(_request("revise")) == str(user["parent_id"])


def test_persist_regenerate_replaces_reply_in_place(data_dir: Path) -> None:
    """重新生成原地替换那条回复，节点数不变，也不新增兄弟分支。"""
    _seed_turns()
    request = _request("regenerate")
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    target = str(tree["head"])
    _persist_turn(request, "丙", "戊", None, _branch_point(request))
    after = store.session_tree(SESSION_ID, CHARACTER_ID)
    # 文件里还是四条消息，被替换的那条换了正文
    assert _file_contents() == ["甲", "乙", "丙", "戊"]
    assert str(after["nodes"][target]["content"]) == "戊"
    # 节点数与 head 都不变，树上没有多出兄弟分支
    assert len(after["nodes"]) == len(tree["nodes"])
    assert str(after["head"]) == target
    assert all(len(kids) <= 1 for kids in after["children"].values())
    # 当前路径走到被替换的那条
    assert _path_contents() == ["甲", "乙", "丙", "戊"]


def test_persist_revise_creates_new_user_node(data_dir: Path) -> None:
    """改写新开一条用户输入，与旧输入互为兄弟。"""
    _seed_turns()
    request = _request("revise")
    _persist_turn(request, "丙改", "戊", None, _branch_point(request))
    assert _file_contents() == ["甲", "乙", "丙", "丁", "丙改", "戊"]
    assert _path_contents() == ["甲", "乙", "丙改", "戊"]


def test_split_blocks_keeps_model_order() -> None:
    """块顺序按模型实际写出的顺序返回，没写出来的块排在后面。"""
    text = "【对话】甲乙丙。【场景】丁戊己。【状态】当前位置：地址U"
    blocks = split_blocks(text, STRUCTURE)
    assert list(blocks.keys()) == ["dialogue", "scene", "state"]
    assert blocks["dialogue"] == "甲乙丙。"
    assert blocks["scene"] == "丁戊己。"
    assert blocks["state"] == "当前位置：地址U"


def test_missing_blocks_ignores_optional_blocks() -> None:
    """可选块缺失与状态块没写都不触发重写。"""
    blocks = {"scene": "", "dialogue": "甲", "state": ""}
    assert _missing_blocks(blocks, STRUCTURE) == []


def test_missing_blocks_flags_unparsable_state() -> None:
    """状态块写了但解析不出字段才触发重写。"""
    blocks = {"scene": "甲", "dialogue": "乙", "state": "他站在地址U二楼。"}
    assert _missing_blocks(blocks, STRUCTURE) == ["state"]
