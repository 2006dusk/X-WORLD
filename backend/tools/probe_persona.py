r"""用户档案段探针：设置 persona 后装配里是否多出这一段。

走真实接口：PUT /v1/settings/persona、GET /v1/prompt-preview。
探针结束后恢复原设置，不留痕迹。

用法：
    python backend\tools\probe_persona.py
"""

from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8760"
CHARACTER_ID = "c_demo"
PERSONA = "叫阿岚，二十六岁，进地址U是为了找失踪的妹妹。"

KEYS = ("system", "character", "persona", "preset_prompts", "post_history",
        "world_info", "memory", "history", "user")


def get(path: str, params: dict | None = None) -> dict:
    """读一个接口。"""
    resp = httpx.get(BASE + path, params=params or {}, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def put_setting(key: str, value: str) -> dict:
    """写设置。"""
    resp = httpx.put(BASE + "/v1/settings/" + key, json={"value": value}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _assembled_system_text() -> str:
    """进程内跑一次装配，取完整的系统消息。"""
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(backend_root))
    from app.assembly.pipeline import assemble
    from app.contracts import ChatMessage, ChatRequest, XwOptions

    request = ChatRequest(
        messages=[ChatMessage(role="user", content="我把磁带放在调音台上")],
        stream=False,
        xw=XwOptions(character_id=CHARACTER_ID),
    )
    ctx = assemble(request)
    return "\n".join(msg.content for msg in ctx.messages if msg.role == "system")


def preview() -> dict:
    """装配预览。"""
    return get(
        "/v1/prompt-preview",
        {"character_id": CHARACTER_ID, "user_input": "我把磁带放在调音台上"},
    )


def main() -> int:
    """验证 persona 段。"""
    try:
        original = get("/v1/settings/persona").get("value")
    except httpx.HTTPStatusError:
        original = ""
    failures = 0
    try:
        put_setting("persona", "")
        before = preview()
        names_before = [seg.get("name") for seg in before.get("segments") or []]
        print("未设置时：段数 %d，persona 段存在 %s" % (len(names_before), "persona" in names_before))
        if "persona" not in names_before:
            print("    失败：persona 段缺失")
            failures += 1

        put_setting("persona", PERSONA)
        after = preview()
        names = [seg.get("name") for seg in after.get("segments") or []]
        persona_seg = [seg for seg in after.get("segments") or [] if seg.get("name") == "persona"]
        # 预览接口把每条消息截到 200 字，档案在角色卡之后会被截掉，
        # 这里直接在进程内跑一次装配，看完整的系统消息。
        system_text = _assembled_system_text()
        print("设置后：段数 %d，顺序 %s" % (len(names), names))
        print("persona 段 token 数：%s" % (persona_seg[0].get("tokens") if persona_seg else "无"))
        print("系统消息含档案：%s" % ("阿岚" in system_text))
        if names != list(KEYS):
            print("    失败：段顺序不对")
            failures += 1
        if not persona_seg or not persona_seg[0].get("tokens"):
            print("    失败：persona 段没有内容")
            failures += 1
        if "阿岚" not in system_text:
            print("    失败：系统消息里没有用户档案")
            failures += 1
    finally:
        put_setting("persona", original if isinstance(original, str) else "")
    print("\n失败项：%d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
