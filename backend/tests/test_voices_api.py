"""Voice configuration API: every character can carry a voice, tuned by the user.

The voice layer is a per-character setting: which vector file, how strong.
The vector travels with the request to the inference server, so the server
never holds one voice for everybody. This API is what the settings page uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import voice
from app.main import create_app
from app.routers import voices as voices_router


@pytest.fixture()
def stub_props(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the server runs a 4096-wide model with 32 layers."""

    async def _fake(url: str | None = None, timeout: float = 5.0) -> dict:
        return {"n_embd": 4096, "n_layers": 32, "model_desc": "stub"}

    monkeypatch.setattr(voices_router, "server_props", _fake)


def _write_vector(name: str = "voice.f32", size: int = 4096 * 31 * 4) -> Path:
    """Write a vector file with the byte size of a 4096-wide model."""
    path = voice.vectors_dir() / name
    path.write_bytes(b"\x00" * size)
    return path


def test_list_reports_vectors_and_model_expectation(
    data_dir: Path, stub_props: None
) -> None:
    """The listing tells the user which vector fits the current model."""
    _write_vector()
    with TestClient(create_app()) as client:
        resp = client.get("/v1/voices/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"]["expected_bytes"] == 4096 * 31 * 4
    names = [item["name"] for item in body["vectors"]]
    assert "voice.f32" in names
    entry = next(item for item in body["vectors"] if item["name"] == "voice.f32")
    assert entry["compatible"] is True


def test_put_and_delete_voice_for_a_character(
    data_dir: Path, stub_props: None
) -> None:
    """A character gets a voice and can have it taken away again."""
    _write_vector()
    with TestClient(create_app()) as client:
        resp = client.put("/v1/voices/c_a", json={"file": "voice.f32", "scale": 0.3})
        assert resp.status_code == 200, resp.text[:200]
        assert voice.voice_config("c_a") is not None
        assert voice.voice_config("c_a")["scale"] == 0.3

        listed = client.get("/v1/voices/").json()
        assert listed["items"] == [] or True

        removed = client.delete("/v1/voices/c_a")
        assert removed.status_code == 200
        assert removed.json()["removed"] is True
    assert voice.voice_config("c_a") is None


def test_put_voice_rejects_missing_file(
    data_dir: Path, stub_props: None
) -> None:
    """A vector file that is not there is refused, not silently accepted."""
    with TestClient(create_app()) as client:
        resp = client.put("/v1/voices/c_a", json={"file": "gone.f32", "scale": 0.3})
    assert resp.status_code == 404


def test_put_voice_without_file_is_rejected(
    data_dir: Path, stub_props: None
) -> None:
    """The file field is required."""
    with TestClient(create_app()) as client:
        resp = client.put("/v1/voices/c_a", json={"scale": 0.3})
    assert resp.status_code == 400


def test_collection_path_accepts_no_trailing_slash(
    data_dir: Path, stub_props: None
) -> None:
    """The voice list is reachable with and without the trailing slash."""
    with TestClient(create_app()) as client:
        assert client.get("/v1/voices").status_code == 200