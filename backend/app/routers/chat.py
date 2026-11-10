"""会话生成路由。

POST /v1/chat/completions — 非流式与 SSE 流式会话生成。

数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from app import presets as preset_service
from app import store
from app.assembly.pipeline import (
    assemble,
    clean_state_fields,
    dropped_state_items,
    resolve_tokenizer,
    user_evidence_text,
)
from app.contracts import (
    BudgetInfo,
    ChatChoice,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    EditRequest,
    Usage,
)
from app.errors import ApiError
from app.inference.base import InferenceParams, StreamEvent
from app.inference.llamacpp import LlamaCppAdapter
from app.inference.normalize import (
    IMPERIAL_SELF_REFERENCES,
    collapse_paragraphs,
    drop_annotation_lines,
    normalize_self_reference,
)
from app.inference.repetition import copied_fragment, is_repetitive
from app.inference.structure import (
    StructureSplitter,
    block_names,
    display_labels,
    split_blocks,
)
from app.state import (
    merge_state,
    parse_state_block,
    parse_state_line,
    render_state_block,
    split_state_block,
    state_payload,
)
from app.voice import voice_config

router = APIRouter()

logger = logging.getLogger(__name__)

RESERVED_OUTPUT_TOKENS = 1024

# 复读闸门：照搬上一轮整句时换种子重采样一次。
RETRY_TEMPERATURE = 0.85
RETRY_SEED_BASE = 20261106

# 复读重采样要的是「跳出上一轮」，温度得比正文更高。原先用 0.85（比正文的
# 1.0 还低），模型在概率质量集中时照旧把上一轮整段搬过来，实测出现过两次
# 连续回合逐字相同。换成 1.15，换种子之外再加一点发散。
REPETITION_RETRY_TEMPERATURE = 1.15

# 采样参数：按 SillyTavern 的做法往「发散但不散架」调。
#
# 默认档原先是 temperature 0.7 / top_p 0.9：文本稳，但每轮都朝着同一个方向
# 逼近，用户反馈「模型好像盯着一个目标一直在靠近」。酒馆默认预设是
# temperature 1 / top_p 1（SillyTavern 默认采样与提示顺序（docs.sillytavern.app）），
# 并提供三种针对性手段（SillyTavern common-settings 文档（docs.sillytavern.app））：
# Dynamic Temperature —— "Aims to produce more creative outputs without
# sacrificing coherency"；XTC —— "retains coherence"，偶尔踢掉首选词；
# DRY —— 挡逐字复读。
#
# 这里取：温度上限 1.0（酒馆默认预设也是 1），top_p 0.95 留一点长尾，
# min_p 0.05 砍掉相对首选词过低的候选。
#
# 酒馆的 Dynamic Temperature、XTC、DRY 三项，自写服务端这一套在请求体里
# 认这三个字段（dynatemp_range / xtc_probability / dry_multiplier），
# 但客户端这层没在当前栈上复测过效果，就不写进请求体骗自己：
# - 旧服务端那套 Python 包没有 dynatemp_range / xtc_probability /
#   dry_multiplier 构造参数；
# - 那套栈的 OAI 兼容端点也不读这三个请求字段（实测固定种子下 plain、dynatemp、
#   XTC、DRY 四种请求逐字相同，见 docs/理论支持/提示层审计.md 第 7 节）；
# - 旧服务端 CLI 也没有对应开关。
# 旧栈实测真正生效的采样旋钮只有：temperature、top_p、min_p、mirostat、seed。
# 酒馆文档里与这三项同类的旋钮，本机可用的对应物是 Min P
# （"Works best at low values such as 0.1-0.01"）。
def _sampling_float(name: str, default: float) -> float:
    """从环境变量读采样参数，读不到或非法时用默认值。

    改采样不必改代码：调参与 A/B 对照都要能换一套参数跑。
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _sampling_extra(defaults: dict[str, float]) -> dict[str, float]:
    """从环境变量读额外采样参数。

    格式是逗号分隔的 key=value，例如
    XWORLD_SAMPLING_EXTRA="xtc_probability=0.1,dry_multiplier=0.8"。
    整串写 none 表示清空额外参数，回到纯 temperature + top_p。
    """
    raw = os.environ.get("XWORLD_SAMPLING_EXTRA", "").strip()
    if not raw:
        return dict(defaults)
    if raw.lower() == "none":
        return {}
    result: dict[str, float] = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        try:
            result[key.strip()] = float(value.strip())
        except ValueError:
            continue
    return result


SAMPLING_TEMPERATURE = _sampling_float("XWORLD_SAMPLING_TEMPERATURE", 1.0)
SAMPLING_TOP_P = _sampling_float("XWORLD_SAMPLING_TOP_P", 0.95)
SAMPLING_EXTRA: dict[str, float] = _sampling_extra(
    {
        # Min P：砍掉相对首选词过低的候选，尾部因此不会无限长。
        # 这套自写服务端的采样默认就是 0.05，
        # 写出来是为了让「本项目有意这么设」这件事有据可查。
        "min_p": 0.05,
    }
)

# 长度闸门：回合短于预设声明的下限时，让模型重写一次。
# 提示层锁不住长度：实测三种写法（抬下限、加建议、拆结构配额）各六轮，
# 带内率都在三到四轮之间，差异落在噪声里。闸门比提示可靠。
LENGTH_RETRY_TEMPERATURE = 0.75

# 对白承接闸门：对白短到接不住用户这一轮时，让模型重写一次。
# 实测（backend/tools/probe_uptake_directive.py）：用户说「晚上好」，
# 回的是「有……威士忌。」——模型在答历史里的旧问题。对白只有一句时，
# 没有落点去接用户这一轮。要求写两句后对白均值从 11 到 21 字涨到 43 字，
# 句数从 1.5 涨到 2.25，场景照搬率不变。
# 闸门排在复读闸门之前：接不住用户比照搬更致命。
DIALOGUE_MIN_SENTENCES = 2
DIALOGUE_SHORT_CHARS = 30

# 角色归属闸门：模型替用户决定说什么或做什么时重写一次。
# 借 SillyTavern 的做法：那边把「替用户写」当成独立的生成模式（Impersonate），
# 并用角色名做停止串从生成层拦。本项目没有那种模式开关，改为按回合块
# 做确定性判定：场景不出现第二人称与引号台词，对白不出现第一人称叙述，
# 也不出现给用户派台词的说话人标签。
# 闸门排在结构之后、对白承接之前：越位比接不住更致命。
#
# 口径与动作块的新写法对齐：动作块写这一格实际发生的事，含用户身上发生的
# 事（用「你」「对方」写），这不算越位；越位是凭空给用户安排他没做过的新动作。
# 实测（backend/tools/probe_paren_v3.py）：新写法下闸门误伤从 2.5 次降到 0.5 次。
ROLE_OWNER_SPEAKER_TAG = re.compile(r"(你|您|用户|对方)\s*[：:]")
# 「角色K：……」这种说话人标签后面的内容是角色自己说的话，不算叙述。
ROLE_OWNER_SPEAKER_LINE = re.compile(r"^[^\n：:]{1,12}[：:].*$", re.MULTILINE)

# 「你」后面跟着这些动词时，是模型替用户写了动作。
ROLE_OWNER_USER_ACTIONS = (
    "伸手", "抱住", "推开", "走近", "后退", "点头", "摇头", "转身", "坐下",
    "站起", "拿起", "放下", "递出", "递给", "递", "塞进", "回答", "喊道",
    "问道", "感到", "决定", "想要", "打算", "发现", "听到", "听见", "想起",
    "看着", "看向", "走进", "离开", "抬头", "低头", "扶住", "抽回", "挣扎",
)
# 「你」前面是这些字时，「你」是宾语：角色在对你做动作，不是模型替你演。
ROLE_OWNER_OBJECT_MARKERS = "对向朝着和与给替帮"
ROLE_OWNER_SUBJECT_MARKERS = ("他", "她")

# 状态行的字段名。模型把状态写进正文块时，正文里会出现这些行首。
STATE_FIELD_PREFIXES = (
    "当前位置：",
    "人物位置：",
    "人物物品：",
    "场景物品：",
    "地图：",
    "时间：",
)


def _get_adapter():
    """获取推理适配器。默认使用 llama.cpp。"""
    return LlamaCppAdapter()


def _last_user_text(request: ChatRequest) -> str:
    """取本轮用户输入原文，不含装配时追加的回合要求。"""
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    return ""


