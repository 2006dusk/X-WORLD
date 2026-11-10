"""上下文装配管线。

固定 9 段装配顺序：
1. system — 预设的 system 提示组
2. character — 角色卡的 identity、behavior、speech、tags
3. persona — 用户档案：对面是谁
4. preset_prompts — 预设其余提示组，按 order 排列
5. post_history — 角色卡 post_history_instructions
6. world_info — 世界书匹配命中条目
7. memory — 记忆检索结果
8. history — 会话历史消息
9. user — 本轮输入

产出 AssembledContext（含 segments、messages、template、tokenizer、truncated）。

数据来源：一律经 app.store 读取，本模块不 import app.db、不使用 sqlite3，
也不直接访问数据目录。装配顺序、预算策略、世界书匹配规则与 tokenizer 回退
行为均保持不变。

memory 段当前承载角色级世界状态：模型每轮自动更新，解析后落盘，这里读回
回灌给模型，让下一轮接着上轮的位置与物品写。没有状态时该段为空段
（tokens 为 0），段集合长度恒为 8。
"""

from __future__ import annotations

import re
from typing import Any

from app import presets as preset_service
from app import store
from app.cards.spec import ROLE_SUPPORTING, normalize_role, normalize_tags, to_internal_v1
from app.contracts import (
    ChatMessage,
    ChatRequest,
    ContextReport,
    ContextSegment,
    XwOptions,
)

from .budget import SegmentData, trim_to_budget
from ..inference.repetition import split_sentences
from ..state import render_state_block
from .matching import MatchedEntry, match_entries
from .tokenizer import Tokenizer, get_tokenizer

SEGMENT_ORDER: tuple[str, ...] = (
    "system",
    "supporting",
    "character",
    "persona",
    "preset_prompts",
    "post_history",
    "world_info",
    "memory",
    "history",
    "user",
)

# 段里的字面量。配角段与角色段被探针、测试按这些串定位，字面量只在这里
# 定义一处，别处引用常量，不要再抄一份。
SUPPORTING_HEADER = "场上还有别人在场"
SUPPORTING_BOUNDARY = "叙述视角仍然是主角"
CHARACTER_LABEL = "角色："
TAG_CONSTRAINT = "角色类型标签（必须符合）"

# 角色卡的三层设定：标签与字段的对应只写一份，主角段与配角段共用。
CARD_LAYERS: tuple[tuple[str, str], ...] = (
    ("身份与处境", "identity"),
    ("行为与细节", "behavior"),
    ("说话方式", "speech"),
)


def resolve_tokenizer() -> Tokenizer:
    """按生效档案选择分词器。

    档案由 app.inference.traits.resolve_profile 解析：档案声明
    tokenizer_type 为 llamacpp 时用服务端真实分词器，其余情况用字符启发式估算。
    档案里没写 tokenizer_type 时取内置默认档的值。
    """
    from app.inference.traits import resolve_profile

    return get_tokenizer(resolve_profile().to_dict())

DEFAULT_TEMPLATE = "chatml"
RESERVED_OUTPUT_TOKENS = 1024

# 历史整形参数：保留最近几轮，角色回复压成最后一句，末尾回退字数。
# 保留轮数从两轮提到四轮。两轮时模型手里只有一句上文，场景全靠状态块猜，
# 用户这一轮的输入常被当成剧情素材而不是要回应的话（见 docs/实践情况
# 报告 7.24 节的回应承接对照）。轮数可用设置 history_policy.keep_turns 覆盖。
HISTORY_KEEP_TURNS = 4
HISTORY_TAIL_CHARS = 40
# 角色回复在历史里保留的场景上限。
# 场景不能全文进历史：模型会照着上一轮的场景改几个字继续写，实测连续四轮
# 场景几乎逐字相同（backend/tools/probe_scene_continuity.py）。
# 只留场景末尾的动作结果句，地点与物品由 memory 段的世界状态承担，
# 既不丢接戏依据，也不给模型可照抄的叙述文本。
HISTORY_SCENE_CHARS = 120
# 历史里没有状态可留时的固定标记。
NO_SPEECH_MARKER = "（这一轮没有台词）"

# 历史里保留的台词长度上限。台词是对话线索，但要防止模型写成长篇独白
# 把上下文撑爆；超长时保留末尾，末尾是最新的一句话。
HISTORY_DIALOGUE_CHARS = 160


class AssembledContext:
    """装配完成后的上下文产物。"""

    __slots__ = (
        "report",
        "messages",
        "template",
        "tokenizer",
        "truncated",
    )

    def __init__(
        self,
        report: ContextReport,
        messages: list[ChatMessage],
        template: str,
        tokenizer: Tokenizer,
        truncated: bool = False,
    ) -> None:
        self.report = report
        self.messages = messages
        self.template = template
        self.tokenizer = tokenizer
        self.truncated = truncated


