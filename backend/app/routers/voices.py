"""角色声线配置 HTTP 路由。

声线是角色的声线层：每个角色可以挂一个控制向量文件与剂量。
向量随请求走（app/voice.py 的 character_voice 映射），推理端按请求挂载，
换角色就换向量，服务端全局不挂。

向量文件放在数据根的 vectors 目录下。向量是按模型尺寸做的：
当前模型要 n_embd × (n_layers - 1) × 4 字节，对不上的向量会被推理端跳过，
所以清单里直接把兼容情况标出来。

数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import conformance, voice
from app.cards import loader
from app.errors import ApiError
from app.inference import service
from app.inference.service import server_props

router = APIRouter(tags=["voices"])

# 运行时生成后要扫的剂量。0.65 以上模型会脱轨，所以上限留在 0.5。
DOSE_SCALES = (0.2, 0.35, 0.5)

VECTOR_SUFFIX = ".f32"


def _expected_bytes(props: dict[str, Any]) -> int | None:
    """当前模型要的向量字节数。取不到模型尺寸时返回 None。"""
    try:
        n_embd = int(props.get("n_embd") or 0)
        n_layers = int(props.get("n_layers") or 0)
    except (TypeError, ValueError):
        return None
    if n_embd <= 0 or n_layers <= 0:
        return None
    return n_embd * (n_layers - 1) * 4


def _vector_files(expected: int | None) -> list[dict[str, Any]]:
    """数据根 vectors 目录里的向量文件与兼容情况。"""
    items: list[dict[str, Any]] = []
    directory = voice.vectors_dir()
    for path in sorted(directory.glob(f"*{VECTOR_SUFFIX}")):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        items.append(
            {
                "name": path.name,
                "bytes": size,
                "compatible": (size == expected) if expected else None,
            }
        )
    return items


def _character_items() -> list[dict[str, Any]]:
    """角色卡与各自的声线配置。"""
    items: list[dict[str, Any]] = []
    for card in loader.list_cards():
        cid = str(card.get("id") or "")
        if not cid:
            continue
        config = voice.voice_config(cid)
        items.append(
            {
                "character_id": cid,
                "name": str(card.get("name") or cid),
                "role": str(card.get("role") or ""),
                "file": str(config.get("path")) if config else "",
                "scale": float(config.get("scale")) if config else 0.0,
                "layer_start": int(config.get("layer_start")) if config else 1,
                "layer_end": int(config.get("layer_end")) if config else -1,
                "active": config is not None,
            }
        )
    return items


@router.get("/")
async def list_voices() -> JSONResponse:
    """列出角色声线配置与可用的向量文件。"""
    props = await server_props()
    expected = _expected_bytes(props)
    return JSONResponse(
        content={
            "items": _character_items(),
            "vectors": _vector_files(expected),
            "model": {
                "model_desc": str(props.get("model_desc") or ""),
                "n_embd": props.get("n_embd"),
                "n_layers": props.get("n_layers"),
                "expected_bytes": expected,
            },
            "max_scale": voice.MAX_SCALE,
        }
    )


@router.put("/{character_id}")
def put_voice(character_id: str, payload: dict[str, Any]) -> JSONResponse:
    """给角色挂一个声线向量。file 是 vectors 目录下的文件名或绝对路径。"""
    name = str(payload.get("file") or "").strip()
    if not name:
        raise ApiError(code="voice_invalid", message="缺少 file 字段", status_code=400)
    path = voice.resolve_path(name)
    if not path or not Path(path).is_file():
        raise ApiError(code="voice_not_found", message="向量文件不存在", status_code=404)
    try:
        scale = float(payload.get("scale", voice.MAX_SCALE))
    except (TypeError, ValueError):
        raise ApiError(code="voice_invalid", message="scale 需要数值", status_code=400)
    config = voice.set_voice_config(character_id, name, voice.clamp_scale(scale))
    return JSONResponse(content={"ok": True, "character_id": character_id, "config": config})


@router.delete("/{character_id}")
def remove_voice(character_id: str) -> JSONResponse:
    """摘掉角色的声线向量。"""
    removed = voice.clear_voice_config(character_id)
    return JSONResponse(content={"ok": True, "character_id": character_id, "removed": removed})

def _voice_spec(card: dict[str, Any]) -> dict[str, Any]:
    """把角色卡变成推理端要的声线规格。

    参数：
        card: 角色卡内部结构。

    返回：
        推理端 /v1/voice 与 /v1/voice/verify 共用的规格。
    """
    data = card.get("data") if isinstance(card.get("data"), dict) else {}
    return {
        "name": str(card.get("name") or card.get("id") or ""),
        "persona": voice.card_persona(data),
        "neutral_persona": voice.NEUTRAL_PERSONA,
        "pairs": voice.voice_samples(data),
        "repeat": 2,
        "max_tokens": 96,
        "capture_ctx": 2048,
    }


def _declared_self_reference(data: dict[str, Any]) -> str:
    """卡里声明的自称。没有就取说话方式层里的那一句。

    与 chat 路由读自称的方式一致：self_reference 字段优先，
    编辑器丢掉它时说话方式层里的「她自称「X」」仍然管用。
    """
    value = str(data.get("self_reference") or "").strip()
    if value:
        return value
    speech = str(data.get("speech") or "")
    match = re.search(r"自称[：: ]*[\u300c\"']([^\u300c\u300d\"']{1,4})[\u300c\u300d\"']", speech)
    return match.group(1) if match else ""

def _judge_spec(card: dict[str, Any]) -> dict[str, Any]:
    """判定用的规格：只用角色卡里的原话。

    生成与判定用不同的语料：生成要样本多，判定要标尺准。拿模型自己生成的
    句子量自己的声线，两边都会显得「像」，判不出东西。
    """
    spec = _voice_spec(card)
    data = card.get("data") if isinstance(card.get("data"), dict) else {}
    judge = voice.voice_judge_samples(data)
    if judge:
        spec["pairs"] = judge
        spec["repeat"] = 1
    return spec


@router.post("/generate")
async def generate_voice(payload: dict[str, Any]) -> JSONResponse:
    """按角色卡现场生成声线向量，挂到这个角色上，并给出判定。

    生成在推理端完成：向量按当前加载的模型尺寸与层数抽取，所以换模型之后
    要重新生成。生成完立刻判定一次：角色自己的原话要更可能出现，中性助手的
    回答不能跟着变容易。
    """
    character_id = str(payload.get("character_id") or "").strip()
    card = loader.get_card(character_id) if character_id else None
    if card is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    spec = _voice_spec(card)
    if not spec["persona"]:
        raise ApiError(
            code="voice_invalid",
            message="角色卡没有设定三层，抽不出声线",
            status_code=400,
        )
    ratio = 0.02
    try:
        ratio = float(payload.get("ratio", ratio))
    except (TypeError, ValueError):
        pass
    scale = voice.clamp_scale(float(payload.get("scale", 0.3)))
    file_name = str(payload.get("file") or voice.vector_file_for(character_id))
    target = str(voice.resolve_path(file_name))
    spec["ratio"] = ratio

    result = await service.generate_voice(spec, target)
    if not result.get("ok"):
        raise ApiError(
            code="voice_generate_failed",
            message=str(result.get("error") or "声线生成失败"),
            status_code=502,
        )
    voice.set_voice_config(character_id, target, scale)

    # 剂量也要量出来：同一条向量在不同强度下判据不一样，
    # 由程序自己扫一遍，取判据最好的那一档挂上。
    judge = _judge_spec(card)
    sweep: list[dict[str, Any]] = []
    best: tuple[float, float, dict[str, Any]] | None = None
    for candidate in DOSE_SCALES:
        measured = await service.verify_voice(judge, target, candidate)
        char_delta = float(measured.get("character_delta") or 0.0)
        ctrl_delta = float(measured.get("control_delta") or 0.0)
        score = char_delta - max(0.0, ctrl_delta)
        sweep.append({
            "scale": candidate,
            "verdict": measured.get("verdict"),
            "character_delta": round(char_delta, 4),
            "control_delta": round(ctrl_delta, 4),
            "score": round(score, 4),
        })
        if best is None or score > best[1]:
            best = (candidate, score, measured)
    if best is not None and best[0] != scale:
        scale = best[0]
        voice.set_voice_config(character_id, target, scale)
    verdict = best[2] if best is not None else {}
    return JSONResponse(
        content={
            "ok": True,
            "character_id": character_id,
            "file": target,
            "scale": scale,
            "ratio": ratio,
            "generate": {
                "bytes": result.get("bytes"),
                "layers": result.get("layers"),
                "n_embd": result.get("n_embd"),
                "pairs": result.get("pairs"),
            },
            "sweep": sweep,
            "verify": verdict,
        }
    )


@router.post("/verify")
async def verify_voice(payload: dict[str, Any]) -> JSONResponse:
    """判定当前挂着的声线向量是不是这个角色的声线。"""
    character_id = str(payload.get("character_id") or "").strip()
    card = loader.get_card(character_id) if character_id else None
    if card is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    config = voice.voice_config(character_id)
    if not config:
        raise ApiError(code="voice_not_found", message="该角色没有挂声线", status_code=404)
    spec = _judge_spec(card)
    verdict = await service.verify_voice(spec, str(config["path"]), float(config["scale"]))
    return JSONResponse(content={"ok": True, "character_id": character_id, "verify": verdict})


@router.post("/conformance")
async def check_conformance(payload: dict[str, Any]) -> JSONResponse:
    """判定一段话是不是这个角色说的。

    三层判定：结构、身份、声线。声线那层要推理端配合，推理端不可用时
    这一层记为跳过，其余两层照常给结论。

    参数：
        payload: character_id 必填；text 是要判定的那段话；
            其他角色的名字走 other_names，也可以不给。

    返回：
        {"ok": True, "verdict": ..., "issues": [...], "layers": [...]}。
    """
    character_id = str(payload.get("character_id") or "").strip()
    card = loader.get_card(character_id) if character_id else None
    if card is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    text = str(payload.get("text") or "")
    data = card.get("data") if isinstance(card.get("data"), dict) else {}

    other_names = payload.get("other_names")
    if not isinstance(other_names, list):
        other_names = []
    if not other_names:
        for other in loader.list_cards():
            name = str(other.get("name") or "").strip()
            if name and name != str(card.get("name") or ""):
                other_names.append(name)

    layers = [
        conformance.check_structure(text),
        conformance.check_identity(
            text,
            declared_self_reference=_declared_self_reference(data),
            character_name=str(card.get("name") or ""),
            other_names=[str(item) for item in other_names],
        ),
    ]

    # 声线层：挂着的向量要让角色自己的原话更可能出现。
    config = voice.voice_config(character_id)
    if config:
        try:
            measured = await service.verify_voice(
                _judge_spec(card), str(config["path"]), float(config["scale"])
            )
            verdict = str(measured.get("verdict") or "unknown")
            # 说明里有正面也有负面：判定通过的那句是结果，不是问题。
            issues = [
                str(item)
                for item in (measured.get("notes") or [])
                if str(item).startswith("the vector did not")
                or str(item).startswith("plain assistant lines became")
                or str(item).startswith("only one authentic sample")
            ]
            layers.append(
                {
                    "verdict": verdict,
                    "issues": issues,
                    "metrics": {
                        "character_delta": measured.get("character_delta"),
                        "control_delta": measured.get("control_delta"),
                    },
                }
            )
        except Exception:  # noqa: BLE001 - 推理端不可用时这一层跳过
            layers.append(
                {
                    "verdict": "skipped",
                    "issues": ["推理端不可用，声线层未判"],
                    "metrics": {},
                }
            )
    else:
        layers.append(
            {
                "verdict": "skipped",
                "issues": ["该角色没有挂声线，声线层未判"],
                "metrics": {},
            }
        )

    summary = conformance.summarize(layers)
    return JSONResponse(
        content={
            "ok": True,
            "character_id": character_id,
            "verdict": summary["verdict"],
            "issues": summary["issues"],
            "layers": summary["layers"],
        }
    )