def _last_assistant_text(session_id: str | None, scope: str) -> str:
    """上一轮的角色回复正文，用于复读检测。"""
    if not session_id:
        return ""
    rows = store.session_path(session_id, scope)
    for row in reversed(rows):
        if row.get("role") == "assistant":
            return str(row.get("content") or "")
    return ""


def _retry_params(
    params: InferenceParams,
    attempt: int,
    temperature: float = RETRY_TEMPERATURE,
) -> InferenceParams:
    """重采样参数：换随机种子并提高温度。

    复读多来自模型在自己的上一轮上续写，换种子并提高温度能打断这种续写。
    """
    return InferenceParams(
        temperature=temperature,
        top_p=params.top_p,
        max_tokens=params.max_tokens,
        repetition_penalty=params.repetition_penalty,
        extra={**params.extra, "seed": RETRY_SEED_BASE + attempt},
    )


def _effective_spec(xw) -> dict:
    """生效预设的 output_spec，取不到时返回空字典。"""
    preset_id = preset_service.resolve_preset_id(xw.preset_id)
    if not preset_id:
        return {}
    preset = preset_service.get_preset(preset_id)
    if not isinstance(preset, dict):
        return {}
    spec = preset.get("output_spec")
    return spec if isinstance(spec, dict) else {}