def assemble(
    request: ChatRequest,
    leaf_id: str | None = None,
) -> AssembledContext:
    """执行上下文装配，返回 AssembledContext。

    参数：
        request: 会话生成请求；数据一律经 app.store 读取。
        leaf_id: 历史段的路径终点。重新生成与改写要排除被替换的那一轮，
            装配时必须停在分支点上，否则模型会把旧回复当上文接着写。

    返回：
        AssembledContext，包含 report 与最终消息序列。
    """
    xw: XwOptions = request.xw
    tokenizer = resolve_tokenizer()
    template = DEFAULT_TEMPLATE

    # 收集各段数据
    segments_data: list[SegmentData] = []

    # 本轮输入与历史先算出来：配角段按名字检索它们，检索范围是本轮输入
    # 加上最近的历史。提到就回来，也保证她在场时不凭空消失。
    user_text = _build_user_text(request)
    history_items = _build_history(request, leaf_id)

    # --- 1. system ---
    system_text = _build_system(xw)
    system_seg = SegmentData(name="system", items=[{"content": system_text}], fixed=True)
    system_seg.tokens = tokenizer.count(system_text)
    segments_data.append(system_seg)

    # --- 2. supporting ---
    # 配角段排在 character 段之前：SillyTavern 的注入位置口径里，
    # Before Char Defs 的影响比 After Char Defs 弱。配角压过主角是最糟的
    # 失败模式，所以配角放弱位。
    supporting_text = _build_supporting(xw, user_text, history_items)
    sup_seg = SegmentData(
        name="supporting",
        items=[{"content": supporting_text}] if supporting_text.strip() else [],
        fixed=False,
    )
    sup_seg.tokens = tokenizer.count(supporting_text)
    segments_data.append(sup_seg)

    # --- 3. character ---
    character_text = _build_character(xw)
    char_seg = SegmentData(
        name="character",
        items=[{"content": character_text}],
        fixed=True,
    )
    char_seg.tokens = tokenizer.count(character_text)
    segments_data.append(char_seg)

    # --- 4. persona ---
    # 用户档案段：告诉模型对面是谁。SillyTavern 有独立的 Persona Description 段，
    # 缺了它模型只能猜，表现为替用户安排动作与心理。
    persona_text = _build_persona(xw)
    persona_seg = SegmentData(
        name="persona",
        items=[{"content": persona_text}] if persona_text.strip() else [],
        fixed=False,
    )
    persona_seg.tokens = tokenizer.count(persona_text)
    segments_data.append(persona_seg)

    # --- 5. preset_prompts ---
    preset_items = _build_preset_prompts(xw)
    preset_text = "\n".join(item.get("content", "") for item in preset_items)
    preset_seg = SegmentData(name="preset_prompts", items=preset_items, fixed=False)
    preset_seg.tokens = tokenizer.count(preset_text)
    segments_data.append(preset_seg)

    # --- 6. post_history ---
    post_history_text = _build_post_history(xw)
    post_seg = SegmentData(
        name="post_history",
        items=[{"content": post_history_text}] if post_history_text.strip() else [],
        fixed=False,
    )
    post_seg.tokens = tokenizer.count(post_history_text)
    segments_data.append(post_seg)

    # --- 7. world_info ---
    matched = _build_world_info(xw, user_text)
    wi_items = [
        {"content": m.content, "priority": m.priority, "entry_id": m.entry_id}
        for m in matched
    ]
    wi_text = "\n".join(m.content for m in matched)
    wi_seg = SegmentData(name="world_info", items=wi_items, fixed=False)
    wi_seg.tokens = tokenizer.count(wi_text)
    segments_data.append(wi_seg)

    # --- 8. memory ---
    # 用户自己说过的话是「这个道具该不该留」的判据之一。
    evidence = _user_evidence(request, history_items)
    memory_items = _build_memory(xw, evidence)
    mem_text = "\n".join(item.get("content", "") for item in memory_items)
    mem_seg = SegmentData(name="memory", items=memory_items, fixed=False)
    mem_seg.tokens = tokenizer.count(mem_text)
    segments_data.append(mem_seg)

    # --- 9. history ---
    hist_text = "\n".join(
        f"{item.get('role', '')}: {item.get('content', '')}"
        for item in history_items
    )
    hist_seg = SegmentData(name="history", items=history_items, fixed=False)
    hist_seg.tokens = tokenizer.count(hist_text)
    segments_data.append(hist_seg)

    # --- 10. user ---
    # 回合要求附在本轮输入末尾：实测表明长度约束只写在 system 段会被弱化，
    # 放在最后一轮输入里模型才会照办。
    auto = str(xw.auto_turn or "")
    user_content = user_text
    directive = _output_directive(xw)
    if auto:
        # 自动回合：用户这一轮没有说话，user 段装的是给模型的指令，不是用户
        # 输入。上一轮的用户原话不能带进来，否则模型会当成用户刚说过。
        user_content = _auto_turn_directive(auto)
        if directive:
            user_content = user_content + chr(10) + chr(10) + directive
    elif directive and user_text.strip():
        user_content = f"{user_text}\n\n{directive}"
    # 世界状态同样贴在本轮输入末尾。位置与物品只写在 system 段时，模型会
    # 忽略它：实测（backend/tools/probe_scene_continuity.py）状态里位置是
    # 「地址N_地址V」，正文却写「走在湿漉漉的人行道上」。
    # 紧邻本轮输入的「当前状态」段落才会被当成本轮的事实依据。
    state_hint = _state_hint(xw, evidence)
    if state_hint and (user_text.strip() or auto):
        user_content = user_content + chr(10) + chr(10) + state_hint
    # 括号即剧情：用户圆括号里写的是他指定的动作。
    # 放在本轮输入最后，紧挨生成点。实测（backend/tools/probe_paren_v3.py）：
    # 这句单独加没用（括号落实率 0），必须与动作块口径一起上；
    # 放在末尾时落实率最高（0.25 到 0.75）。
    # 指令内容随用户括号里的动作变：把原文带回来，模型才知道要写哪一件事。
    if not auto and user_text.strip():
        paren_note = preset_service.paren_directive(user_text)
        if paren_note:
            user_content = user_content + chr(10) + chr(10) + paren_note
    user_seg = SegmentData(
        name="user",
        items=[{"content": user_content}],
        fixed=False,
    )
    user_seg.tokens = tokenizer.count(user_content)
    segments_data.append(user_seg)

    # 预算裁剪
    limit = xw.max_context_tokens - RESERVED_OUTPUT_TOKENS
    if limit < 0:
        limit = xw.max_context_tokens

    segments_data, truncated = trim_to_budget(segments_data, limit, tokenizer.count)

    # 构建 ContextReport
    report_segments: list[ContextSegment] = []
    for sd in segments_data:
        seg = ContextSegment(name=sd.name, tokens=sd.tokens)
        if sd.name == "world_info":
            seg.entries = [
                item.get("entry_id", "") for item in sd.items if item.get("entry_id")
            ]
        if sd.name == "memory":
            seg.hits = len(sd.items)
        report_segments.append(seg)

    report = ContextReport(
        segments=report_segments,
        template=template,
        tokenizer=tokenizer.name,
        truncated=truncated,
    )

    # 构建最终消息序列
    messages = _build_final_messages(segments_data, request)

    return AssembledContext(
        report=report,
        messages=messages,
        template=template,
        tokenizer=tokenizer,
        truncated=truncated,
    )


