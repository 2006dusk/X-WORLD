"""The self reference gate: a card declares how the character speaks.

The imperial self reference slips into palace scenes because the scene prior
and the user's address both push the character towards being the emperor.
The prompt layer never fixed it, so the pipeline judges it and rewrites.
"""

from __future__ import annotations

from app.contracts import ChatMessage
from app.routers import chat as chat_router


def test_declared_self_reference_that_slipped_is_caught() -> None:
    """A card declaring "我" and a line using "朕" is a violation."""
    blocks = {"dialogue": "今日？朕批的红墨未干。", "action": "她抬眼。"}
    assert chat_router._self_reference_violation(blocks, ["action", "dialogue"], "我") is True


def test_declared_self_reference_in_use_passes() -> None:
    """The declared self reference is not a violation."""
    blocks = {"dialogue": "今日？我这里都好。", "action": "她抬眼。"}
    assert chat_router._self_reference_violation(blocks, ["action", "dialogue"], "我") is False


def test_imperial_self_reference_is_not_a_violation_when_declared() -> None:
    """A card that declares "朕" is allowed to use it."""
    blocks = {"dialogue": "今日？朕在此。", "action": "她抬眼。"}
    assert chat_router._self_reference_violation(blocks, ["action", "dialogue"], "朕") is False


def test_no_declared_self_reference_is_not_judged() -> None:
    """Without a declaration there is nothing to judge against."""
    blocks = {"dialogue": "今日？朕在此。", "action": "她抬眼。"}
    assert chat_router._self_reference_violation(blocks, ["action", "dialogue"], "") is False


def test_narration_self_reference_is_not_judged() -> None:
    """Only the dialogue counts: narration never speaks in the first person."""
    blocks = {"dialogue": "今日可好。", "action": "她想起朕从前说过的话。"}
    assert chat_router._self_reference_violation(blocks, ["action", "dialogue"], "我") is False


def test_rewrite_note_reaches_the_user_turn() -> None:
    """The rewrite instruction is appended to the user turn, next to the prompt."""
    messages = [ChatMessage(role="system", content="设定"), ChatMessage(role="user", content="在吗")]
    updated = chat_router._with_self_reference_note(messages)
    assert updated[-1].role == "user"
    assert "在吗" in updated[-1].content
    assert "自称" in updated[-1].content
    # the original list is untouched
    assert messages[-1].content == "在吗"
