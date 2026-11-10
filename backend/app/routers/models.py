"""模型管理路由。

GET /v1/models — 模型清单
POST /v1/models/active — 切换激活模型
GET /v1/inference/tiers — 模型档位清单与运行状态
POST /v1/inference/start — 按档位拉起推理服务并切换
POST /v1/inference/stop — 停掉托管中的推理服务
GET /v1/tokenizers — 分词器清单与探测结果

数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app import store
from app.assembly.tokenizer import ApproxTokenizer, LlamaCppTokenizer
from app.config import models_dir
from app.contracts import ModelInfo
from app.errors import ApiError

router = APIRouter()

MODEL_SUFFIXES = (".gguf", ".bin", ".pt", ".safetensors")


@router.get("/models")
def list_models() -> list[ModelInfo]:
    """返回模型清单，从本地模型目录与 settings 读取。"""
    models: list[ModelInfo] = []

    md = models_dir()
    if md.exists():
        for entry in sorted(md.iterdir()):
            if entry.is_file() and entry.suffix in MODEL_SUFFIXES:
                models.append(
                    ModelInfo(
                        id=entry.stem,
                        label=entry.stem,
                        provider="local",
                        context_length=16384,
                        supports_reasoning=False,
                    )
                )

    stored = store.get_setting("models")
    if isinstance(stored, list):
        for item in stored:
            if isinstance(item, dict):
                models.append(
                    ModelInfo(
                        id=item.get("id", ""),
                        label=item.get("label", item.get("id", "")),
                        provider=item.get("provider", "unknown"),
                        context_length=item.get("context_length", 8192),
                        supports_reasoning=item.get("supports_reasoning", False),
                    )
                )

    if not models:
        models.append(
            ModelInfo(
                id="xw-local-default",
                label="本地默认模型",
                provider="llamacpp",
                context_length=16384,
                supports_reasoning=False,
            )
        )

    return models


@router.post("/models/active")
async def set_active_model(request: Request) -> dict:
    """切换激活模型与 profile。

    请求体：{"model_id": "...", "profile_id": "..."}
    """
    body = await request.json()
    model_id = body.get("model_id", "")
    profile_id = body.get("profile_id", "")

    store.set_setting(
        "active_model",
        {"model_id": model_id, "profile_id": profile_id},
    )

    return {"ok": True, "model_id": model_id, "profile_id": profile_id}


@router.get("/inference/status")
async def inference_status() -> dict:
    """推理服务状态与当前生效的模型档位。

    服务探测走 /v1/models 与分词计数端点（自写服务端是 /tokenize），
    不可达时返回 reachable=false 与错误原因，不抛异常。
    """
    from app.inference.service import probe_service
    from app.inference.traits import resolve_profile

    profile = resolve_profile()
    service = await probe_service()
    return {"service": service, "profile": profile.to_dict()}


@router.get("/inference/tiers")
async def inference_tiers() -> dict:
    """模型档位清单：内置档位与自建 profile，带运行状态与实测数据。"""
    from app.inference.launcher import tiers

    return {"items": tiers()}


@router.post("/inference/start")
async def start_inference(request: Request) -> dict:
    """按档位拉起本地推理服务并把它设为生效地址。

    请求体：{"profile_id": "fast", "replace": true}
    replace 为真时先停掉在跑的其它档位：8GB 显存装不下两个大模型并存。
    """
    from app.inference.launcher import start as start_service

    body = await request.json()
    profile_id = str(body.get("profile_id") or "").strip()
    if not profile_id:
        raise ApiError(code="profile_required", message="缺少 profile_id")
    replace = bool(body.get("replace", True))
    try:
        service = await run_in_threadpool(start_service, profile_id, replace)
    except FileNotFoundError as exc:
        raise ApiError(code="model_missing", message=str(exc), status_code=400)
    except RuntimeError as exc:
        raise ApiError(code="inference_start_failed", message=str(exc), retryable=True)
    return {"ok": True, "service": service}


@router.post("/inference/model")
async def switch_inference_model(request: Request) -> dict:
    """在运行的推理服务里原地换模型。

    请求体：{"profile_id": "fast"}，也可直接给 {"model_path": "<权重文件的绝对路径>"}。

    推理端只有自写服务端一套，原地重载权重，不重启进程。
    """
    from app.inference.service import switch_model
    from app.inference.traits import resolve_profile

    body = await request.json()
    profile_id = str(body.get("profile_id") or "").strip()
    model_path = str(body.get("model_path") or "").strip()
    if not profile_id and not model_path:
        raise ApiError(code="profile_required", message="缺少 profile_id 或 model_path")
    if not model_path:
        profile = resolve_profile(profile_id)
        model_path = profile.model_path

    result = await switch_model(model_path)
    if result.get("ok"):
        return {"ok": True, "mode": "reload", "model": result.get("model", "")}
    raise ApiError(
        code="model_switch_failed",
        message=str(result.get("error") or "换模型失败"),
        retryable=True,
    )


@router.post("/inference/stop")
async def stop_inference(request: Request) -> dict:
    """停掉托管中的推理服务，不给 profile_id 就全停。"""
    from app.inference.launcher import stop as stop_service

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 空请求体按全停处理
        body = {}
    profile_id = str(body.get("profile_id") or "").strip() or None
    result = await run_in_threadpool(stop_service, profile_id)
    return {"ok": True, **result}


@router.get("/tokenizers")
def list_tokenizers() -> dict:
    """返回分词器清单与探测结果。"""
    approx = ApproxTokenizer()
    llama = LlamaCppTokenizer()

    tokens = llama.count("你好 world")
    llama_available = not llama.approximate

    return {
        "tokenizers": [
            {
                "name": approx.name,
                "available": True,
                "approximate": True,
                "description": "字符启发式估算分词器",
            },
            {
                "name": llama.name,
                "available": llama_available,
                "approximate": llama.approximate,
                "description": "llama.cpp server 真实分词器",
            },
        ],
        "default": "approx",
        "probe_result": {
            "llamacpp_available": llama_available,
            "test_text": "你好 world",
            "approx_count": approx.count("你好 world"),
            "llamacpp_count": tokens,
        },
    }
