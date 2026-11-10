"""块归位测试。

模型漏写【对话】标记时，台词会跟在动作或场景后面。整块当叙述，
气泡里就是一大段旁白：谁在说话分不清，界面看着像角色自说自话。
解析层把台词挪回对白块，内容不丢，只改归属。
"""

from __future__ import annotations

from app.routers.chat import _normalize_blocks

STRUCTURE = ["action", "scene", "dialogue", "state"]


def test_inline_dialogue_moves_to_dialogue_block() -> None:
    """台词跟在动作后面时挪回对白块。"""
    blocks = {
        "action": "她轻勾门帘，两名宫女鱼贯而入。\n\n\u201c陛下莫要惊慌。\u201d",
        "scene": "",
        "dialogue": "",
        "state": "当前位置：地址A",
    }
    result = _normalize_blocks(blocks, STRUCTURE)
    assert "鱼贯而入" in result["action"]
    assert "\u201c陛下莫要惊慌。\u201d" in result["dialogue"]
    assert "鱼贯而入" not in result["dialogue"]


def test_inline_dialogue_at_block_start_moves_to_dialogue_block() -> None:
    """整块都是台词时全部挪进对白块，动作块留空。"""
    blocks = {
        "action": "\u201c陛下莫要惊慌。\u201d她轻笑。",
        "scene": "",
        "dialogue": "",
        "state": "",
    }
    result = _normalize_blocks(blocks, STRUCTURE)
    assert result["action"] == ""
    assert "\u201c陛下莫要惊慌。\u201d" in result["dialogue"]


def test_quoted_term_stays_in_action() -> None:
    """引号里只是个词时不挪走。"""
    blocks = {
        "action": "她拿起那枚\u300c金线小锁\u300d，缓缓扣上。",
        "scene": "",
        "dialogue": "",
        "state": "",
    }
    result = _normalize_blocks(blocks, STRUCTURE)
    assert result["action"] == blocks["action"]
    assert result["dialogue"] == ""


def test_existing_dialogue_block_is_kept() -> None:
    """已经有对白块时不动。"""
    blocks = {
        "action": "她轻勾门帘。",
        "scene": "",
        "dialogue": "\u201c走吧。\u201d",
        "state": "",
    }
    result = _normalize_blocks(blocks, STRUCTURE)
    assert result["dialogue"] == "\u201c走吧。\u201d"
    assert result["action"] == "她轻勾门帘。"