# ── 各段构建函数 ──────────────────────────────────────────────


def _prompt_enabled(prompt: dict[str, Any]) -> bool:
    """判断提示组是否启用；缺省视为启用。"""
    return bool(prompt.get("enabled", True))


def _prompt_order(prompt: dict[str, Any]) -> int:
    """读取提示组 order，非法值按 0 处理。"""
    try:
        return int(prompt.get("order", 0))
    except (TypeError, ValueError):
        return 0


def _resolve_preset(xw: XwOptions) -> dict[str, Any]:
    """解析生效预设，取不到时返回空字典。"""
    preset_id = preset_service.resolve_preset_id(xw.preset_id)
    if not preset_id:
        return {}
    preset = store.get_preset(preset_id)
    return preset if isinstance(preset, dict) else {}


def _output_directive(xw: XwOptions) -> str:
    """按生效预设的 output_spec 渲染回合要求，没有就返回空串。"""
    spec = _resolve_preset(xw).get("output_spec")
    if not isinstance(spec, dict):
        return ""
    return preset_service.render_output_spec(spec, auto_turn=str(xw.auto_turn or ""))


# 自动回合的两条指令。用户在两种模式下都不说话，差别只在这一轮写什么。
AUTO_TURN_CONTINUE = (
    "本轮用户没有说话。角色主动开口，接着上一轮往下说："
    "写这一格实际发生的事，再写他对用户说出口的话。"
    "本轮只写【动作】块与【对话】块与【状态】块。"
    "仍然只写角色本人，不要替用户决定说什么或做什么。"
)
AUTO_TURN_DESCRIBE = (
    "本轮用户没有说话。这一轮只推进场景与环境的描写："
    "写清楚此刻周围的光线、声音、器物与气氛，以及角色此刻的姿态。"
    "本轮只写【场景】块与【状态】块。"
    "不要替用户决定说什么或做什么。"
)


def _auto_turn_directive(mode: str) -> str:
    """自动回合的指令：用户这轮没说话，写什么由模式决定。

    continue 接角色自己的话往下走，带动作与台词；
    describe 只推进场景描写，不写对白。
    """
    if mode == "describe":
        return AUTO_TURN_DESCRIBE
    return AUTO_TURN_CONTINUE


def _load_prompt_groups(xw: XwOptions) -> list[dict[str, Any]]:
    """读取生效预设的提示组，仅保留启用项，按 order 升序排列。

    生效预设由 app.presets.resolve_preset_id 判定：请求指定的预设优先，
    其次设置里的默认预设，最后是内置预设。
    """
    preset = _resolve_preset(xw)
    if not preset:
        return []
    prompts = preset.get("prompts")
    if not isinstance(prompts, list):
        return []
    groups = [p for p in prompts if isinstance(p, dict) and _prompt_enabled(p)]
    return sorted(groups, key=_prompt_order)


def _card_payload(xw: XwOptions) -> dict[str, Any] | None:
    """读取角色卡的载荷字段，卡片缺失时返回 None。"""
    if not xw.character_id:
        return None
    card = store.get_card(xw.character_id)
    if not isinstance(card, dict):
        return None
    payload = card.get("data")
    if isinstance(payload, dict):
        return payload
    return card


def _build_system(xw: XwOptions) -> str:
    """从预设的 system 提示组构建 system 段。"""
    groups = _load_prompt_groups(xw)
    contents = [
        str(group.get("content", ""))
        for group in groups
        if group.get("role") == "system" and str(group.get("content", "")).strip()
    ]
    return "\n".join(contents)


def _build_character(xw: XwOptions) -> str:
    """从角色卡构建 character 段。

    包含：角色名与身份锚点、identity、behavior、speech、tags。不含 scenario 与
    mes_example。

    内部 v1 卡把这三件事分成三层：identity 说角色是谁，behavior 说她做什么，
    speech 说她怎么说。每层单独成行并带标签，模型才能把「行为与细节」用在
    动作块上、把「说话方式」用在对白上。写在一段里的坏处实测得到：模型
    把「好为人师、边做边讲解」这类性格设定直接搬进台词，于是每轮都在训话。

    身份锚点是必需的：设定字段裸拼在一起时，模型在场景切换后会把角色重新
    定义成新场景里的人。实测走到地址V后角色变成了吧台员工，状态块里随后
    混进抹布、咖啡机与订单纸条。锚点写明角色是谁，并声明地点变了身份不变。

    scenario 不进这一段：场景由世界书的地区条目提供，角色卡里再放一份会
    与世界状态冲突。实测角色卡里的地址U与状态里的地址V互相矛盾时，
    模型会按冲突重新给角色安排身份，状态块里随后混进抹布、咖啡机与
    订单纸条。

    tags 进这一段：标签是角色类型约束（四爱、女攻男受之类）。它只写在
    卡片数据里，模型看不到，注入后按类型写才稳。

    mes_example 是示例对话，注入后模型会当正文素材逐字搬走：
    实测（backend/tools/probe_copy_source.py）原样注入时六轮全部低于
    150 字下沿，模型在模仿示例里的 <START> 与 {{user}}/{{char}} 标记结构；
    剥掉标记再注入仍照抄了十句示例对白。示例对话因此不进 system 段。
    """
    payload = _card_payload(xw)
    if payload is None:
        return ""
    # 旧格式卡（description / personality）也走同一套映射，
    # 映射只在这一处定义，装配层不另写一份。
    payload = to_internal_v1(payload)
    name = str(payload.get("name", "") or "").strip()
    parts: list[str] = []
    if name:
        parts.append(
            CHARACTER_LABEL + name + "。无论走到哪里，扮演的角色都是这个人，身份不会变。"
        )
    for label, field in CARD_LAYERS:
        val = payload.get(field, "")
        if isinstance(val, str) and val.strip():
            parts.append(label + "：" + val.strip())
    # 标签解析走卡片规范里的同一个函数：别名归一、去重、按受控词表排序，
    # 分隔符中英文逗号与顿号都认。原来这里只切逗号，顿号串会变成一个标签。
    names = normalize_tags(payload.get("tags"))
    if names:
        parts.append(TAG_CONSTRAINT + "：" + "、".join(names))
    return "\n".join(parts)


