# -*- coding: utf-8 -*-
"""Role voice vector: configuration lookup and request attachment.

The vector belongs to a character: the backend looks it up by character id
and hands it to the inference server with the request. A character without a
vector must not get one, and the vector must not leak to other characters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import store, voice
from app.contracts import ChatRequest
from app.routers import chat as chat_router


def _write_vector(root: Path, name: str = "voice.f32") -> Path:
    """Write a dummy vector file into the data root's vectors directory."""
    path = voice.vectors_dir() / name
    path.write_bytes(b"\x00" * 16)
    return path


def test_character_without_voice_has_no_config(data_dir: Path) -> None:
    """A character with no vector configured resolves to no config."""
    assert voice.voice_config("c_nobody") is None


def test_set_voice_config_resolves_file_under_data_root(data_dir: Path) -> None:
    """The configured file name resolves to an absolute path in the data root."""
    _write_vector(data_dir)
    saved = voice.set_voice_config("c_a", "voice.f32", 0.35)
    assert saved["scale"] == 0.35
    assert saved["path"] == str(voice.vectors_dir() / "voice.f32")
    assert Path(saved["path"]).is_file()
    assert voice.voice_config("c_a") == saved


def test_missing_vector_file_resolves_to_none(data_dir: Path) -> None:
    """A configured but absent file gives no config, so the turn runs plain."""
    voice.set_voice_config("c_a", "gone.f32", 0.35)
    assert voice.voice_config("c_a") is None


def test_scale_is_clamped_to_the_usable_range(data_dir: Path) -> None:
    """A too strong dose is pulled back into the range that keeps the format."""
    _write_vector(data_dir)
    saved = voice.set_voice_config("c_a", "voice.f32", 5.0)
    assert saved["scale"] == voice.MAX_SCALE


def test_clear_voice_config_removes_the_entry(data_dir: Path) -> None:
    """Clearing takes the character back to no vector."""
    _write_vector(data_dir)
    voice.set_voice_config("c_a", "voice.f32", 0.35)
    assert voice.clear_voice_config("c_a") is True
    assert voice.voice_config("c_a") is None
    assert voice.clear_voice_config("c_a") is False


def test_chat_params_carry_the_character_vector(data_dir: Path) -> None:
    """The speaking character's vector rides along with the inference request."""
    _write_vector(data_dir)
    voice.set_voice_config("c_a", "voice.f32", 0.35)
    request = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        xw={"character_id": "c_a"},
    )
    params = chat_router._build_params(request)
    control = params.extra.get("control_vector")
    assert control is not None
    assert control["scale"] == 0.35
    assert control["path"].endswith("voice.f32")


def test_chat_params_skip_characters_without_a_vector(data_dir: Path) -> None:
    """A character without a vector gets no control vector in the request."""
    request = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        xw={"character_id": "c_b"},
    )
    params = chat_router._build_params(request)
    assert "control_vector" not in params.extra


def test_chat_params_find_the_character_by_session_scope(data_dir: Path) -> None:
    """Without character_id the session scope still identifies the speaker."""
    _write_vector(data_dir)
    voice.set_voice_config("c_a", "voice.f32", 0.35)
    store.ensure_session("s_voice", "c_a")
    request = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        session="s_voice",
    )
    params = chat_router._build_params(request)
    control = params.extra.get("control_vector")
    assert control is not None
    assert control["scale"] == 0.35
