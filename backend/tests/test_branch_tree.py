r"""分支树的形状测试。

树什么时候才会分叉：改写某一轮的用户输入，或定稿某一条角色回复。
同一父节点下因此有两条线，旧的那条留在树上可切回。

重新生成不算分支：它把那条回复原地换掉，不新增节点，因此不算分支。
被换掉的那一轮之后若还有几轮，那些轮次留在树上但不在当前路径上，
继续对话时另起一条分支，于是同一轮出现两个版本。

数据目录一律用 tmp_path + XWORLD_DATA_DIR 隔离，不碰真实用户数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from app import store
from app.contracts import ChatRequest, XwOptions
from app.routers.chat import _branch_point, _persist_turn

CHARACTER_ID = "c_branch"
SESSION_ID = "s_branch"


def _seed_turns(count: int = 2) -> list[str]:
    """写若干轮问答，返回每轮角色回复的节点 id。"""
    parent = ""
    replies: list[str] = []
    for index in range(count):
        user = store.branch_message(
            SESSION_ID,
            CHARACTER_ID,
            parent,
            {"role": "user", "content": f"问{index}"},
        )
        reply = store.branch_message(
            SESSION_ID,
            CHARACTER_ID,
            str(user["id"]),
            {"role": "assistant", "content": f"答{index}"},
        )
        parent = str(reply["id"])
        replies.append(str(reply["id"]))
    return replies


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


def _path_contents() -> list[str]:
    """当前分支路径的正文列表。"""
    return [
        str(row.get("content") or "")
        for row in store.session_path(SESSION_ID, CHARACTER_ID)
    ]


def test_linear_session_tree_is_a_chain(data_dir: Path) -> None:
    """线性会话的树是一条线，每个节点至多一个子节点。"""
    _seed_turns(3)
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    assert all(len(kids) == 1 for kids in tree["children"].values())


def test_regenerate_does_not_add_a_branch(data_dir: Path) -> None:
    """重新生成原地替换那条回复，树上不出现兄弟分支。"""
    replies = _seed_turns(2)
    before = store.session_tree(SESSION_ID, CHARACTER_ID)
    request = _request("regenerate")
    _persist_turn(request, "问1", "新答1", None, _branch_point(request))
    after = store.session_tree(SESSION_ID, CHARACTER_ID)
    assert len(after["nodes"]) == len(before["nodes"])
    assert str(after["nodes"][replies[1]]["content"]) == "新答1"
    assert str(after["nodes"][replies[1]]["parent_id"]) == str(
        before["nodes"][replies[1]]["parent_id"]
    )
    assert all(len(kids) <= 1 for kids in after["children"].values())


def test_regenerate_middle_turn_keeps_old_continuation(data_dir: Path) -> None:
    """重新生成中间那一轮：路径停在那一轮，原来的第三轮仍在树上。"""
    replies = _seed_turns(3)
    request = _request("regenerate", replies[1])
    _persist_turn(request, "问1", "新答1", None, _branch_point(request))
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    # 当前路径停在被重新生成的那一轮
    assert str(tree["head"]) == replies[1]
    assert _path_contents() == ["问0", "答0", "问1", "新答1"]
    # 原来的第三轮仍在树上，仍挂在这条回复下面
    kids = tree["children"][replies[1]]
    assert len(kids) == 1
    assert str(tree["nodes"][kids[0]]["content"]) == "问2"


def test_continuing_after_regenerate_gives_two_thirds(data_dir: Path) -> None:
    """重新生成后继续对话：同一轮出现两个版本，这才是分叉。"""
    replies = _seed_turns(3)
    request = _request("regenerate", replies[1])
    _persist_turn(request, "问1", "新答1", None, _branch_point(request))
    # 继续对话，新的一轮挂在被重新生成那条回复下面
    store.branch_message(
        SESSION_ID,
        CHARACTER_ID,
        replies[1],
        {"role": "user", "content": "新的问2"},
    )
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    kids = tree["children"][replies[1]]
    assert len(kids) == 2
    assert sorted(
        str(tree["nodes"][kid].get("content")) for kid in kids
    ) == ["新的问2", "问2"]


def test_revise_creates_sibling_branch(data_dir: Path) -> None:
    """改写用户输入：新旧两版互为兄弟，树上多出一处分叉。"""
    replies = _seed_turns(2)
    before = store.session_tree(SESSION_ID, CHARACTER_ID)
    user2 = str(before["nodes"][replies[1]]["parent_id"])
    request = _request("revise", user2)
    _persist_turn(request, "改过的问1", "新答1", None, _branch_point(request))
    after = store.session_tree(SESSION_ID, CHARACTER_ID)
    parent = str(before["nodes"][user2]["parent_id"])
    assert len(after["children"][parent]) == 2
    assert _path_contents() == ["问0", "答0", "改过的问1", "新答1"]


def test_revise_first_input_forks_at_root(data_dir: Path) -> None:
    """改写第一条输入：新分支从根分出来，不能接到末尾。

    第一条输入的父节点是根（空串），兜底成 head 会把新分支接到末尾，
    那样就不是分叉而是续写。
    """
    _seed_turns(2)
    before = store.session_tree(SESSION_ID, CHARACTER_ID)
    first_user = ""
    for node in before["nodes"].values():
        if node.get("role") == "user" and not node.get("parent_id"):
            first_user = str(node.get("id") or "")
            break
    request = _request("revise", first_user)
    _persist_turn(request, "改过的问0", "新答0", None, _branch_point(request))
    after = store.session_tree(SESSION_ID, CHARACTER_ID)
    # 根下面有两条线：原来的第一条输入与改过的那条
    roots = after["children"].get("") or []
    assert len(roots) == 2
    # 当前路径只有改过的那一条
    assert _path_contents() == ["改过的问0", "新答0"]


def test_revise_on_middle_turn_keeps_old_line(data_dir: Path) -> None:
    """改写中间那一轮：旧的那条线整条留在树上，可切回。"""
    replies = _seed_turns(3)
    before = store.session_tree(SESSION_ID, CHARACTER_ID)
    user2 = str(before["nodes"][replies[1]]["parent_id"])
    request = _request("revise", user2)
    _persist_turn(request, "改过的问1", "新答1", None, _branch_point(request))
    after = store.session_tree(SESSION_ID, CHARACTER_ID)
    parent = str(before["nodes"][user2]["parent_id"])
    kids = after["children"][parent]
    assert len(kids) == 2
    # 旧的那条线仍带着原来的第三轮
    old = [
        kid
        for kid in kids
        if str(after["nodes"][kid].get("content")) == "问1"
    ]
    assert len(old) == 1
    assert len(after["children"][str(old[0])]) == 1


def test_branch_point_follows_target_node(data_dir: Path) -> None:
    """分支点取被改写那条消息的父节点。"""
    replies = _seed_turns(3)
    tree = store.session_tree(SESSION_ID, CHARACTER_ID)
    request = _request("regenerate", replies[1])
    assert _branch_point(request) == str(tree["nodes"][replies[1]]["parent_id"])