def _state_line(state: dict[str, Any]) -> str:
    """把配角的状态写成一行的可读文本。"""
    parts: list[str] = []
    for key in ("location", "mood", "items"):
        value = state.get(key)
        if isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                parts.append("、".join(items))
        elif isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "；".join(parts)


def _build_supporting(
    xw: XwOptions,
    user_text: str,
    history_items: list[dict[str, Any]],
) -> str:
    """构建 supporting 段：上下文里被提到的配角。

    配角是对话中由模型引入的角色，卡上标 role=supporting。检索方式按名字：
    名字出现在本轮输入或最近的历史里，就把她的设定注入进来。这既是
    「提到就回来」的回归机制，也让她在场时不至于凭空消失。

    段的位置在 character 段之前：SillyTavern 的注入位置口径里，Before
    Char Defs 的影响比 After Char Defs 弱，配角压过主角是最糟的失败模式，
    所以配角放弱位。

    段内不注入 tags：标签在主角段是「角色类型标签（必须符合）」的硬约束，
    给配角注入会被模型当成对自己的要求。
    """
    search_text = user_text + "\n" + "\n".join(
        str(item.get("content") or "") for item in history_items
    )
    if not search_text.strip():
        return ""

    found: list[tuple[str, dict[str, Any]]] = []
    for summary in store.list_cards():
        name = str(summary.get("name") or "").strip()
        if not name or name not in search_text:
            continue
        card = store.get_card(str(summary.get("id") or ""))
        if not isinstance(card, dict):
            continue
        payload = card.get("data")
        payload = payload if isinstance(payload, dict) else {}
        # 走卡片统一归一：外部卡进来时也能对上三层，说话方式缺失时有兜底。
        payload = to_internal_v1(payload)
        if normalize_role(payload.get("role")) != ROLE_SUPPORTING:
            continue
        # 待完善的卡不进段：设定还空着，注进去是噪音，模型会照着空白编。
        if bool(payload.get("pending")):
            continue
        found.append((name, payload))
    if not found:
        return ""

    main_payload = _card_payload(xw)
    main_name = ""
    if isinstance(main_payload, dict):
        main_name = str(main_payload.get("name") or "").strip()
    # 指代用名字，不用性别代词：配角可能是男性，写死「她」会把模型引到错的性别上。
    # 与提示层的既有原则一致：「你」只指用户，角色一律用名字指代。
    names = "、".join(name for name, _ in found)
    subject = found[0][0] if len(found) == 1 else names
    lines = [
        SUPPORTING_HEADER + "：" + names + "。",
        subject + "不是你要扮演的角色。你用第三人称写"
        + subject + "：" + subject + "说话、" + subject + "做事，都由你从旁描写。",
        "但本回合的" + SUPPORTING_BOUNDARY
        + ("（" + main_name + "）" if main_name else "")
        + "——主角才是镜头所在，" + subject + "只是场景里的人。",
    ]
    for name, payload in found:
        block = ["【" + name + "】"]
        for label, field in CARD_LAYERS:
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                block.append(label + "：" + value.strip())
        relation = payload.get("relation")
        if isinstance(relation, str) and relation.strip():
            block.append("与主角的关系：" + relation.strip())
        state = payload.get("state")
        if isinstance(state, dict) and state:
            state_text = _state_line(state)
            if state_text:
                block.append(name + "此刻的状态：" + state_text)
        lines.append(chr(10).join(block))
    return chr(10).join(lines)


def _build_preset_prompts(xw: XwOptions) -> list[dict[str, Any]]:
    """构建 preset_prompts 段：预设中非 system 角色的提示组。"""
    groups = _load_prompt_groups(xw)
    return [
        {
            "content": str(group.get("content", "")),
            "role": str(group.get("role", "system")),
            "order": _prompt_order(group),
        }
        for group in groups
        if group.get("role") != "system"
    ]


def _build_post_history(xw: XwOptions) -> str:
    """从角色卡提取 post_history_instructions。"""
    payload = _card_payload(xw)
    if payload is None:
        return ""
    value = payload.get("post_history_instructions", "")
    return value if isinstance(value, str) else ""


def _build_user_text(request: ChatRequest) -> str:
    """提取本轮用户输入。"""
    for msg in reversed(request.messages):
        if msg.role == "user":
            return msg.content
    return ""


