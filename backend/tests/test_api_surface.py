"""接口表面：契约文档里写的路径必须真的能通。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

COLLECTION_PATHS = [
    "/v1/cards",
    "/v1/memory",
    "/v1/plugins",
    "/v1/presets",
    "/v1/sessions",
    "/v1/settings",
    "/v1/state",
    "/v1/world-books",
]


def test_collection_paths_accept_no_trailing_slash() -> None:
    """集合端点不带尾斜杠也能访问：契约里写的就是这种写法。"""
    with TestClient(create_app()) as client:
        for path in COLLECTION_PATHS:
            bare = client.get(path)
            slashed = client.get(path + "/")
            assert bare.status_code == 200, (path, bare.status_code, bare.text[:200])
            assert bare.status_code == slashed.status_code, path


def test_unknown_api_path_still_404() -> None:
    """没登记的接口仍然是 404，别被斜杠改写带偏。"""
    with TestClient(create_app()) as client:
        assert client.get("/v1/not-a-real-endpoint").status_code == 404
        assert client.get("/v1/state-not-real").status_code == 404


def test_nested_paths_are_not_rewritten() -> None:
    """带参数的子路径保持原样，斜杠改写只作用于集合前缀本身。"""
    with TestClient(create_app()) as client:
        response = client.get("/v1/state/")
        assert response.status_code == 200
        assert client.get("/v1/world-books/w_missing/regions").status_code == 404
