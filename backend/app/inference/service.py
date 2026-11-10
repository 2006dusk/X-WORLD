"""推理服务状态探测。

参考 StarHub 的 core/environment_checker.py 与 services/system_monitor.py 的做法：
把「服务是不是活着、能不能用」变成一个可查询的端点，而不是让用户从报错里猜。

探测走自写服务端实际存在的端点：
- GET /v1/models 判断服务是否可达；
- POST /tokenize（{"content": 文本}）取真实分词计数；
- GET /props 报 build_info、n_embd 与 n_layers：判断跑的是不是它，
  以及控制向量该多大。
"""

from __future__ import annotations

from typing import Any

import httpx

PROBE_TEXT = "你好 world"


def base_url() -> str:
    """推理服务地址。取 store 的 settings，缺省 http://127.0.0.1:8080。"""
    from app import store
    from app.config import HOST

    try:
        value = store.get_setting("inference_url")
        if isinstance(value, dict):
            url = value.get("url")
            if isinstance(url, str) and url:
                return url.rstrip("/")
        elif isinstance(value, str) and value:
            return value.rstrip("/")
    except Exception:  # noqa: BLE001 - 读设置失败按缺省地址处理
        pass
    return f"http://{HOST}:8080"


async def server_kind(url: str | None = None, timeout: float = 5.0) -> str:
    """在跑的推理服务是哪一种。

    自写服务端在 /props 里报 build_info；探测失败按未知处理，
    调用方自己决定退路。
    """
    target = (url or base_url()).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{target}/props")
            if resp.status_code != 200:
                return "unknown"
            payload = resp.json()
    except Exception:  # noqa: BLE001 - 探测失败按未知处理
        return "unknown"
    if not isinstance(payload, dict):
        return "unknown"
    build = str(payload.get("build_info") or "")
    if "world-work-cpp" in build:
        return "world-work-cpp"
    return "unknown"


async def server_props(url: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """读推理端的 /props。

    自写服务端在 /props 里报 n_embd、n_layers 与模型描述：控制向量是按模型尺寸
    做的，配置声线前要先知道当前模型要多大字节的向量。端点不可达时返回空字典。
    """
    target = (url or base_url()).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{target}/props")
            if resp.status_code != 200:
                return {}
            payload = resp.json()
    except Exception:  # noqa: BLE001 - 探测失败按未知处理
        return {}
    return payload if isinstance(payload, dict) else {}


async def switch_model(
    path: str,
    url: str | None = None,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """让在跑的推理服务原地换一个模型文件。

    自写服务端支持原地重载权重，不重启进程。
    返回 {"ok": bool, "model": str, "error": str | None}。
    """
    target = (url or base_url()).rstrip("/")
    result: dict[str, Any] = {"ok": False, "model": "", "error": None}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{target}/v1/model", json={"path": path})
            if resp.status_code != 200:
                result["error"] = f"服务返回 {resp.status_code}: {resp.text[:200]}"
                return result
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - 换模型失败统一收敛成字段
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if isinstance(payload, dict):
        result["ok"] = str(payload.get("status") or "") == "ok"
        result["model"] = str(payload.get("model_desc") or "")
        result["error"] = payload.get("error")
    return result


async def generate_voice(
    spec: dict[str, Any],
    save_path: str,
    url: str | None = None,
    timeout: float = 1800.0,
) -> dict[str, Any]:
    """让推理端按角色设定现场抽一条声线向量。

    推理端在同一个进程里做这件事：同一段提示跑两遍，一遍带角色声线、
    一遍带中性助手，比较回答段的残差流，再按残差尺度归一。向量属于当前
    加载的模型，所以必须在服务的模型上抽，不能换进程换模型。
    """
    target = (url or base_url()).rstrip("/")
    result: dict[str, Any] = {"ok": False, "error": None, "path": save_path}
    payload = dict(spec)
    payload["save_path"] = save_path
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{target}/v1/voice", json=payload)
            if resp.status_code != 200:
                result["error"] = f"服务返回 {resp.status_code}: {resp.text[:200]}"
                return result
            body = resp.json()
    except Exception as exc:  # noqa: BLE001 - 调用失败统一收敛成字段
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if isinstance(body, dict):
        result["ok"] = str(body.get("status") or "") == "ok"
        result["error"] = body.get("error")
        for key, value in body.items():
            if key not in ("error", "status"):
                result[key] = value
    return result


async def verify_voice(
    spec: dict[str, Any],
    vector_path: str,
    scale: float,
    url: str | None = None,
    timeout: float = 1800.0,
) -> dict[str, Any]:
    """判定一条声线向量是不是这个角色的声线。

    判据是似然，不是裁判模型的印象：角色自己的原话在挂上向量之后要更可能
    出现，而中性助手的回答不能跟着变容易。后者是把所有句子一起抬高，
    那只是通用偏移，不是这个角色的声线。
    """
    target = (url or base_url()).rstrip("/")
    result: dict[str, Any] = {"ok": False, "error": None, "verdict": "unknown"}
    payload = dict(spec)
    payload["vector_path"] = vector_path
    payload["scale"] = scale
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{target}/v1/voice/verify", json=payload)
            if resp.status_code != 200:
                result["error"] = f"服务返回 {resp.status_code}: {resp.text[:200]}"
                return result
            body = resp.json()
    except Exception as exc:  # noqa: BLE001 - 调用失败统一收敛成字段
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if isinstance(body, dict):
        result["ok"] = str(body.get("status") or "") == "ok"
        result["error"] = body.get("error")
        for key, value in body.items():
            if key not in ("error", "status"):
                result[key] = value
    return result


async def probe_service(
    url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """探测推理服务，返回可达性、已加载模型与分词器计数。"""
    target = (url or base_url()).rstrip("/")
    result: dict[str, Any] = {
        "reachable": False,
        "base_url": target,
        "models": [],
        "tokenizer_count": None,
        "error": None,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{target}/v1/models")
            if resp.status_code != 200:
                result["error"] = f"服务返回 {resp.status_code}"
                return result
            payload = resp.json()
            entries = payload.get("data") if isinstance(payload, dict) else None
            result["models"] = [
                str(item.get("id"))
                for item in (entries or [])
                if isinstance(item, dict) and item.get("id")
            ]
            result["reachable"] = True

            try:
                token_resp = await client.post(
                    f"{target}/tokenize",
                    json={"content": PROBE_TEXT},
                )
                if token_resp.status_code == 200:
                    body = token_resp.json()
                    if isinstance(body, dict):
                        result["tokenizer_count"] = body.get("count")
            except httpx.HTTPError:
                result["tokenizer_count"] = None
    except Exception as exc:  # noqa: BLE001 - 探测失败统一收敛成字段
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result