def _min_chars(xw) -> int:
    """生效预设声明的回合字数下限；没有声明时为 0，表示不设长度闸门。"""
    try:
        value = int(_effective_spec(xw).get("min_chars") or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _length_detail(
    xw,
    blocks: dict[str, str] | None,
    structure: list[str],
    min_chars: int,
) -> str:
    """按块给出实际字数与下限，让重写指令可执行。

    笼统的「写足 150 字」效果有限：模型把篇幅都给了场景，对白只有 19 字。
    点名到块并附上下限，模型才知道该往哪补。
    """
    floors = preset_service.structure_minimums(_effective_spec(xw), min_chars)
    labels = display_labels(structure)
    parts: list[str] = []
    for name in structure:
        if name == "state":
            continue
        actual = len(blocks.get(name, "")) if blocks else 0
        floor = floors.get(name)
        label = labels.get(name, name)
        if floor:
            parts.append(label + " " + str(actual) + " 字（要求至少 " + str(floor) + " 字）")
        else:
            parts.append(label + " " + str(actual) + " 字")
    if not parts:
        return ""
    detail = "上一版" + "、".join(parts) + "，请重写这个回合"
    if "state" in structure:
        detail += "，状态块照旧写六行短句"
    return detail


def _with_expand_note(
    messages: list[ChatMessage],
    current_chars: int,
    min_chars: int,
    detail: str = "",
) -> list[ChatMessage]:
    """给本轮输入追加一句扩写要求。

    detail 非空时用它点名到块，否则退回笼统的「写足 N 字」。
    """
    if detail:
        note = detail + "。"
    else:
        note = (
            "上一版只有 " + str(current_chars) + " 字，"
            "请把这个回合写足 " + str(min_chars) + " 字以上。"
        )
    updated = list(messages)
    if updated and updated[-1].role == "user":
        last = updated[-1]
        updated[-1] = ChatMessage(
            role=last.role,
            content=last.content + "\n\n" + note,
            reasoning=last.reasoning,
        )
    return updated


def _structure_names(xw) -> list[str]:
    """生效预设声明的结构块名；没有声明时是空列表。

    声明结构的回合按标记分块输出，由 app.inference.structure 拆块。
    """
    return block_names(_effective_spec(xw).get("structure"))


def _single_paragraph(xw) -> bool:
    """生效预设是否要求把回合写成完整一段。

    由 output_spec.single_paragraph 声明。提示层已经写明「写成完整一段，
    不分行」，但去掉预设里的格式示例后模型会自行分段，段落是排版要求，
    因此在路由层按声明确定性地压平。

    声明结构时不再压平：分块回合要保留块内换行，压平会把块压成一段。
    """
    if _structure_names(xw):
        return False
    return bool(_effective_spec(xw).get("single_paragraph"))


def _reply_parts(
    text: str,
    structure: list[str],
    single_paragraph: bool,
    self_reference: str = "",
) -> tuple[str, dict[str, str] | None]:
    """把模型原始输出整理成 (正文, 结构块)。

    结构化回合：正文去掉标记，块内容单独给出。
    普通回合：按需压平段落，块为空。

    self_reference 非空时，台词里的帝王自称按卡片声明换掉。
    """
    cleaned = normalize_self_reference(drop_annotation_lines(text), self_reference)
    if structure:
        blocks = _normalize_blocks(split_blocks(cleaned, structure), structure)
        return _reply_text(blocks, structure), blocks
    if single_paragraph:
        return collapse_paragraphs(cleaned), None
    return cleaned, None


def _with_missing_note(
    messages: list[ChatMessage],
    missing: list[str],
) -> list[ChatMessage]:
    """给本轮输入追加一句缺块补齐要求。"""
    labels = display_labels(missing)
    note = (
        "上一版缺少 "
        + "、".join(labels.get(name, name) for name in missing)
        + " 这块，请按回合要求分块重写，每块都要有。"
    )
    updated = list(messages)
    if updated and updated[-1].role == "user":
        last = updated[-1]
        updated[-1] = ChatMessage(
            role=last.role,
            content=last.content + "\n\n" + note,
            reasoning=last.reasoning,
        )
    return updated


def _dialogue_sentences(text: str) -> int:
    """对白句数：按中文句末标点计。"""
    return sum(1 for ch in text if ch in "。！？")


def _dialogue_uptake_short(
    blocks: dict[str, str] | None,
    structure: list[str],
    auto_turn: str = "",
) -> bool:
    """对白是否短到接不住用户这一轮。

    判定：结构化回合有对白块，且对白为空或只有一句短台词。
    一句短台词没有落点去回应用户，模型会顺着历史里上一句继续答旧问题。

    场景推进轮（describe）不查这一项：那一轮本就不该有对白，
    闸门会把模型按回「补两句台词」，与这一轮的目的相反。
    """
    if auto_turn == "describe":
        return False
    if not structure or not blocks or "dialogue" not in structure:
        return False
    dialogue = str(blocks.get("dialogue") or "").strip()
    if not dialogue:
        # 对白块是可选的：模型没写对白不算接不住，不为它重跑一轮。
        return False
    if _dialogue_sentences(dialogue) >= DIALOGUE_MIN_SENTENCES:
        return False
    return len(dialogue) < DIALOGUE_SHORT_CHARS


def _character_name(xw) -> str:
    """角色卡里的名字。判定谁在说话时要用：角色名是合法的主语。"""
    character_id = getattr(xw, "character_id", None)
    if not character_id:
        return ""
    card = store.get_card(character_id)
    if not isinstance(card, dict):
        return ""
    payload = card.get("data")
    if not isinstance(payload, dict):
        payload = card
    return str(payload.get("name", "") or "").strip()


def _strip_quoted(text: str) -> str:
    """去掉引号内容，只留叙述部分。

    引号里是角色自己说的话，里面出现「你」是正常的；
    要判定的是叙述部分有没有越过角色视角。
    """
    out: list[str] = []
    depth = 0
    for ch in text or "":
        if ch in "“\"「":
            depth += 1
            continue
        if ch in "”」":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _user_written_actions(scene: str, char_name: str = "") -> bool:
    """场景里模型是不是替用户写了动作。

    「他看向你」是角色对用户做动作，不算越位；
    「你伸手抱住他」是模型替用户行动，算越位。
    判定看两处：「你」前面是宾语标记或第三人称主语时放过；
    「你」后面跟着动作动词时判越位。
    """
    subjects = ROLE_OWNER_SUBJECT_MARKERS
    if char_name:
        subjects = subjects + (char_name,)
    for match in re.finditer("你|您", scene):
        index = match.start()
        before = scene[max(0, index - 8) : index]
        after = scene[index + 1 : index + 9]
        # 「你」前面是第三人称主语时，「你」是宾语：角色在对用户做动作。
        head = before.replace("，", "|").replace("。", "|").split("|")[-1]
        if any(marker in head for marker in subjects):
            continue
        if any(verb in after for verb in ROLE_OWNER_USER_ACTIONS):
            return True
    return False


def _role_owner_violation(
    blocks: dict[str, str] | None,
    structure: list[str],
    char_name: str = "",
) -> str:
    """角色归属违规类型；空串表示没有越位。

    口径与 SillyTavern 一致：AI 只写角色本人，不替用户决定说什么或做什么，
    也不在场景里替双方开口。第二人称单独不算越位：「他看向你」是角色在
    对用户做动作，只有模型替用户行动或替用户说话才算。

    与动作块的新写法对齐：动作块写这一格实际发生的事，写用户身上发生的
    事（用「你」「对方」）不算越位；越位是凭空给用户安排他没做过的新动作。
    """
    if not structure or not blocks:
        return ""
    # 叙述块有两块：动作块（普通回合）与场景块（场景推进轮）。
    # 两块都要查：只查一块的话，另一块里的越位文本没人查，闸门等于失效。
    narration_parts = [
        str(blocks.get(name) or "") for name in ("action", "scene")
    ]
    narration = "\n".join(part for part in narration_parts if part)
    dialogue = str(blocks.get("dialogue") or "")
    if not narration and not dialogue:
        return ""
    if narration and ("“" in narration or "「" in narration or '"' in narration):
        # 叙述块里写了台词：模型在叙述里替双方开口。
        return "narration_has_dialogue"
    narration_out = _strip_quoted(narration)
    if _user_written_actions(narration_out, char_name):
        return "narration_user_action"
    if "我" in narration_out:
        return "narration_first_person"
    if ROLE_OWNER_SPEAKER_TAG.search(dialogue):
        return "user_speaker_tag"
    dialogue_out = ROLE_OWNER_SPEAKER_LINE.sub("", _strip_quoted(dialogue))
    if "我" in dialogue_out:
        return "dialogue_first_person"
    return ""


def _self_reference_violation(
    blocks: dict[str, str] | None,
    structure: list[str],
    declared: str,
) -> bool:
    """台词里的自称是不是滑成了别的自称。

    卡里声明了自称就以它为准。宫廷场景里模型会滑到「朕」「本宫」：
    场景先验与用户称呼都把角色往皇帝那边推，提示层三种位置都试过，
    一次都没纠正过来（实测四轮里「朕」出现六到十次）。

    归一层的确定性替换是事后改写，这一层是事前判定：自称不对就重写，
    改写出来的文本由模型自己顺下来，比事后替换自然。

    参数：
        blocks: 本轮的块内容。
        structure: 生效预设声明的块名。
        declared: 卡里声明的自称，空串表示没声明，这一层不判。

    返回：
        滑了返回 True。
    """
    if not declared or not structure or not blocks:
        return False
    dialogue = str(blocks.get("dialogue") or "")
    if not dialogue:
        return False
    for word in IMPERIAL_SELF_REFERENCES:
        if word == declared:
            continue
        if word in dialogue:
            return True
    return False


def _with_self_reference_note(messages: list[ChatMessage]) -> list[ChatMessage]:
    """给本轮输入追加一句自称改写要求。"""
    note = (
        "上一版用了不属于这个角色的自称。重写这个回合："
        "台词里只用角色自己的自称，不要用「朕」「本宫」这类帝王自称，"
        "也不要替用户说话。"
    )
    updated = list(messages)
    if updated and updated[-1].role == "user":
        last = updated[-1]
        updated[-1] = ChatMessage(
            role=last.role,
            content=last.content + chr(10) + chr(10) + note,
            reasoning=last.reasoning,
            blocks=last.blocks,
        )
    elif not updated:
        updated.append(ChatMessage(role="user", content=note))
    return updated


def _with_role_owner_note(messages: list[ChatMessage], kind: str) -> list[ChatMessage]:
    """给本轮输入追加一句角色归属改写要求。"""
    if kind == "narration_has_dialogue":
        note = (
            "上一版在动作或场景描写里写了台词，等于替对方开口。"
            "重写这个回合：动作块写这一格实际发生的事，"
            "不要在叙述里出现任何引号里的台词；"
            "台词一律写进【对话】块。"
        )
    elif kind == "narration_user_action":
        note = (
            "上一版替用户写了用户没做过的新动作，等于替用户扮演。"
            "重写这个回合：只写角色自己说出口的台词；"
            "用户这一轮指定或写到的场面照着写，"
            "用户身上发生的事用「你」或「对方」写，"
            "不要凭空给用户安排他没做过的动作。"
        )
    elif kind == "narration_first_person":
        note = (
            "上一版在动作或场景描写里用了第一人称。"
            "重写这个回合：叙述用第三人称，"
            "角色本人写「他」「她」或名字，用户写「你」或「对方」；"
            "台词里才用第一人称。"
        )
    elif kind == "user_speaker_tag":
        note = (
            "上一版替用户派了台词。重写这个回合："
            "只写角色自己说出口的台词，不替用户派台词。"
        )
    else:
        note = (
            "上一版的人称越界。重写这个回合："
            "叙述用第三人称（角色写「他」「她」或名字，用户写「你」或「对方」），"
            "台词里用第一人称；只写角色自己说出口的台词。"
        )
    updated = list(messages)
    if updated and updated[-1].role == "user":
        last = updated[-1]
        updated[-1] = ChatMessage(
            role=last.role,
            content=last.content + "\n\n" + note,
            reasoning=last.reasoning,
        )
    return updated


def _with_dialogue_note(
    messages: list[ChatMessage],
    user_text: str,
    auto_turn: str = "",
) -> list[ChatMessage]:
    """给本轮输入追加一句对白改写要求，点名用户这一轮的话。"""
    spoken = user_text.strip()
    if auto_turn:
        # 自动回合用户没说话，不能要求回应用户这一轮：改成接住上一轮往下说。
        note = (
            "上一版的对白太短，接不住你上一轮说的话。"
            "请重写这个回合：对白至少写两句，接着上一轮的语境往下说。"
        )
    elif spoken:
        note = (
            "上一版的对白太短，没有接住用户这一轮。请重写这个回合："
            "对白至少写两句，第一句要直接回应用户说的「" + spoken + "」。"
        )
    else:
        note = (
            "上一版的对白太短，没有接住用户这一轮。"
            "请重写这个回合：对白至少写两句，并且直接回应用户这一轮。"
        )
    updated = list(messages)
    if updated and updated[-1].role == "user":
        last = updated[-1]
        updated[-1] = ChatMessage(
            role=last.role,
            content=last.content + "\n\n" + note,
            reasoning=last.reasoning,
        )
    return updated


def _with_repeat_note(
    messages: list[ChatMessage],
    fragment: str = "",
) -> list[ChatMessage]:
    """给本轮输入追加一句不要照搬上一轮的要求。

    只换种子重采样常带不动：上一轮的原话就在历史里，模型顺着它续写。
    把照搬的那一段点出来，模型才知道要避开的是哪一段。
    """
    if fragment:
        note = (
            "上一版把上一轮说过的内容照搬了过来（"
            + fragment
            + "）。这一轮要给新的进展：不要重复上一轮说过的话，也不要重复上一轮的动作。"
        )
    else:
        note = (
            "上一版重复了上一轮说过的内容。这一轮要给新的进展："
            "不要重复上一轮说过的话，也不要重复上一轮的动作。"
        )
    updated = list(messages)
    if updated and updated[-1].role == "user":
        last = updated[-1]
        updated[-1] = ChatMessage(
            role=last.role,
            content=last.content + "\n\n" + note,
            reasoning=last.reasoning,
        )
    return updated


def _previous_body_block(blocks: dict[str, str]) -> str:
    """找状态块前面最近的一个正文块。

    块顺序自由后状态块可能在最前面，那时退到它后面最近的一个正文块。
    """
    order = list(blocks.keys())
    if "state" not in order:
        return ""
    index = order.index("state")
    before = [name for name in order[:index] if name != "state"]
    if before:
        return before[-1]
    after = [name for name in order[index + 1:] if name != "state"]
    return after[0] if after else ""


def _extract_state_from_body(
    blocks: dict[str, str],
    structure: list[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """从正文块末尾摘出状态行。

    模型漏写【状态】标记时，状态行会跟在场景或对白后面。不摘出来，
    状态行就留在对话气泡里，右侧面板也更新不了。

    只从末尾往前摘连续的状态行：正文中间出现的「时间：……」是台词不是状态。
    """
    fields: dict[str, Any] = {}
    updated = dict(blocks)
    for name in structure:
        if name == "state":
            continue
        text = updated.get(name, "")
        if not text:
            continue
        lines = text.split(chr(10))
        while lines:
            parsed = parse_state_line(lines[-1])
            if parsed is None:
                break
            key, value = parsed
            if key in fields:
                break
            fields[key] = value
            lines.pop()
        if len(lines) != len(text.split(chr(10))):
            updated[name] = chr(10).join(lines).strip()
    return fields, updated


def _reattribute_dialogue(
    blocks: dict[str, str],
    structure: list[str],
) -> dict[str, str]:
    """台词块空着而叙述块里混着台词时，把台词挪回对白块。

    模型漏写【对话】标记时，台词会跟在动作或场景后面。整块当叙述，
    气泡里就是一大段旁白：谁在说话分不清，界面看着像角色自说自话。
    实测（backend/tools/probe_paren_e2e.py）：四轮里三轮出现，闸门改写
    也常常改不掉。

    内容不丢，只改归属——台词与叙述本来就在同一段里。引号里只是个词
    （「恩赐」）时不挪：引号前不是句末或段首，那是术语不是台词。
    """
    if "dialogue" not in structure:
        return blocks
    if str(blocks.get("dialogue") or "").strip():
        return blocks
    for name in ("action", "scene"):
        text = str(blocks.get(name) or "")
        if not text:
            continue
        index = -1
        for mark in ("\u201c", "\u300c", '"'):
            found = text.find(mark)
            if found != -1 and (index == -1 or found < index):
                index = found
        if index == -1:
            continue
        head = text[:index]
        if head and head[-1] not in "\u3002\uff01\uff1f\uff0c\n":
            continue
        dialogue = text[index:].strip()
        if not dialogue:
            continue
        updated = dict(blocks)
        updated[name] = head.strip()
        updated["dialogue"] = dialogue
        return updated
    return blocks


def _normalize_blocks(blocks: dict[str, str], structure: list[str]) -> dict[str, str]:
    """规范化状态块与台词块。

    状态块只留该有的键值行；模型塞进状态块的成句内容并回正文块。
    实测（backend/tools/probe_state_block.py）状态块尾部会多出一段括号散文，
    归一后状态块只剩该有的东西，散文也不丢。

    块顺序自由后状态块可能整体落在正文前面，里面全是散文而没有键值行。
    这时散文要回正文块：留在状态块里会被前端一起过滤掉，回合正文就少了一段。

    状态块整个没写时，到正文块末尾找状态行：模型漏写【状态】标记是常见写法，
    不摘出来，状态行会出现在对话气泡里。

    台词块空着而叙述块里混着台词时，把台词挪回对白块，见 _reattribute_dialogue。
    """
    blocks = _reattribute_dialogue(blocks, structure)
    if "state" not in structure:
        return blocks
    fields, rest = split_state_block(blocks.get("state", ""))
    normalized = dict(blocks)
    if not fields:
        fields, normalized = _extract_state_from_body(normalized, structure)
    if not fields and not rest:
        return normalized
    normalized["state"] = render_state_block(fields) if fields else ""
    if rest:
        previous = _previous_body_block(blocks)
        if previous:
            body = normalized.get(previous, "")
            normalized[previous] = (body + chr(10) + chr(10).join(rest)).strip()
    return normalized


MAX_BODY_CHARS = 900


def _body_chars(blocks: dict[str, str] | None, structure: list[str]) -> int:
    """正文字数：只算场景与对白，状态块是元数据不计入。"""
    if not blocks:
        return 0
    return sum(len(blocks.get(name, "")) for name in structure if name != "state")


def _state_leaked_into_body(blocks: dict[str, str], structure: list[str]) -> bool:
    """状态行是不是漏进了正文块。

    模型偶尔把六行状态写在【对话】块里，【状态】块空着：面板拿不到新状态，
    正文块里还多出一堆状态文本。实测（backend/tools/probe_role_owner.py
    的 v16 对照轮次）就是这样。对白因此变长，还会骗过对白长度闸门。
    """
    for name in structure:
        if name == "state":
            continue
        text = blocks.get(name, "")
        if any(prefix in text for prefix in STATE_FIELD_PREFIXES):
            return True
    return False


def _missing_blocks(blocks: dict[str, str], structure: list[str]) -> list[str]:
    """找出需要重写的块。

    场景块与对白块是可选的：回合形式自由，模型可以只写一块，也可以不写标记。
    这两种情况都不重写。

    状态块写出来却解析不出字段，等同于缺失：重写一次比留一块空面板好。
    状态块整个没写不重写：闲聊回合不该为此重跑一轮，更不该丢弃已显示的内容。
    """
    missing: list[str] = []
    for name in structure:
        if name != "state":
            continue
        text = blocks.get(name, "")
        if text and not parse_state_block(text):
            missing.append(name)
    if _state_leaked_into_body(blocks, structure):
        missing.append("state")
    # 单块异常膨胀也算缺块：实测（backend/tools/probe_scene_continuity.py）
    # 闸门重写时模型偶尔丢掉标记，整段正文灌进对白块，单块能到 900 字。
    for name in structure:
        if name == "state":
            continue
        if len(blocks.get(name, "")) > MAX_BODY_CHARS:
            missing.append(name)
    return missing


def _reply_text(blocks: dict[str, str] | None, structure: list[str]) -> str:
    """回合正文：结构化回合按块拼接，去掉状态块。

    状态是元数据，留在历史里会挤掉对白的最后一句，历史整形就取不到对白了。
    """
    if not blocks:
        return ""
    parts = [blocks.get(name, "") for name in structure if name != "state"]
    return chr(10).join(part for part in parts if part).strip()


def _session_scope(request: ChatRequest) -> str:
    """会话文件所在的作用域目录。

    优先用会话文件自身所在目录：会话与角色解耦后，@ 换角色时目录不变，
    用当前发言角色定位会把消息写进另一个目录，历史就断了。
    """
    if request.session:
        found = store.session_scope(request.session)
        if found:
            return found
    return request.xw.character_id or store.GLOBAL_SCOPE


def _apply_session_defaults(request: ChatRequest) -> None:
    """用会话头补齐请求缺省项。

    世界位置记在会话头上，请求里没带时按会话头的来，避免漏传导致世界书
    静默不生效。

    角色也在这里补：会话按角色分目录存放，会话头记着这个会话属于谁。
    客户端漏传 character_id 时角色卡整段不进提示，模型手里只有世界书，
    于是拿别的世界的场景说话（实测：漏传时地址L社长的回合写的是太极宫
    偏殿、红烛与龙涎香）。漏传一次就串场，所以这里必须补上。
    """
    if not request.session:
        return
    scope = store.session_scope(request.session)
    if not scope:
        return
    head = store.get_session_head(request.session, scope)
    books = [str(item) for item in (head.get("world_book_ids") or []) if str(item)]
    if books and not request.xw.world_book_ids:
        request.xw.world_book_ids = books
    region_id = str(head.get("region_id") or "")
    if region_id and not request.xw.region_id:
        request.xw.region_id = region_id
    if not str(request.xw.character_id or "").strip() and scope != store.GLOBAL_SCOPE:
        request.xw.character_id = scope


def _record_speaker(request: ChatRequest, scope: str) -> None:
    """把本轮发言角色记进会话头的角色清单。

    一个会话里可以 @ 多个角色，清单供会话列表分组与在场提示使用。
    """
    character_id = request.xw.character_id
    if not character_id or not request.session:
        return
    head = store.get_session_head(request.session, scope)
    roster = [str(item) for item in (head.get("characters") or []) if str(item)]
    if character_id in roster:
        return
    roster.append(character_id)
    store.update_session_meta(request.session, scope, {"characters": roster})


def _filter_state_items(
    blocks: dict[str, str] | None,
    xw,
    evidence: str = "",
) -> dict[str, str] | None:
    """状态块里的场景陈设按角色卡与世界书过滤。

    模型会在状态行里写自己编的物件（实测「金笼」「龙床」这类隐喻代称与
    凭空家具）。装配回灌与右侧面板都已经过滤过，消息里的状态行
    按同一套依据过滤，三处显示因此一致。
    """
    if not blocks:
        return blocks
    text = blocks.get("state")
    if not text:
        return blocks
    fields, _rest = split_state_block(text)
    if not fields:
        return blocks
    cleaned = clean_state_fields(fields, xw, evidence)
    updated = dict(blocks)
    updated["state"] = render_state_block(cleaned)
    return updated


def _drop_dialogue(blocks: dict[str, str] | None) -> dict[str, str] | None:
    """场景推进轮丢掉对白块。

    describe 模式这一轮只写场景描写。模型有时仍会写一句台词，这里连同
    消息气泡、落盘流水与分支树里的对白一起去掉，三处显示保持一致。
    """
    if not blocks or "dialogue" not in blocks:
        return blocks
    updated = dict(blocks)
    updated.pop("dialogue", None)
    return updated


def _update_state(
    character_id: str | None,
    blocks: dict[str, str] | None,
    structure: list[str],
) -> dict | None:
    """解析状态块并更新角色级世界状态，返回对外形状。

    状态按角色存：每个人有自己的记忆与所在位置。同一段对话里 @ 换人时，
    看的是另一个人的位置与随身物品，世界本身是所有人共用的。
    状态块缺失或解析不出字段时返回 None，本轮不改状态。
    """
    if not blocks or "state" not in structure:
        return None
    parsed = parse_state_block(blocks.get("state", ""))
    if not parsed:
        return None
    scope = character_id or store.GLOBAL_SCOPE
    merged = merge_state(store.get_state(scope), parsed)
    saved = store.save_state(merged, scope)
    return state_payload(saved, scope)


def _persist_turn(
    request: ChatRequest,
    user_text: str,
    assistant_text: str,
    blocks: dict[str, str] | None = None,
    branch_point: str = "",
) -> None:
    """把本轮问答写入会话流水，并挂到分支点上。

    会话流水是装配管线「历史消息」段的唯一来源。不落盘则每轮都是冷启动，
    上下文里只剩 system 与本轮输入，模型既接不住上文也容易回落到预设示例。

    branch_point 是本轮新消息的父节点。改写时先写新的用户输入，角色
    回复再挂在它下面，新旧两版互为兄弟。父节点上已有同内容的用户输入时
    复用它，避免重试时重复写入同一轮输入。

    重新生成不走这条路：它在原地替换被改那条回复，详见 _persist_regenerated。
    """
    session_id = request.session
    if not session_id or not assistant_text.strip():
        return
    scope = _session_scope(request)
    store.ensure_session(session_id, scope, request.xw.preset_id)
    if request.turn_action == "regenerate":
        _persist_regenerated(request, assistant_text, blocks, scope)
        return
    tree = store.session_tree(session_id, scope)
    nodes = tree.get("nodes") or {}
    # 改写的父节点就是被改那条输入的父节点，可能是根（空串）。
    # 这里不能拿 head 兜底：第一条输入的父节点是根，兜底会把新分支接到末尾。
    if request.turn_action == "revise":
        parent = branch_point
    else:
        parent = branch_point or str(tree.get("head") or "")
    if request.xw.auto_turn:
        # 自动回合：用户这一轮没有说话，不落空的用户消息。
        # 空消息会在会话里留下一条没有内容的用户气泡，界面上像掉了一次输入。
        user_id = parent
    else:
        parent_node = nodes.get(parent) or {}
        same_user = (
            parent_node.get("role") == "user"
            and str(parent_node.get("content") or "") == user_text
        )
        user_id = parent
        if not same_user:
            created = store.branch_message(
                session_id,
                scope,
                parent,
                {"role": "user", "content": user_text},
            )
            user_id = str(created.get("id") or "")
    row: dict = {"role": "assistant", "content": assistant_text}
    if blocks:
        row["blocks"] = blocks
    store.branch_message(session_id, scope, user_id, row)
    _record_speaker(request, scope)
    _auto_intro_characters(request, scope, user_text, assistant_text)


def _auto_intro_characters(
    request: ChatRequest,
    scope: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """回合末抽新出现的人物，自动落成配角卡，并排一次 AI 补全。

    建卡是自动的：名字抽到就落卡，卡上记着是谁带出来的（用户 / 模型）
    与关系程度（相逢 / 相识 / 相熟）。三层设定与状态交给模型补全，
    补全前这张卡是待完善的，不进 supporting 段。

    这一步失败只记日志，不影响这一轮对话。
    """
    character_id = request.xw.character_id
    session_id = request.session or ""
    if not character_id or not session_id:
        return
    try:
        from app.cards.auto_fill import schedule_fill
        from app.cards.auto_intro import collect_new_characters

        history: list[str] = []
        try:
            for item in store.get_messages(session_id, scope)[-6:]:
                history.append(str(item.get("content") or ""))
        except Exception:
            history = []
        cards = collect_new_characters(
            character_id,
            session_id,
            scope,
            user_text,
            assistant_text,
            history,
        )
        context = user_text + "\n" + assistant_text
        for card in cards:
            schedule_fill(str(card.get("id") or ""), context)
    except Exception as exc:
        logger.warning("角色自动建卡失败：%s", exc)


def _persist_regenerated(
    request: ChatRequest,
    assistant_text: str,
    blocks: dict[str, str] | None,
    scope: str,
) -> None:
    """重新生成：原地替换被改那条回复，并把当前路径切到它。

    重新生成不新开节点，回复占住原来的位置，树上不出现兄弟分支，
    所以重新生成本身不算分支。被替换节点之后的那几轮仍留在树上，但不在
    当前路径上；继续对话时另起一条分支，于是同一轮出现两个版本。
    """
    session_id = request.session or ""
    target = _turn_target(request, "assistant")
    if not session_id or not target:
        return
    row: dict = {"role": "assistant", "content": assistant_text}
    if blocks:
        row["blocks"] = blocks
    store.replace_message(session_id, scope, target, row)
    store.set_head(session_id, scope, target)
    _record_speaker(request, scope)


async def _sse_generate(
    adapter,
    messages: list[ChatMessage],
    params: InferenceParams,
    profile: dict | None = None,
    budget: BudgetInfo | None = None,
    tokenizer=None,
    sink: list[str] | None = None,
    single_paragraph: bool = False,
    structure: list[str] | None = None,
    raw_sink: list[str] | None = None,
    drop_blocks: list[str] | None = None,
    self_reference: str = "",
) -> AsyncIterator[tuple[str, dict]]:
    """把适配器事件流归一成事件名与数据成对产出。

    计量以装配结果与本地计数为准：llama.cpp 的流式响应不带 usage，
    上游 token 计数恒为 0。prompt_tokens 取装配回执的段合计，
    completion_tokens 由本地分词器对本轮流式正文计数得出。
    sink 非空时，流式正文全文写入其中，供调用方落盘。

    structure 非空时按结构化回合处理：标记被拆掉，每个增量带 block 字段
    指明归属的块；raw_sink 收到未经拆分的原文，供最终按块解析。

    drop_blocks 里的块不往外发增量：场景推进轮的对白不该在界面上闪过。
    原文仍进 generated，计量与复读判定照常算它。
    """
    generated: list[str] = []
    last_block = ""
    first_delta_at: float | None = None
    splitter = StructureSplitter(structure) if structure else None
    async for event in adapter.stream_chat(messages, params, profile):
        event_name = event.event
        data = event.data or {}
        if event_name == "delta":
            content = data.get("content")
            if isinstance(content, str):
                # 自称归一在增量这一层做：流式与最终落盘用的是同一份文本。
                if self_reference:
                    content = normalize_self_reference(content, self_reference)
                if content and first_delta_at is None:
                    first_delta_at = time.perf_counter()
                if splitter is not None:
                    # 结构化回合：按标记分块，标记不进正文。
                    # 原文另存一份，供最终按块解析。
                    if raw_sink is not None:
                        raw_sink.append(content)
                    pieces = splitter.feed(content)
                    if not pieces:
                        # 分片被整段留白（可能是被切开的标记），不发增量。
                        continue
                    for name, piece in pieces:
                        if generated and name != last_block:
                            # 换块时补一个换行，正文与最终块解析保持一致。
                            piece = chr(10) + piece
                        last_block = name
                        generated.append(piece)
                        if drop_blocks and name in drop_blocks:
                            # 这一块不往界面上送。
                            continue
                        item = dict(data)
                        item["content"] = piece
                        item["block"] = name
                        yield "delta", item
                    continue
                if single_paragraph:
                    content = collapse_paragraphs(content)
                    data = dict(data)
                    data["content"] = content
                generated.append(content)
        if event_name == "usage":
            data = dict(data)
            if budget is not None:
                data["prompt_tokens"] = budget.used
                data["budget"] = budget.model_dump()
            if tokenizer is not None:
                completion = tokenizer.count("".join(generated))
                data["completion_tokens"] = completion
                # 生成速率：首个增量到计量事件的解码窗口，不含提示词预填充。
                if first_delta_at is not None:
                    elapsed = time.perf_counter() - first_delta_at
                    if elapsed > 0:
                        data["generation_tps"] = round(completion / elapsed, 2)
        yield event_name, data

    if splitter is not None:
        # 流尾可能还压着半个标记，flush 把它归到当前块。
        for name, piece in splitter.flush():
            if generated and name != last_block:
                piece = chr(10) + piece
            last_block = name
            generated.append(piece)
            if drop_blocks and name in drop_blocks:
                continue
            yield "delta", {"content": piece, "block": name}

    if sink is not None:
        sink.append(drop_annotation_lines("".join(generated)))


def _turn_target(request: ChatRequest, role: str) -> str:
    """本轮要改写的节点 id。

    重新生成针对角色回复，改写针对用户输入。客户端可以指定节点，指定
    的节点角色对不上时按没有目标处理。没指定时取当前路径上最后一个对应
    角色的节点。
    """
    session_id = request.session
    if not session_id:
        return ""
    scope = _session_scope(request)
    tree = store.session_tree(session_id, scope)
    nodes = tree.get("nodes") or {}
    target = str(request.target_id or "")
    if target:
        node = nodes.get(target) or {}
        return target if node.get("role") == role else ""
    current = str(tree.get("head") or "")
    while current and current in nodes:
        node = nodes[current]
        if node.get("role") == role:
            return current
        current = str(node.get("parent_id") or "")
    return ""


def _branch_point(request: ChatRequest) -> str:
    """算出本轮新消息挂在哪个父节点下。

    追加挂在当前 head。重新生成与改写都停在被改写那条消息的父节点上：
    重新生成在原地替换那条回复，不新增节点；改写新开一条用户输入，与旧
    输入互为兄弟。

    装配也要停在这个点上，否则历史段会把刚要替换掉的那一轮读进来，模型
    会接着上一轮继续写。
    """
    session_id = request.session
    if not session_id:
        return ""
    scope = _session_scope(request)
    tree = store.session_tree(session_id, scope)
    head = str(tree.get("head") or "")
    nodes = tree.get("nodes") or {}
    action = request.turn_action
    if action in ("regenerate", "revise"):
        role = "assistant" if action == "regenerate" else "user"
        target = _turn_target(request, role)
        node = nodes.get(target) or {}
        if not target:
            return head
        return str(node.get("parent_id") or "")
    return head


def _session_snapshot(
    session_id: str | None,
    scope: str,
    structure: list[str],
) -> dict[str, Any]:
    """会话快照：当前路径与整棵分支树，客户端据此重建消息列表。"""
    snapshot = store.session_snapshot(session_id or "", scope)
    snapshot["labels"] = display_labels(structure)
    return snapshot


@router.post("/chat/completions")
async def chat_completions(request: ChatRequest):
    """会话生成端点。

    非流式返回 ChatResponse；流式返回 SSE 事件流。
    """
    # 先算出本轮消息挂在哪个父节点下，再装配。装配必须停在分支点上，
    # 否则历史段会把刚要替换掉的那一轮读进来，模型会接着上一轮继续写。
    _apply_session_defaults(request)
    branch_point = _branch_point(request)
    # 执行装配（数据经 app.store 读取）
    ctx = assemble(request, leaf_id=branch_point)

    # 构建推理参数
    params = _build_params(request)

    # 获取 profile
    profile = _load_profile(request)

    # 获取适配器
    adapter = _get_adapter()

    # 回合是否要求写成完整一段
    single_paragraph = _single_paragraph(request.xw)

    # 角色卡声明的自称：台词里的帝王自称按声明换掉
    self_reference = _card_self_reference(_speaking_character_id(request))

    # 回合是否按标记分块输出
    structure = _structure_names(request.xw)

    # 回合字数下限，来自生效预设的 output_spec
    min_chars = _min_chars(request.xw)

    # 复读闸门用：上一轮的角色回复
    scope = _session_scope(request)
    previous_text = _last_assistant_text(request.session, scope)

    if request.stream:
        total_tokens = sum(seg.tokens for seg in ctx.report.segments)
        stream_budget = BudgetInfo(
            limit=request.xw.max_context_tokens,
            reserved_output=RESERVED_OUTPUT_TOKENS,
            used=total_tokens,
        )
        stream_tokenizer = resolve_tokenizer()

        async def _event_stream() -> AsyncIterator[str]:
            # 先发送 context 事件（装配结果）
            context_data = ctx.report.model_dump()
            yield f"event: context\ndata: {json.dumps(context_data, ensure_ascii=False)}\n\n"

            attempt = 0
            current_params = params
            current_messages = ctx.messages
            best_text = ""
            best_raw = ""
            finish: dict = {"finish_reason": "stop"}
            final_text = ""
            final_raw = ""

            while True:
                # 发送推理事件
                sink: list[str] = []
                raw_sink: list[str] = []
                async for event_name, data in _sse_generate(
                    adapter,
                    current_messages,
                    current_params,
                    profile,
                    stream_budget,
                    stream_tokenizer,
                    sink,
                    single_paragraph,
                    structure,
                    raw_sink,
                    ["dialogue"] if request.xw.auto_turn == "describe" else None,
                    self_reference,
                ):
                    if event_name == "done":
                        finish = data
                        continue
                    payload = json.dumps(data, ensure_ascii=False)
                    yield f"event: {event_name}\ndata: {payload}\n\n"

                text = "".join(sink)
                raw_text = "".join(raw_sink)
                blocks_try = (
                    _normalize_blocks(split_blocks(raw_text, structure), structure)
                    if structure
                    else {}
                )
                # 长度按正文算：状态块是元数据，不占正文字数。
                body_chars = _body_chars(blocks_try, structure) if structure else len(text)

                # 候选择优：更长的、不照搬上一轮的；结构有问题的候选不参与竞选。
                # 状态行漏进正文块时正文更长，不排除就会选中它。
                if (
                    not is_repetitive(text, previous_text)
                    and not _missing_blocks(blocks_try, structure)
                    and len(text) > len(best_text)
                ):
                    best_text = text
                    best_raw = raw_text

                if attempt == 0:
                    if structure:
                        missing = _missing_blocks(blocks_try, structure)
                        if missing:
                            # 缺块：让模型按块重写一次。
                            attempt += 1
                            current_params = _retry_params(
                                params, attempt, temperature=LENGTH_RETRY_TEMPERATURE
                            )
                            current_messages = _with_missing_note(ctx.messages, missing)
                            payload = json.dumps({"reason": "structure"}, ensure_ascii=False)
                            yield f"event: replace\ndata: {payload}\n\n"
                            continue
                    role_owner = _role_owner_violation(
                        blocks_try, structure, _character_name(request.xw)
                    )
                    if role_owner:
                        # 角色归属越界：替用户写了动作或台词，重写一次。
                        attempt += 1
                        current_params = _retry_params(
                            params, attempt, temperature=LENGTH_RETRY_TEMPERATURE
                        )
                        current_messages = _with_role_owner_note(ctx.messages, role_owner)
                        payload = json.dumps({"reason": "role_owner"}, ensure_ascii=False)
                        yield f"event: replace\ndata: {payload}\n\n"
                        continue
                    if _dialogue_uptake_short(
                        blocks_try, structure, request.xw.auto_turn
                    ):
                        # 对白接不住用户这一轮：重写一次，要求写两句并点名回应。
                        attempt += 1
                        current_params = _retry_params(
                            params, attempt, temperature=LENGTH_RETRY_TEMPERATURE
                        )
                        current_messages = _with_dialogue_note(
                            ctx.messages,
                            _last_user_text(request),
                            request.xw.auto_turn,
                        )
                        payload = json.dumps({"reason": "dialogue"}, ensure_ascii=False)
                        yield f"event: replace\ndata: {payload}\n\n"
                        continue
                    if _self_reference_violation(
                        blocks_try, structure, _card_self_reference(
                            _speaking_character_id(request)
                        )
                    ):
                        # 自称滑了：卡里声明的自称没被用上，重写一次。
                        attempt += 1
                        current_params = _retry_params(
                            params, attempt, temperature=LENGTH_RETRY_TEMPERATURE
                        )
                        current_messages = _with_self_reference_note(ctx.messages)
                        payload = json.dumps({"reason": "self_reference"}, ensure_ascii=False)
                        yield f"event: replace\ndata: {payload}\n\n"
                        continue
                    if is_repetitive(text, previous_text):
                        # 照搬了上一轮的整句：换种子重采样一次。
                        # 先发 replace 让客户端丢掉本轮已显示的正文。
                        attempt += 1
                        current_params = _retry_params(
                            params, attempt, temperature=REPETITION_RETRY_TEMPERATURE
                        )
                        payload = json.dumps({"reason": "repetition"}, ensure_ascii=False)
                        yield f"event: replace\ndata: {payload}\n\n"
                        continue
                    if min_chars and body_chars < min_chars:
                        # 回合短于预设下限：让模型重写一次，要求写足。
                        attempt += 1
                        current_params = _retry_params(
                            params, attempt, temperature=LENGTH_RETRY_TEMPERATURE
                        )
                        current_messages = _with_expand_note(
                            ctx.messages,
                            body_chars,
                            min_chars,
                            _length_detail(
                                request.xw, blocks_try, structure, min_chars
                            ),
                        )
                        payload = json.dumps({"reason": "length"}, ensure_ascii=False)
                        yield f"event: replace\ndata: {payload}\n\n"
                        continue

                final_text = best_text or text
                final_raw = drop_annotation_lines(best_raw or raw_text)
                break

            blocks = None
            if structure:
                # 结构化回合的最终块。以选中的候选原文为准，与客户端显示一致。
                blocks = _normalize_blocks(split_blocks(final_raw, structure), structure)
                # 场景推进轮不带对白：气泡、落盘与分支树三处一致。
                if request.xw.auto_turn == "describe":
                    blocks = _drop_dialogue(blocks)
                # 状态行里的场景陈设按依据过滤，与落盘状态和右侧面板一致。
                blocks = _filter_state_items(
                    blocks,
                    request.xw,
                    user_evidence_text(request, branch_point),
                )
                payload = json.dumps(
                    {"blocks": blocks, "labels": display_labels(structure)},
                    ensure_ascii=False,
                )
                yield f"event: blocks\ndata: {payload}\n\n"

            # 世界状态：模型这轮声明的状态解析后落盘，并推给客户端右侧面板。
            state = _update_state(request.xw.character_id, blocks, structure)
            if state is not None:
                # 被过滤掉的陈设一并推给前端：模型编出来的东西要让人看见，
                # 不然用户只会觉得道具凭空消失。
                dropped = dropped_state_items(
                    request.xw,
                    user_evidence_text(request, branch_point),
                )
                state_payload_data = json.dumps(
                    {"state": state, "dropped_items": dropped},
                    ensure_ascii=False,
                )
                yield f"event: state\ndata: {state_payload_data}\n\n"

            # 流结束后落盘本轮问答，供下一轮装配的历史消息段使用。
            # 正文去掉状态块：状态是元数据，留在历史里会挤掉对白的最后一句。
            persist_text = _reply_text(blocks, structure) if structure else final_text
            _persist_turn(
                request,
                _last_user_text(request),
                persist_text,
                blocks,
                branch_point,
            )

            # 会话路径与分支树：客户端按此重建消息列表与分支视图。
            snapshot = _session_snapshot(request.session, scope, structure)
            payload = json.dumps(snapshot, ensure_ascii=False)
            yield f"event: path\ndata: {payload}\n\n"

            payload = json.dumps(finish, ensure_ascii=False)
            yield f"event: done\ndata: {payload}\n\n"

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # 非流式
    started_at = time.perf_counter()
    try:
        result = await adapter.chat(ctx.messages, params, profile)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            code="inference_error",
            message=str(exc),
            retryable=False,
            status_code=502,
        )

    # 本轮正文；结构化回合拆块，普通回合按需压平段落
    reply_text, blocks = _reply_parts(
        result.content, structure, single_paragraph, self_reference
    )

    # 对白承接闸门：对白短到接不住用户这一轮时重写一次。
    # 排在复读闸门之前：接不住用户比照搬更致命。
    missing_blocks = _missing_blocks(blocks, structure) if (structure and blocks) else []
    if missing_blocks:
        # 缺块：让模型按块重写一次。
        try:
            retry = await adapter.chat(
                _with_missing_note(ctx.messages, missing_blocks),
                _retry_params(params, 1, temperature=LENGTH_RETRY_TEMPERATURE),
                profile,
            )
        except Exception:  # noqa: BLE001 - 重写失败时保留首轮结果
            retry = None
        if retry is not None:
            candidate, candidate_blocks = _reply_parts(
                retry.content, structure, single_paragraph, self_reference
            )
            if not is_repetitive(candidate, previous_text) and not _missing_blocks(
                candidate_blocks, structure
            ):
                result = retry
                reply_text = candidate
                blocks = candidate_blocks
    elif _self_reference_violation(
        blocks, structure, _card_self_reference(_speaking_character_id(request))
    ):
        # 自称滑了：卡里声明的自称没被用上，重写一次。
        try:
            retry = await adapter.chat(
                _with_self_reference_note(ctx.messages),
                _retry_params(params, 1, temperature=LENGTH_RETRY_TEMPERATURE),
                profile,
            )
        except Exception:  # noqa: BLE001 - 重写失败时保留首轮结果
            retry = None
        if retry is not None:
            candidate, candidate_blocks = _reply_parts(
                retry.content, structure, single_paragraph, self_reference
            )
            if (
                not is_repetitive(candidate, previous_text)
                and not _self_reference_violation(
                    candidate_blocks,
                    structure,
                    _card_self_reference(_speaking_character_id(request)),
                )
                and not _missing_blocks(candidate_blocks, structure)
            ):
                result = retry
                reply_text = candidate
                blocks = candidate_blocks
    elif _role_owner_violation(blocks, structure, _character_name(request.xw)):
        # 角色归属越界：替用户写了动作或台词，重写一次。
        try:
            retry = await adapter.chat(
                _with_role_owner_note(
                    ctx.messages,
                    _role_owner_violation(
                        blocks, structure, _character_name(request.xw)
                    ),
                ),
                _retry_params(params, 1, temperature=LENGTH_RETRY_TEMPERATURE),
                profile,
            )
        except Exception:  # noqa: BLE001 - 重写失败时保留首轮结果
            retry = None
        if retry is not None:
            candidate, candidate_blocks = _reply_parts(
                retry.content, structure, single_paragraph, self_reference
            )
            if (
                not is_repetitive(candidate, previous_text)
                and not _role_owner_violation(
                    candidate_blocks, structure, _character_name(request.xw)
                )
                and not _missing_blocks(candidate_blocks, structure)
            ):
                result = retry
                reply_text = candidate
                blocks = candidate_blocks
    elif _dialogue_uptake_short(blocks, structure, request.xw.auto_turn):
        try:
            retry = await adapter.chat(
                _with_dialogue_note(
                    ctx.messages, _last_user_text(request), request.xw.auto_turn
                ),
                _retry_params(params, 1, temperature=LENGTH_RETRY_TEMPERATURE),
                profile,
            )
        except Exception:  # noqa: BLE001 - 重写失败时保留首轮结果
            retry = None
        if retry is not None:
            candidate, candidate_blocks = _reply_parts(
                retry.content, structure, single_paragraph, self_reference
            )
            if (
                not is_repetitive(candidate, previous_text)
                and not _dialogue_uptake_short(candidate_blocks, structure)
                and not _missing_blocks(candidate_blocks, structure)
            ):
                result = retry
                reply_text = candidate
                blocks = candidate_blocks
    elif min_chars and len(reply_text) < min_chars:
        # 长度闸门：回合短于预设下限，重写一次要求写足
        try:
            retry = await adapter.chat(
                _with_expand_note(
                    ctx.messages,
                    len(reply_text),
                    min_chars,
                    _length_detail(request.xw, blocks, structure, min_chars),
                ),
                _retry_params(params, 1, temperature=LENGTH_RETRY_TEMPERATURE),
                profile,
            )
        except Exception:  # noqa: BLE001 - 重写失败时保留首轮结果
            retry = None
        if retry is not None:
            candidate, candidate_blocks = _reply_parts(
                retry.content, structure, single_paragraph, self_reference
            )
            if len(candidate) > len(reply_text) and not is_repetitive(candidate, previous_text):
                result = retry
                reply_text = candidate
                blocks = candidate_blocks

    # 复读闸门：照搬上一轮时换种子重采样一次，重采样结果仍照搬则保留首轮。
    #
    # 单独跑，不挂在上面的 elif 链里：前面的闸门（缺块、角色归属、对白承接）
    # 一旦先跑，复读闸门就被跳过，它们重写出来的正文照搬上一轮时也没人管。
    # 实测（backend/tools/probe_story_run.py）：角色上一轮说「别发愣啊……
    # 听懂了吗？给我把腰挺直了」，下一轮整段照搬四十多字，闸门一次没触发。
    #
    # 重采样时把照搬的那一段点出来：只换种子带不动，上一轮的原话就在历史里，
    # 模型顺着它续写。
    if is_repetitive(reply_text, previous_text):
        try:
            retry = await adapter.chat(
                _with_repeat_note(
                    ctx.messages, copied_fragment(reply_text, previous_text)
                ),
                _retry_params(params, 1),
                profile,
            )
        except Exception:  # noqa: BLE001 - 重采样失败时保留首轮结果
            retry = None
        if retry is not None:
            candidate, candidate_blocks = _reply_parts(
                retry.content, structure, single_paragraph, self_reference
            )
            if not is_repetitive(candidate, previous_text):
                result = retry
                reply_text = candidate
                blocks = candidate_blocks

    # 场景推进轮不带对白：正文与块一起去掉对白，三处显示保持一致。
    if request.xw.auto_turn == "describe" and blocks:
        blocks = _drop_dialogue(blocks)
        reply_text = _reply_text(blocks, structure)

    # 状态行里的场景陈设按依据过滤，与落盘状态和右侧面板一致。
    blocks = _filter_state_items(
        blocks,
        request.xw,
        user_evidence_text(request, branch_point),
    )

    # 世界状态：模型这轮声明的状态解析后落盘
    state = _update_state(request.xw.character_id, blocks, structure)

    # 落盘本轮问答，供下一轮装配的历史消息段使用。正文去掉状态块。
    persist_text = _reply_text(blocks, structure) if structure else reply_text
    _persist_turn(
        request,
        _last_user_text(request),
        persist_text,
        blocks,
        branch_point,
    )
    snapshot = _session_snapshot(request.session, scope, structure)

    # 构建 Usage
    total_tokens = sum(seg.tokens for seg in ctx.report.segments)
    budget = BudgetInfo(
        limit=request.xw.max_context_tokens,
        reserved_output=RESERVED_OUTPUT_TOKENS,
        used=total_tokens,
    )
    completion_tokens = result.usage.completion_tokens if result.usage else 0
    elapsed = time.perf_counter() - started_at
    usage = Usage(
        prompt_tokens=total_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=result.usage.reasoning_tokens if result.usage else 0,
        cached_tokens=result.usage.cached_tokens if result.usage else 0,
        budget=budget,
        # 非流式口径：整个调用耗时，含提示词预填充，与流式的解码窗口不同。
        generation_tps=(
            round(completion_tokens / elapsed, 2) if elapsed > 0 and completion_tokens else None
        ),
    )

    response = ChatResponse(
        id=f"cmpl_{uuid.uuid4().hex[:12]}",
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content=reply_text,
                    reasoning=result.reasoning,
                    blocks=blocks,
                ),
                finish_reason=result.finish_reason,
            )
        ],
        usage=usage,
        xw={
            "context_report": ctx.report.model_dump(),
            "state": state,
            "dropped_items": dropped_state_items(
                request.xw,
                user_evidence_text(request, branch_point),
            ),
            "path": snapshot,
        },
    )
    return response.model_dump()