def _normalize_entry(
    entry: dict[str, Any],
    book_id: str,
    index: int,
) -> dict[str, Any]:
    """把世界书条目文件形态归一为匹配层所需的字段。"""
    keys = entry.get("keys", "")
    if isinstance(keys, list):
        keys = ", ".join(str(k).strip() for k in keys if str(k).strip())
    normalized: dict[str, Any] = dict(entry)
    raw_id = entry.get("id")
    normalized["id"] = str(raw_id) if raw_id not in (None, "") else f"{book_id}__{index}"
    normalized["book_id"] = str(entry.get("book_id") or book_id)
    normalized["keys"] = str(keys or "")
    normalized["content"] = str(entry.get("content", ""))
    normalized.setdefault("priority", 100)
    return normalized


def _mark_region(entries: list[dict[str, Any]], region_id: str | None) -> None:
    """把对话选定的区域标成常驻。

    区域不靠关键词命中：这段对话在哪个区域，就一定注入哪个区域的设定。
    """
    if not region_id:
        return
    for entry in entries:
        if str(entry.get("id")) != str(region_id):
            continue
        entry["constant"] = True
        name = str(entry.get("name") or "").strip()
        content = str(entry.get("content") or "")
        if name:
            entry["content"] = "当前所在区域：" + name + chr(10) + content
        return


def _build_world_info(
    xw: XwOptions,
    user_text: str,
) -> list[MatchedEntry]:
    """世界书匹配。

    参与匹配的世界书集合由请求中的 world_book_ids 决定，逐个经
    app.store.get_world_book 取出；取不到的世界书跳过而非报错。
    对话选定的区域额外按常驻处理，不受关键词命中影响。
    """
    if not xw.world_book_ids:
        return []

    entries: list[dict[str, Any]] = []
    for book_id in xw.world_book_ids:
        book = store.get_world_book(book_id)
        if not isinstance(book, dict):
            continue
        book_entries = book.get("entries")
        if not isinstance(book_entries, list):
            continue
        for index, entry in enumerate(book_entries):
            if isinstance(entry, dict):
                entries.append(_normalize_entry(entry, book_id, index))

    if not entries:
        return []
    _mark_region(entries, xw.region_id)
    return match_entries(entries, user_text)


def _build_persona(xw: XwOptions) -> str:
    """用户档案段：对面是谁。

    取全局设置 persona 里的文本。SillyTavern 有独立的 Persona Description 段，
    缺了它模型只能猜对面是谁，表现为替用户安排动作与心理。
    """
    value = store.get_setting("persona")
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    return "用户档案（对面的人）：" + text


def _user_evidence(
    request: ChatRequest,
    history_items: list[dict[str, Any]],
) -> str:
    """用户自己说过的话。

    道具误丢的修复依据：用户明确提到的物件不是模型漂移出来的陈设，
    角色卡与世界书里没写也要留着，否则用户递过来的道具下一轮就消失。
    证据取本轮输入与最近的用户消息。
    """
    parts: list[str] = []
    user_text = _build_user_text(request)
    if user_text.strip():
        parts.append(user_text.strip())
    for item in history_items:
        if str(item.get("role", "")) != "user":
            continue
        content = str(item.get("content", "") or "").strip()
        if content:
            parts.append(content)
    return chr(10).join(parts)


def _build_memory(xw: XwOptions, evidence: str = "") -> list[dict[str, Any]]:
    """构建 memory 段。

    段内容当前是角色级世界状态：模型每轮回复里的状态块被解析后落盘，
    这里读回来放进 memory 段，模型下一轮能看到自己上轮声明的位置与随身物品。

    没有状态时该段为空列表，ContextReport 中仍作为 tokens=0 的段出现，
    段集合长度恒为 9。记忆检索由记忆任务另行施工，本模块不 import 记忆模块。
    """
    state = _filtered_state(xw, evidence)
    if not state:
        return []
    text = render_state_block(state)
    if not text:
        return []
    return [{"content": text, "source": "world_state"}]


def _character_role(payload: dict[str, Any] | None) -> str:
    """从角色卡描述里取身份短句。

    取描述的第一个短句，通常是「地址N地址U的调音师」这样的身份。
    状态被早期漂移污染时（实测角色K被写成在厨房做咖啡的店员），
    身份锚点比状态更权威：状态说在哪，身份说他是哪个人。
    """
    if not payload:
        return ""
    payload = to_internal_v1(payload)
    identity = str(payload.get("identity", "") or "").strip()
    if not identity:
        return ""
    # 先按句切，首句不超过 24 字就整句用。
    # 原来按「，。；、\n」一起切，角色A那句「父貈权臣的女儿、当朝皇后」
    # 被顿号切成前半截，「当朝皇后」这个决定自称的身份词没了，
    # 模型无从判断自己不是皇帝，自称就滑到「朕」。
    sentences = [part.strip() for part in re.split(r"[，。；\n]", identity) if part.strip()]
    if not sentences:
        return ""
    head = sentences[0]
    if len(head) <= 24:
        return head
    # 首句太长时按顿号切，取第一段。
    first = re.split(r"[、]", head)[0].strip()
    return first if 0 < len(first) <= 24 else ""


def _allowed_state_text(xw: XwOptions, evidence: str = "") -> str:
    """状态里的陈设能留下的依据文本。

    依据是角色卡、世界书、人物随身物品与用户自己说过的话：前两样写清了
    什么地方、什么东西，用户的话证明这是他带来的道具而不是模型编的。
    状态里的场景陈设落在这之外，多半是模型自己漂移出来的——实测角色K
    （地址U调音师）被写成在厨房做咖啡，状态里多了咖啡机、牛奶锅、空杯，
    而状态提示又要求场景与它一致，漂移因此被每轮固化。
    """
    parts: list[str] = []
    payload = _card_payload(xw)
    if payload:
        payload = to_internal_v1(payload)
        for field in ("name", "identity", "behavior"):
            value = payload.get(field, "")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    for book_id in xw.world_book_ids:
        book = store.get_world_book(book_id)
        if not isinstance(book, dict):
            continue
        for entry in book.get("entries") or []:
            if isinstance(entry, dict):
                content = str(entry.get("content", "") or "").strip()
                if content:
                    parts.append(content)
    state = store.get_state(xw.character_id or store.GLOBAL_SCOPE)
    if isinstance(state, dict):
        for item in state.get("items") or []:
            parts.append(str(item))
    if evidence.strip():
        parts.append(evidence.strip())
    return chr(10).join(parts)


