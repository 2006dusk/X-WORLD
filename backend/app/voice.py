"""角色专属控制向量。

控制向量是角色的声线层：角色A挂现代口语的向量，别的角色挂自己的，或者
不挂。它不能挂在推理服务端全局——服务端一套向量只服务一个角色，换角色就
得换向量，所以向量随请求走。

配置记在 settings 的 character_voice 映射里，按 character_id 查：

    {
      "c_xxx": {"file": "voice_001.f32", "scale": 0.5}
    }

file 是数据根 vectors 目录下的文件名，也可以是绝对路径。向量文件本身是
float32，每层一段 n_embd，第 0 层没有，所以文件有 n_layers - 1 段。

数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app import config, store

# settings 里的键名。
VOICE_SETTING_KEY = "character_voice"

# 数据根下的向量目录名。
VOICE_DIR_NAME = "vectors"


def vectors_dir() -> Path:
    """向量文件目录：数据根下的 vectors 子目录。"""
    path = config.data_root() / VOICE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_path(name: str) -> str:
    """把向量文件定位成绝对路径。相对名按数据根的 vectors 目录解析。"""
    text = str(name or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    if candidate.is_absolute():
        return str(candidate)
    return str(vectors_dir() / text)


def _number(value: Any, default: float) -> float:
    """取一个数值；取不到或不是数就用默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# 剂量上限。实测 0.65 时模型脱轨：自己续写多轮对话，把模板标记当正文吐
# 出来，回合格式也散了。上限压在这里，配置写错也不会把回合写坏。
MAX_SCALE = 0.6


def _clamp_scale(scale: float) -> float:
    """把注入强度夹进可用区间。"""
    if scale < 0.0:
        return 0.0
    if scale > MAX_SCALE:
        return MAX_SCALE
    return scale


def clamp_scale(scale: float) -> float:
    """把注入强度夹进可用区间。对外接口，给配置入口用。"""
    return _clamp_scale(scale)


# ---------------------------------------------------------------------------
# 运行时生成声线
#
# 声线向量是按模型尺寸做、按角色定向的。角色卡里已经有两样东西：
# 设定三层（identity / behavior / speech）与对话示例（mes_example）。
# 对话示例就是这个角色自己的说话样本，运行时用它当声线语料，
# 让推理端按「角色声线回答 vs 中性助手回答」的差分现场抽出一份向量。
# ---------------------------------------------------------------------------

# 角色设定三层，按这个顺序拼成给模型看的声线描述。
PERSONA_FIELDS = ("identity", "behavior", "speech")

# 中性助手设定：负样本一侧。差分要的是「角色的说法」减去「普通助手的说法」。
NEUTRAL_PERSONA = "你是一个普通的助手，用平实、简明的语气回答问题，不要用角色的口吻。"

# 卡里没有对话示例时用的兜底问题：让模型用角色设定自己回答。
FALLBACK_PROMPTS = ("今天发生了什么事？", "你怎么看眼前这个人？")

# 至少要几个提示。提示太少时，差分会被那一段对话的话题主导，
# 而不是角色的声线；正负两侧用同一批提示，话题抵消，剩下的才是声线。
MIN_SAMPLES = 3


def card_persona(payload: dict[str, Any]) -> str:
    """把角色卡的设定三层拼成一段声线描述。

    参数：
        payload: 角色卡的数据段。

    返回：
        拼好的文本；卡里三层都空时返回空串。
    """
    parts: list[str] = []
    for key in PERSONA_FIELDS:
        value = str(payload.get(key) or "").strip()
        if value:
            parts.append(value)
    return "\n\n".join(parts)


def parse_examples(text: str) -> list[tuple[str, str]]:
    """把卡里的对话示例拆成「用户那句、角色那句」的样本对。

    参数：
        text: mes_example 原文，形如 {{user}}: ... {{char}}: ...

    返回：
        样本对列表；没有可用样本时是空列表。
    """
    pairs: list[tuple[str, str]] = []
    user: str | None = None
    char_lines: list[str] = []

    def flush() -> None:
        if user and char_lines:
            pairs.append((user, "\n".join(char_lines)))

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{{user}}"):
            flush()
            user = line[len("{{user}}"):].lstrip(": ").strip()
            char_lines = []
        elif line.startswith("{{char}}"):
            if user is None:
                continue
            tail = line[len("{{char}}"):].lstrip(": ").strip()
            if tail:
                char_lines.append(tail)
        elif user is not None and char_lines:
            char_lines.append(line)
    flush()
    return pairs


# 引号里的台词。示例对白里角色的发言都夹在引号中，
# 引号外的是叙述动作，不算台词。
SPOKEN_QUOTES = re.compile(r"[\u201c\u300c]([^\u201d\u300d\u201c\u300d]{1,200})[\u201d\u300d]")