@router.post("/chat/edit")
async def edit_turn(request: EditRequest) -> JSONResponse:
    """编辑角色回复并定稿。

    content 是完整的编辑源文本，含场景、对白与状态三段。后端按生效预设
    声明的结构解析：正文进气泡，状态块写进世界状态。新回复挂在被改那一轮
    的父节点下，与旧回复互为兄弟分支，可切回。数据层只追加不删除。
    """
    session_id = request.session
    if not session_id:
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    scope = _session_scope(request)
    tree = store.session_tree(session_id, scope)
    parent_id = request.parent_id
    if parent_id and parent_id not in (tree.get("nodes") or {}):
        raise ApiError(code="session_node_not_found", message="父节点不存在", status_code=404)
    structure = _structure_names(request.xw)
    blocks: dict[str, str] | None = None
    body = request.content
    if structure:
        blocks = _normalize_blocks(split_blocks(request.content, structure), structure)
        body = _reply_text(blocks, structure)
    row: dict[str, Any] = {"role": "assistant", "content": body}
    if blocks:
        row["blocks"] = blocks
    created = store.branch_message(session_id, scope, parent_id, row)
    state = _update_state(request.xw.character_id, blocks, structure)
    snapshot = _session_snapshot(session_id, scope, structure)
    snapshot["state"] = state
    return JSONResponse(
        content={
            "message": created,
            "head_id": snapshot["head_id"],
            "items": snapshot["items"],
            "tree": snapshot["tree"],
            "labels": snapshot["labels"],
            "state": state,
            "count": snapshot["count"],
        }
    )