def _item_supported(item: str, allowed: str) -> bool:
    """场景陈设是否有依据：物品名的两字片段出现在依据文本里。

    两字片段是折中：单个字太松（「机」到处都在），整词太严
    （「落地监听音箱」不会原样写进卡片）。
    """
    text = item.strip()
    if len(text) < 2:
        return True
    for index in range(len(text) - 1):
        if text[index : index + 2] in allowed:
            return True
    return False


def _clean_state(
    xw: XwOptions,
    evidence: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """按角色卡清洗过的世界状态，以及被丢弃的陈设。

    只在渲染时过滤，落盘的状态不改：过滤掉的陈设是模型漂移的产物，
    留着它们等于让漂移继续传播。全部被过滤掉时保留原样，不把场景清空。
    人物随身物品不再重复出现在场景陈设里：角色带着的东西不是场上的陈设。

    用户自己提到过的物件不算漂移：角色卡与世界书没写也留着，
    否则用户刚递过来的道具下一轮就凭空消失。
    """
    character_id = xw.character_id or store.GLOBAL_SCOPE
    state = store.get_state(character_id)
    if not isinstance(state, dict) or not state:
        return {}, []
    allowed = _allowed_state_text(xw, evidence)
    if not allowed:
        return dict(state), []
    cleaned = dict(state)
    dropped: list[str] = []
    scene_items = state.get("scene_items")
    if isinstance(scene_items, list):
        carried = {str(item).strip() for item in state.get("items") or []}
        kept: list[str] = []
        for raw in scene_items:
            item = str(raw).strip()
            if not item or item in carried:
                continue
            if _item_supported(item, allowed):
                kept.append(item)
        if kept:
            cleaned["scene_items"] = kept
            dropped = [
                str(raw).strip()
                for raw in scene_items
                if str(raw).strip() and str(raw).strip() not in kept
            ]
    position = str(state.get("position") or "").strip()
    if position and not _item_supported(position, allowed):
        # 位置也不在卡片允许的范围里：多半是被漂移改掉的场所
        # （地址U里凭空多出一个厨房）。留空，让场景回到卡片描述的地方。
        cleaned["position"] = ""
    return cleaned, dropped


def clean_state_fields(
    fields: dict[str, Any],
    xw: XwOptions,
    evidence: str = "",
) -> dict[str, Any]:
    """按角色卡与世界书过滤状态字段里的场景陈设。

    与 _clean_state 同一套依据：场景里的物件要么在角色卡与世界书里，
    要么是用户自己提过的，否则就是模型编的。消息里的状态行按同一套
    依据过滤，气泡、落盘状态与右侧面板因此显示一致。
    """
    allowed = _allowed_state_text(xw, evidence)
    if not allowed:
        return dict(fields)
    result = dict(fields)
    scene_items = result.get("scene_items")
    if isinstance(scene_items, list):
        result["scene_items"] = [
            str(item).strip()
            for item in scene_items
            if str(item).strip() and _item_supported(str(item), allowed)
        ]
    return result


def _filtered_state(xw: XwOptions, evidence: str = "") -> dict[str, Any]:
    """按角色卡清洗过的世界状态。"""
    cleaned, _dropped = _clean_state(xw, evidence)
    return cleaned


def user_evidence_text(request: ChatRequest, leaf_id: str | None = None) -> str:
    """用户自己说过的话：本轮输入与最近的用户消息。

    对外接口，供聊天路由判断哪些陈设是用户带来的、哪些是模型编的。
    """
    return _user_evidence(request, _build_history(request, leaf_id))


def dropped_state_items(xw: XwOptions, evidence: str = "") -> list[str]:
    """被过滤掉的场景陈设。

    界面用它提示用户：这些陈设模型自己编的，要不要写进角色卡或世界书。
    不另存数据表，被丢弃的项每轮现算。
    """
    _cleaned, dropped = _clean_state(xw, evidence)
    return dropped


def _state_hint(xw: XwOptions, evidence: str = "") -> str:
    """当前状态提示：把角色级世界状态渲染成一段话，贴在本轮输入末尾。

    状态只放在 system 段时模型会忽略它。实测（backend/tools/
    probe_scene_continuity.py）：状态里位置是「地址N_地址V」，正文却写
    「走在湿漉漉的人行道上」，位置被无视，场景因此回退。

    这里明确写成约束句，并要求场景块与它一致。
    """
    state = _filtered_state(xw, evidence)
    if not state:
        return ""
    text = render_state_block(state)
    if not text:
        return ""
    payload = _card_payload(xw)
    name = str((payload or {}).get("name", "") or "").strip()
    identity = ""
    if name:
        # 身份锚点跟在状态后面：状态里的位置一旦写成「吧台后」这类值，
        # 模型会照着演成店里的工作人员。实测连续四轮里角色被写成店员并
        # 递东西给顾客。每轮把身份钉一次，位置就不会顶掉身份。
        # 这里原来写死「他」：「位置只是他此刻站在哪里，不会改变他是谁」
        # 「他在这个故事里的身份是……」。角色A是皇后、是权臣的女儿，
        # 提示词却把角色称作「他」，与角色卡直接打架：模型于是把皇后
        # 滑向皇帝，自称变成「朕」，甚至把角色与用户的身份对调。
        # 改成不带代词的中性写法：角色名 + 「这个角色」，谁是谁不会错。
        identity = (
            chr(10) + "角色身份不变：扮演的角色始终是" + name
            + "。位置只说明此刻站在哪里，不会改变这个角色是谁。"
        )
        role = _character_role(payload)
        if role:
            identity += name + "在这个故事里的身份是" + role + "，场景要符合这个身份。"
    return (
        "当前状态（上一轮结束时的事实，场景块要与它一致，不要写人物"
        "已经在别处的画面；但位置与身份以角色卡为准，状态里的物品或场所"
        "若与角色身份不符，按角色卡写）：" + chr(10) + text + chr(10)
        + "人物物品只列角色随身带的私人物品；店里的器具、机器、食材、订单"
        + "等属于场景物品，不属于人物物品。" + identity
    )


def _build_history(
    request: ChatRequest,
    leaf_id: str | None = None,
) -> list[dict[str, Any]]:
    """从 app.store 读取会话历史消息并按历史策略整形。

    会话缺失时按空历史处理。整形规则见 _shape_history。

    历史取当前分支路径，不取文件顺序：会话里可能同时留着多条分支，
    文件顺序不等于用户正在看的那条线。leaf_id 为空时用会话头的 head。
    """
    if not request.session:
        return []
    rows = store.session_path(request.session, request.xw.character_id, leaf_id)
    items = [
        {
            "role": str(row.get("role", "user")),
            "content": str(row.get("content", "")),
            # 结构化回合落盘时带 blocks，场景与对白分开存，这里要用到。
            "blocks": row.get("blocks") if isinstance(row.get("blocks"), dict) else None,
        }
        for row in rows
        if isinstance(row, dict)
    ]
    return _shape_history(items)


def _last_sentence(text: str) -> str:
    """取最后一句以句末标点收尾的完整句；没有则退回末尾若干字。"""
    for sentence in reversed(split_sentences(text)):
        if sentence.endswith(("。", "！", "？")):
            return sentence
    return text[-HISTORY_TAIL_CHARS:]


def _truncate_scene(scene: str, limit: int) -> str:
    """截断场景到上限，落在句末而不是句中。

    直接按字符切会把句子拦腰截断，模型读到半句话会当成完整场景来接戏。
    这里在上限内找最后一个句末标点；找不到就整段保留（宁可略超上限，
    也不给模型半句话）。
    """
    if len(scene) <= limit:
        return scene
    head = scene[:limit]
    for index in range(len(head) - 1, -1, -1):
        if head[index] in "。！？":
            return head[: index + 1]
    return head


def _scene_result(scene: str, limit: int) -> str:
    """场景在历史里只留末尾的动作结果句，不留开头的环境描写。

    不能留全文：模型会照抄上一轮的场景文本，实测（backend/tools/
    probe_scene_continuity.py）连续四轮场景几乎逐字相同，每轮只改几个字。
    开头那段环境描写正是被照抄的部分，所以从末尾往前取。

    留两句更长的版本实测更差（正文降到 55 到 152 字，带内 1/6），
    因此只留一句。

    这里曾补一段固定长度的样例文字给模型当长度参照。那段样例本身成了
    新的照抄源，且回合要求改为不设字数下限时不再需要长度参照，因此删掉。

    地点与物品由 memory 段的世界状态回灌，不依赖这里的叙述文本。
    """
    text = str(scene or "").strip()
    if not text:
        return ""
    result = ""
    for sentence in reversed(split_sentences(text)):
        if sentence.strip():
            result = sentence.strip()
            break
    if not result:
        result = text
    if len(result) > limit:
        result = _truncate_scene(result, limit)
    return result


def _last_quoted(text: str) -> str:
    """取文本里最后一段引号内的台词，取不到返回空串。

    旧落盘数据没有分块信息，台词与叙述混在一段里，只能靠引号切出来。
    """
    if not text:
        return ""
    for left, right in (("“", "”"), ("「", "」"), ('"', '"')):
        end = text.rfind(right)
        if end < 0:
            continue
        start = text.rfind(left, 0, end)
        if start < 0:
            continue
        piece = text[start + len(left) : end].strip()
        if piece:
            return piece
    return ""


# 动作块在历史里的长度上限。动作块本来是一句话，这里只是兜底。
HISTORY_ACTION_CHARS = 90


def _history_action(blocks: dict[str, Any]) -> str:
    """动作块在历史里的形态：这一格实际发生的事。

    动作块是接戏的依据：历史里只有台词时，模型看不到上一轮发生了什么，
    场面连续性断掉，回复就退回「站着说话」
    （实测 backend/tools/probe_paren_v3.py）。

    分块错位时动作块里混着台词，整段丢掉，免得台词在历史里出现两遍。
    """
    action = str(blocks.get("action") or "").strip()
    if not action:
        return ""
    if any(mark in action for mark in ("\u201c", "\u300c", '"')):
        return ""
    return _truncate_scene(action, HISTORY_ACTION_CHARS)


def _history_reply(
    content: str,
    blocks: dict[str, Any] | None,
) -> str:
    """角色回复在历史里的形态：留动作与台词，不留场景叙述。

    两条实测证据夹出这条边界：

    一、叙述文本不能进历史。backend/tools/probe_logic_turn.py 的四臂对照里，
    旧口径（场景末句加对白末句）照搬 3 轮，模型顺着自己上一轮的话走，
    用户说什么都不接。场景叙述是模型会照搬的素材。

    二、台词不能不进历史。只留状态键值时，模型看不到自己说过什么，
    对话线索断掉。实测六轮对话里前三轮是自足问题（有什么喝的吗、
    有酒吗），答得正常；第四轮是依赖上文的一句话（好吧），被答成
    「要喝点什么？」——模型已经把咖啡和没有酒都说过一遍，却重新发问。
    状态本身救不了线索：状态只说明人在哪、带着什么，不说明说过什么。

    三、动作块要进历史。动作块写的是这一格实际发生的事，是接戏依据。
    只留台词时模型看不到上一轮发生了什么，场面连续性断掉，回复退回
    「站着说话」（实测 backend/tools/probe_paren_v3.py）。动作块很短，
    且不含引号里的台词，不构成照抄风险。

    所以历史里的角色回合留动作与台词：台词是对话线索本身，动作是这一格
    发生了什么；场景环境叙述照抄风险高，全部丢弃。世界状态不逐轮重复，
    它已经在 memory 段整体注入。
    """
    action = ""
    dialogue = ""
    if isinstance(blocks, dict):
        action = _history_action(blocks)
        dialogue = str(blocks.get("dialogue") or "").strip()
    if dialogue:
        if len(dialogue) > HISTORY_DIALOGUE_CHARS:
            dialogue = dialogue[-HISTORY_DIALOGUE_CHARS :]
        if action:
            return action + chr(10) + dialogue
        return dialogue
    quoted = _last_quoted(content)
    if quoted:
        return quoted
    if action:
        return action
    return NO_SPEECH_MARKER


def _shape_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """历史整形：只保留最近若干轮，角色回复只留台词，场景叙述丢弃。

    llama.cpp 的重复惩罚只作用于已生成的 token，不覆盖 prompt 里的历史，
    因此对白侧的复读压制只能做在上下文侧，不能靠采样参数解决。

    保留轮数可用 settings 里的 history_policy.keep_turns 覆盖。
    """
    if not items:
        return []

    keep = HISTORY_KEEP_TURNS * 2
    policy = store.get_setting("history_policy")
    if isinstance(policy, dict):
        try:
            turns = int(policy.get("keep_turns", HISTORY_KEEP_TURNS))
        except (TypeError, ValueError):
            turns = HISTORY_KEEP_TURNS
        if turns > 0:
            keep = turns * 2

    shaped: list[dict[str, Any]] = []
    for item in items[-keep:]:
        row = dict(item)
        if str(row.get("role", "")) == "assistant":
            row["content"] = _history_reply(
                str(row.get("content", "")),
                row.get("blocks"),
            )
        row.pop("blocks", None)
        shaped.append(row)

    # 历史开头不该是角色回复。落盘异常或会话中途接手时会出现这种情况，
    # 留着会让模型把角色回复当成开场白继续写。
    while shaped and str(shaped[0].get("role", "")) == "assistant":
        shaped.pop(0)
    return shaped


def _build_final_messages(
    segments_data: list[SegmentData],
    request: ChatRequest,
) -> list[ChatMessage]:
    """从裁剪后的段数据构建最终消息序列。

    构建策略：
    - system 段 → ChatMessage(role="system")
    - character 段与 persona 段内容合并到 system
    - preset_prompts 非 system 按 role 独立消息
    - post_history → system 消息
    - world_info → system 消息
    - memory → system 消息
    - history → 各自 role 消息
    - user → 本轮用户输入
    """
    messages: list[ChatMessage] = []

    # 索引各段
    seg_map = {sd.name: sd for sd in segments_data}

    # system 合并：system + character + post_history + world_info + memory
    system_parts: list[str] = []

    sys_seg = seg_map.get("system")
    if sys_seg and sys_seg.items:
        system_parts.append(sys_seg.items[0].get("content", ""))

    # 配角段在角色段之前：配角是弱影响位，压过主角是最糟的失败模式。
    sup_seg = seg_map.get("supporting")
    if sup_seg and sup_seg.items:
        system_parts.append(sup_seg.items[0].get("content", ""))

    char_seg = seg_map.get("character")
    if char_seg and char_seg.items:
        system_parts.append(char_seg.items[0].get("content", ""))

    # 用户档案紧跟角色卡：先认识对面是谁，再看对面在跟谁说话。
    persona_seg = seg_map.get("persona")
    if persona_seg and persona_seg.items:
        system_parts.append(persona_seg.items[0].get("content", ""))

    wi_seg = seg_map.get("world_info")
    if wi_seg and wi_seg.items:
        for item in wi_seg.items:
            system_parts.append(item.get("content", ""))

    mem_seg = seg_map.get("memory")
    if mem_seg and mem_seg.items:
        for item in mem_seg.items:
            system_parts.append(item.get("content", ""))

    # post_history 放在 system 段最后：SillyTavern 的 post-history instructions
    # 就在所有设定之后、生成之前，离生成点最近。放前面会被稀释。
    post_seg = seg_map.get("post_history")
    if post_seg and post_seg.items:
        for item in post_seg.items:
            content = item.get("content", "")
            if content.strip():
                system_parts.append(content)

    combined_system = "\n".join(p for p in system_parts if p.strip())
    if combined_system.strip():
        messages.append(ChatMessage(role="system", content=combined_system))

    # preset_prompts 非固定 role
    preset_seg = seg_map.get("preset_prompts")
    if preset_seg and preset_seg.items:
        for item in preset_seg.items:
            role = item.get("role", "system")
            content = item.get("content", "")
            if content.strip():
                messages.append(ChatMessage(role=role, content=content))

    # history
    hist_seg = seg_map.get("history")
    if hist_seg and hist_seg.items:
        for item in hist_seg.items:
            role = item.get("role", "user")
            content = item.get("content", "")
            if content.strip():
                messages.append(ChatMessage(role=role, content=content))

    # user 本轮输入
    user_seg = seg_map.get("user")
    if user_seg and user_seg.items:
        user_content = user_seg.items[0].get("content", "")
        if user_content.strip():
            messages.append(ChatMessage(role="user", content=user_content))

    return messages