def spoken_lines(text: str) -> list[str]:
    """从一段示例回答里取出角色说出口的每一句。

    参数：
        text: 角色那段回答，可能混着叙述与台词。

    返回：
        台词列表，按出现顺序；没有引号内容时返回空列表。
    """
    found: list[str] = []
    for piece in SPOKEN_QUOTES.findall(str(text or "")):
        line = piece.strip()
        if line:
            found.append(line)
    return found


def voice_judge_samples(payload: dict[str, Any]) -> list[dict[str, str]]:
    """取角色的原话样本，用来判定声线。

    判定只能用角色卡里的原话：那些是作者写的、确定属于这个角色的说法。
    模型自己生成的句子不能当标尺——模型本来就更喜欢自己的输出，拿它量自己的
    声线会得出「挂不挂向量都像」的结果。

    样本按「一句台词」拆开，不按整段回答。整段回答里叙述占了一半，测的是
    叙述文风，不是说话声线；实测按句拆后判据强度翻倍（四张现役卡：
    角色A +0.28 对 +0.10，角色B +0.42 对 +0.24），而且样本数从 1 到 2
    涨到 4 到 6，判定不再只靠一句话。

    参数：
        payload: 角色卡的数据段。

    返回：
        每项是 {"prompt": 用户那句, "answer": 角色的一句台词}。
    """
    samples: list[dict[str, str]] = []
    for prompt, answer in parse_examples(str(payload.get("mes_example") or "")):
        lines = spoken_lines(answer)
        if lines:
            for line in lines:
                samples.append({"prompt": prompt, "answer": line})
        else:
            # 没有引号时整段也算一句，别让这一张卡没有样本可判。
            text = answer.strip()
            if text:
                samples.append({"prompt": prompt, "answer": text})
    return samples


def voice_samples(payload: dict[str, Any]) -> list[dict[str, str]]:
    """取出角色的声线样本。

    参数：
        payload: 角色卡的数据段。

    返回：
        每项是 {"prompt": 用户那句, "answer": 角色那句}。
        卡里没有对话示例时退回兜底问题，answer 留空由推理端按角色设定生成。
    """
    samples: list[dict[str, str]] = []
    for prompt, answer in parse_examples(str(payload.get("mes_example") or "")):
        lines = spoken_lines(answer)
        if lines:
            for line in lines:
                samples.append({"prompt": prompt, "answer": line})
        else:
            samples.append({"prompt": prompt, "answer": answer})
    for prompt in FALLBACK_PROMPTS:
        if len(samples) >= MIN_SAMPLES:
            break
        if all(item["prompt"] != prompt for item in samples):
            samples.append({"prompt": prompt, "answer": ""})
    return samples


def vector_file_for(character_id: str) -> str:
    """角色运行时声线向量的文件名。"""
    return f"{character_id}_runtime.f32"


def voice_config(character_id: str | None) -> dict | None:
    """取角色挂的控制向量配置。

    参数：
        character_id: 角色 id。

    返回：
        推理端要用的向量配置，字段是 path、scale、layer_start、layer_end；
        角色没挂向量或配置不可用时返回 None。
    """
    if not character_id:
        return None
    try:
        mapping = store.get_setting(VOICE_SETTING_KEY)
    except Exception:
        return None
    if not isinstance(mapping, dict):
        return None
    entry = mapping.get(character_id)
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("file") or entry.get("path") or "").strip()
    if not name:
        return None
    path = resolve_path(name)
    if not path or not Path(path).is_file():
        return None
    result = {
        "path": path,
        "scale": _clamp_scale(_number(entry.get("scale", 1.0), 1.0)),
        "layer_start": int(_number(entry.get("layer_start", 1), 1)),
        "layer_end": int(_number(entry.get("layer_end", -1), -1)),
    }
    return result


def set_voice_config(character_id: str, file: str, scale: float = 1.0) -> dict:
    """给角色挂一个控制向量。

    参数：
        character_id: 角色 id。
        file: 向量文件名（数据根 vectors 目录下）或绝对路径。
        scale: 注入强度。

    返回：
        落库后的配置。
    """
    mapping = store.get_setting(VOICE_SETTING_KEY)
    if not isinstance(mapping, dict):
        mapping = {}
    else:
        mapping = dict(mapping)
    mapping[character_id] = {"file": str(file), "scale": float(scale)}
    store.set_setting(VOICE_SETTING_KEY, mapping)
    return voice_config(character_id) or mapping[character_id]


def clear_voice_config(character_id: str) -> bool:
    """摘掉角色的控制向量。

    参数：
        character_id: 角色 id。

    返回：
        之前确实挂着向量时返回 True。
    """
    mapping = store.get_setting(VOICE_SETTING_KEY)
    if not isinstance(mapping, dict) or character_id not in mapping:
        return False
    mapping = dict(mapping)
    mapping.pop(character_id, None)
    store.set_setting(VOICE_SETTING_KEY, mapping)
    return True