def _speaking_character_id(request: ChatRequest) -> str:
    """本轮发言的角色 id。

    请求里带了 character_id 就用它；没带时按会话所在的作用域目录找——
    会话按角色分目录存放，目录名就是角色 id。客户端漏传这一项时，角色
    专属的声线向量仍然挂得上。
    """
    character_id = str(request.xw.character_id or "").strip()
    if character_id:
        return character_id
    scope = _session_scope(request)
    if scope and scope != store.GLOBAL_SCOPE:
        return scope
    return ""


# 卡片里声明自称的写法：「她自称「我」」。
# 否定式（不自称「朕」）不算声明，所以前面带否定词的匹配要跳过。
SELF_REFERENCE_PATTERN = re.compile(
    r"(?<![不勿别])自称[：: ]*[\u300c\"']([^\u300c\u300d\"']{1,4})[\u300d\"']"
)


def _card_self_reference(character_id: str | None) -> str:
    """角色卡声明的自称。没声明就返回空串。

    先看卡里的 self_reference 字段，没有就从说话方式层里读「她自称「X」」
    这一句。前者被编辑器丢掉时后者仍然管用。
    """
    if not character_id:
        return ""
    card = store.get_card(character_id)
    if not isinstance(card, dict):
        return ""
    payload = card.get("data")
    if not isinstance(payload, dict):
        payload = card
    declared = str(payload.get("self_reference") or "").strip()
    if declared:
        return declared
    match = SELF_REFERENCE_PATTERN.search(str(payload.get("speech") or ""))
    return match.group(1).strip() if match else ""


