"""参数 profile 路由。

GET  /v1/profiles — 列出所有 profile
POST /v1/profiles — 创建或更新 profile
GET  /v1/profiles/{profile_id} — 获取单个 profile

数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import store
from app.contracts import Profile
from app.errors import ApiError

router = APIRouter()


@router.get("")
def list_profiles() -> list[Profile]:
    """列出所有 profile。"""
    profiles: list[Profile] = []
    for item in store.list_profiles():
        profiles.append(
            Profile(
                id=str(item.get("id", "")),
                name=str(item.get("name", item.get("id", ""))),
                params=item.get("params") if isinstance(item.get("params"), dict) else {},
            )
        )
    return profiles


@router.post("")
async def save_profile(request: Request) -> dict:
    """创建或更新 profile。

    请求体：{"id": "...", "name": "...", "params": {...}}
    若不提供 id 则自动生成。
    """
    body = await request.json()
    profile_id = body.get("id") or store.new_id("pf")
    name = body.get("name", "未命名")
    params = body.get("params", {})

    saved = store.save_profile({"id": profile_id, "name": name, "params": params})

    return {
        "ok": True,
        "id": saved["id"],
        "name": saved["name"],
        "params": saved.get("params", {}),
    }


@router.get("/{profile_id}")
def get_profile(profile_id: str) -> Profile:
    """获取单个 profile。"""
    item = store.get_profile(profile_id)
    if not isinstance(item, dict):
        raise ApiError(
            code="profile_not_found",
            message=f"Profile {profile_id} 不存在",
            retryable=False,
            status_code=404,
        )
    return Profile(
        id=str(item.get("id", profile_id)),
        name=str(item.get("name", profile_id)),
        params=item.get("params") if isinstance(item.get("params"), dict) else {},
    )
