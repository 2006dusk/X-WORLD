"""装配预览路由。

GET /v1/prompt-preview — 预览装配结果。

数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.assembly.pipeline import assemble
from app.contracts import (
    ChatMessage,
    ChatRequest,
    PreviewMessage,
    PromptPreview,
    XwOptions,
)

router = APIRouter()

MAX_CONTENT_PREVIEW = 200


@router.get("/prompt-preview")
def prompt_preview(
    session: str | None = Query(None),
    character_id: str | None = Query(None),
    preset_id: str | None = Query(None),
    world_book_ids: str | None = Query(None),
    region_id: str | None = Query(None),
    user_input: str = Query(""),
) -> dict:
    """预览装配结果。

    返回 context_report 同构对象 + messages 预览数组。
    """
    wb_ids: list[str] = []
    if world_book_ids:
        wb_ids = [w.strip() for w in world_book_ids.split(",") if w.strip()]

    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_input)],
        session=session,
        stream=False,
        xw=XwOptions(
            character_id=character_id,
            preset_id=preset_id,
            world_book_ids=wb_ids,
            region_id=region_id,
            memory_mode="hybrid",
            max_context_tokens=16384,
        ),
    )

    ctx = assemble(request)

    preview_messages: list[PreviewMessage] = []
    for msg in ctx.messages:
        content = msg.content
        if len(content) > MAX_CONTENT_PREVIEW:
            content = content[:MAX_CONTENT_PREVIEW] + "..."
        source = _guess_source(msg)
        preview_messages.append(
            PreviewMessage(role=msg.role, content=content, source=source)
        )

    preview = PromptPreview(
        segments=ctx.report.segments,
        template=ctx.report.template,
        tokenizer=ctx.report.tokenizer,
        truncated=ctx.report.truncated,
        messages=preview_messages,
    )
    return preview.model_dump()


def _guess_source(msg: ChatMessage) -> str:
    """根据消息角色推测来源段。"""
    if msg.role == "system":
        return "system/character/preset"
    elif msg.role == "user":
        return "history/user"
    elif msg.role == "assistant":
        return "history"
    return "unknown"