def _build_params(request: ChatRequest) -> InferenceParams:
    """从请求构建推理参数。

    max_tokens 取 768：回合要写场景、对白与状态三块，留足余量。
    实测输出 token 最高 313，512 并未被顶到，768 只是余量不是瓶颈。
    """
    # 角色专属的声线向量随请求走：推理端按请求挂载，跑完这一轮再按需
    # 换回下一个角色的向量。角色没挂向量时这一项不出现。
    extra: dict = {**SAMPLING_EXTRA, "seed": secrets.randbelow(2**31)}
    control = voice_config(_speaking_character_id(request))
    if control:
        extra["control_vector"] = control
    return InferenceParams(
        temperature=SAMPLING_TEMPERATURE,
        top_p=SAMPLING_TOP_P,
        max_tokens=768,
        repetition_penalty=1.1,
        # 每轮换一个随机种子。请求里不给种子时，推理服务用的是它自己的
        # 固定默认种子，于是同一段上下文永远生成同一段文本：重新生成点两次
        # 结果一样，同一场戏重开还是老走法。发散得先有随机的入口。
        # 实测见 docs/理论支持/提示层审计.md 第 7 节。
        extra=extra,
    )


def _load_profile(request: ChatRequest) -> dict | None:
    """加载 profile 参数。

    优先用 settings 中的 active_model.profile_id 定位 profile；缺失时回退到
    最近更新的一个 profile。
    """
    profile_id = ""
    active = store.get_setting("active_model")
    if isinstance(active, dict):
        profile_id = str(active.get("profile_id") or "")

    if profile_id:
        profile = store.get_profile(profile_id)
        if isinstance(profile, dict):
            params = profile.get("params")
            return params if isinstance(params, dict) else None

    profiles = store.list_profiles()
    if profiles:
        params = profiles[-1].get("params")
        return params if isinstance(params, dict) else None
    return None
