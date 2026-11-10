"""预设读写。

全部经 app.store 落地为 JSON 文件，不引入任何数据库。

内置 RP 预设：
本机实测（docs/实践情况/质量与速度平衡点.md）表明，回合长度由提示决定，
不由模型决定：默认提示下各候选只写 20 到 100 字；显式写死长度与结构后，
ArityFlow-4B Q5_K_M 稳定落在 180 到 220 字，且每轮带中文引号对白。
因此内置预设把回合要求写进 output_spec，装配管线按此渲染成回合要求，
附在本轮输入末尾交给模型执行。

预设的 system 提示组不写具体格式示例：实测四种写法里，带示例的三种都会
被模型照抄，见 backend/tools/probe_preset_ab.py。
"""

from __future__ import annotations

from typing import Any

from app import store as file_store
from app.errors import ApiError
from app.inference.structure import STRUCTURE_BLOCKS, block_entry, block_names

# 内置预设的固定 id 与版本号。版本落后时按内置载荷刷新，用户自建预设不受影响。
BUILTIN_PRESET_ID = "p_builtin_rp"
BUILTIN_PRESET_VERSION = 30

# 内置预设的 system 提示组：角色扮演契约，只管角色一致与剧情推进。
# 角色扮演契约。角色归属口径与动作块口径是同一件事的两面：
# 动作块写这一格实际发生的事（含用户身上发生的事），就不等于替用户扮演；
# 只写角色本人的动作与反应，用户的指定剧情就只能落到对白里变成「光说不做」。
# 实测见 backend/tools/probe_paren_v3.py 与 backend/app/inference/structure.py。
#
# 2026-11-07 提示层审计后改写（审计报告见
# docs/理论支持/提示层审计.md）。三处问题：
# 1. 同一规则重复三到四次（「台词是对用户说的」在回合要求里出现三次，
#    「动作块写这一格实际发生的事」在两段里各出现两次）。规则稀释，
#    还挤占注意力预算。重复的条款一律删掉，只在回合要求里留一次。
# 2. 「优先推进剧情：每次回复给出新的信息、动作或反应，让场景往前走」
#    是单一目标指令：模型朝着一个方向逼近，不会发散。换成酒馆 Roleplay -
#    Detailed 的做法「Develop the plot slowly」，并补一句句式起伏
#    （酒馆 Roleplay - Immersive 的 "complexity and burstiness"）。
# 3. 系统段不再复述动作块与台词的口径：它们在回合要求里，离生成点更近。
BUILTIN_SYSTEM_PROMPT = (
    "扮演角色卡里的角色，以这个角色的身份回应用户。\n"
    "叙述用第三人称：扮演的角色写「他」「她」或名字，用户写「你」或「对方」，"
    "别的在场的人写「他」「她」；台词里才用第一人称，两种人称不要混用。\n"
    "台词里的自称按角色卡写明的来：角色卡写了自称就照那个自称，"
    "没写才按身份与礼制推；不要借用设定里出现过的他人身份或称谓。\n"
    "保持角色一致：只写这个角色能知道的事，不解释设定，不提自己是 AI，不写 OOC 或元叙述。\n"
    "不替用户说话，也不替用户安排对方没做过的新动作。\n"
    "剧情慢慢发展：不必每轮都往前推一大步，有时候只是停顿、犹豫、一个没说完的动作；"
    "这一格真实发生了什么就写什么。\n"
    "句式可以有起伏：长短句交错，不要每轮都用同一种句型，"
    "也不要每次都拿同一种动作收尾。\n"
    "地点会变，身份不变：人物走到哪里都还是角色卡里的那个人，"
    "不会变成店里的工作人员或服务人员。\n"
    "用户的消息可能是对白，也可能是让你推进剧情的指示；"
    "用户指定过的动作照原样写，其余按最自然的理解回应。\n"
    "用中文写作。"
)

# 这句回合要求曾经被写了两遍。实测（backend/tools/probe_copy_loop.py），
# 同一句在 system 段出现两次时，模型会把整轮回复逐字复现（六轮里一轮与
# 更早的一轮完全相同）；只写一遍的对照组没有整轮相同。
# 回合要求在 system 段只出现一次，长度与引号仍由 output_spec 渲染出的
# 回合要求保证。

# 括号即剧情：用户消息里圆括号写的内容是他指定的动作。
# 这句只在用户真的写了圆括号时才出现：提了就等于叫模型写它
# （同一条结论见 backend/tools/probe_scene_ratio.py）。
# 实测（backend/tools/probe_paren_v3.py）：括号规则单独加没有用
# （括号落实率 0），必须与动作块口径一起上；放在本轮输入最后时最稳。
#
# 措辞改过一次。原来写「已经发生，照着它往下写」，模型把括号当成已经过去的
# 背景，于是去写「之后」才发生的事：用户指定的动作被换成角色自己的动作，
# 或者只被搬进对白里讲给用户听——用户反馈的「模型还是有自己的想法」。
# 现在把括号内容原样带回来，并明确「这一格就写它本身」。
PAREN_DIRECTIVE_HEAD = (
    "用户消息里圆括号内的内容是用户指定的动作，不是背景交代："
)
PAREN_DIRECTIVE_TAIL = (
    "。这一格发生的事就写它本身——谁对谁做了什么、道具落在谁身上，"
    "照原样写下来，别绕开、别改成别的意思，也别只写它之后才发生的事。"
    "台词照常写角色此刻会说的话，不要把这整件事搬进台词里讲给用户听。"
)


def extract_paren_text(user_text: str) -> list[str]:
    """取出用户消息里成对的圆括号内容，按出现顺序。

    中英文括号都认。没闭合的不算。
    """
    found: list[str] = []
    stack: list[tuple[str, int]] = []
    pairs = {"（": "）", "(": ")"}
    closers = {value: key for key, value in pairs.items()}
    for index, char in enumerate(user_text):
        if char in pairs:
            stack.append((char, index))
        elif char in closers and stack:
            opener, start = stack.pop()
            if pairs[opener] == char:
                found.append(user_text[start + 1:index].strip())
    return [item for item in found if item]


def paren_directive(user_text: str) -> str:
    """按用户括号里的内容拼出本轮的括号指令。

    没有成对圆括号时返回空串：提了就等于叫模型写它。
    """
    targets = "；".join(extract_paren_text(user_text))
    if not targets:
        return ""
    return PAREN_DIRECTIVE_HEAD + targets + PAREN_DIRECTIVE_TAIL


def has_paren_directive(user_text: str) -> bool:
    """用户这一轮有没有用圆括号指定剧情。"""
    return bool(extract_paren_text(user_text))


# 内置预设不写具体格式示例。
# 本机实测（backend/tools/probe_preset_ab.py，四种写法各六轮，同一组提示）：
# 写具体叙事示例时模型会把示例当内容照抄、逐字输出；换成占位符骨架时
# 六轮输出完全相同；换成一个与角色卡无关的具体场景也一样被照抄。
# 完全不写示例的一组六轮全部落在 150 到 300 字、零复读轮、六种不同回复，
# 场景描写占比也最高。长度与中文引号由 output_spec 渲染出的回合要求保证，
# 回合要求附在本轮输入末尾，比写在 system 段更有效。

# 回合要求。字段含义见 render_output_spec。
BUILTIN_OUTPUT_SPEC: dict[str, Any] = {
    # 不声明字数下限与上限。实测（backend/tools/probe_length_distribution.py）：
    # 4B 模型自然正文长度均值 129.6、中位 121、最高 234，18 次采样只有 16.7%
    # 能到 150 字；闸门重写后带内率仍只有 0 到 2/6，耗时却从 2.8 秒涨到 6 秒，
    # 界面还会出现「内容消失又重新生成」。回合长度交给模型自己判断。
    # 结构化回合：模型按标记分块输出，客户端拆成场景、对白与状态三块。
    # 状态块由模型每轮自动更新当前场景、装备物品、地图与时间，
    # 后端解析落盘并回灌给下一轮，右侧面板据此显示世界状态。
    # 与 single_paragraph 互斥，声明结构后不再要求写成完整一段。
    "structure": ["action", "scene", "dialogue", "state"],
    # 提示里讲动作、对白与状态三块。场景块仍然认：模型自己写了标记或先写了
    # 一段环境，那部分仍落进场景块，不丢。
    # 实测（backend/tools/probe_scene_ratio.py，四种写法各六轮）：
    # 提示里完全不提场景块时，六轮全部只有对白与状态；
    # 一旦写上「不要写场景块」这类禁令，四轮里三轮照写；
    # 写成长句加例外条款，五轮里四轮照写，还有一轮只剩场景没有对白。
    # 提场景就等于叫模型写场景，普通回合的提示里索性不提。
    # 动作块是这轮加回来的：只有对白的回合读起来像角色在自言自语，
    # 补一段一到两句的动作才是互动回合（实测见 backend/tools/probe_interaction.py）。
    "structure_prompt": ["action", "dialogue", "state"],
    "advance_plot": True,
    "forbid_meta": True,
}


def list_presets() -> list[dict[str, Any]]:
    """列出预设。"""
    return file_store.list_presets()


def get_preset(preset_id: str) -> dict[str, Any] | None:
    """读取预设。"""
    return file_store.get_preset(preset_id)


def save_preset(preset: dict[str, Any]) -> dict[str, Any]:
    """写入预设，含提示组与正则规则。"""
    if not isinstance(preset, dict):
        raise ApiError(code="preset_invalid", message="预设必须是 JSON 对象")
    prompts = []
    for index, item in enumerate(preset.get("prompts") or []):
        prompt = dict(item)
        prompt.setdefault("id", file_store.new_id("pr"))
        prompt.setdefault("name", f"提示组 {index + 1}")
        prompt.setdefault("content", "")
        prompt.setdefault("role", "system")
        prompt.setdefault("order", index)
        prompt.setdefault("enabled", True)
        prompts.append(prompt)
    payload = dict(preset)
    payload["prompts"] = prompts
    payload.setdefault("regex_rules", [])
    return file_store.save_preset(payload)


def delete_preset(preset_id: str) -> bool:
    """删除预设。"""
    return file_store.delete_preset(preset_id)


def list_prompt_groups(preset_id: str) -> list[dict[str, Any]]:
    """列出预设提示组，按 order 升序。"""
    preset = get_preset(preset_id)
    if preset is None:
        raise ApiError(code="preset_not_found", message="预设不存在", status_code=404)
    groups = [dict(item) for item in preset.get("prompts") or []]
    groups.sort(key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")))
    return groups


def split_prompt_groups(preset_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """拆分提示组为 system 组与其余组。

    system 组在装配时固定不裁剪，其余组按 order 依次裁剪。
    """
    groups = list_prompt_groups(preset_id)
    system = [item for item in groups if str(item.get("role") or "system") == "system"]
    others = [item for item in groups if str(item.get("role") or "system") != "system"]
    return system, others


def list_regex_rules(preset_id: str) -> list[dict[str, Any]]:
    """列出预设携带的正则规则。"""
    preset = get_preset(preset_id)
    if preset is None:
        return []
    return [dict(item) for item in preset.get("regex_rules") or []]


def builtin_preset() -> dict[str, Any]:
    """内置 RP 预设的完整载荷。"""
    return {
        "id": BUILTIN_PRESET_ID,
        "name": "内置 RP 预设",
        "builtin": True,
        "builtin_version": BUILTIN_PRESET_VERSION,
        "prompts": [
            {
                "id": "rp_system",
                "name": "角色扮演契约",
                "content": BUILTIN_SYSTEM_PROMPT,
                "role": "system",
                "order": 0,
                "enabled": True,
            }
        ],
        "output_spec": dict(BUILTIN_OUTPUT_SPEC),
        "regex_rules": [],
    }


def ensure_builtin_preset() -> str:
    """确保内置预设存在并可用，返回其 id。

    首次写入时同时把默认预设指向它。已存在且版本不低于当前版本时保持原样，
    不做无谓改写；版本落后时按内置载荷整体刷新，用户改过内置预设也会被覆盖，
    需要保留改动请先复制成新预设。
    """
    existing = file_store.get_preset(BUILTIN_PRESET_ID)
    if not isinstance(existing, dict):
        save_preset(builtin_preset())
    else:
        try:
            stale = int(existing.get("builtin_version")) < BUILTIN_PRESET_VERSION
        except (TypeError, ValueError):
            stale = True
        if stale:
            save_preset(builtin_preset())

    if not file_store.get_setting("default_preset_id"):
        file_store.set_setting("default_preset_id", BUILTIN_PRESET_ID)
    return BUILTIN_PRESET_ID


def resolve_preset_id(preset_id: str | None = None) -> str | None:
    """解析生效预设 id。

    顺序：请求指定的预设 → 设置里的默认预设 → 内置预设。
    请求指定的预设在数据目录里不存在时按未指定处理。
    """
    if preset_id and isinstance(file_store.get_preset(preset_id), dict):
        return preset_id

    fallback = file_store.get_setting("default_preset_id")
    if isinstance(fallback, str) and fallback:
        if isinstance(file_store.get_preset(fallback), dict):
            return fallback

    if isinstance(file_store.get_preset(BUILTIN_PRESET_ID), dict):
        return BUILTIN_PRESET_ID
    return None


# 自动回合的块收窄：那一轮只写该模式该写的块。
# 提示层不出现不该写的块，模型就不会先写出来再被丢掉：
# 生成一次场景描写再丢掉是白花 token。
AUTO_TURN_BLOCKS = {
    "continue": ("action", "dialogue", "state"),
    "describe": ("scene", "state"),
}

# 每种模式必须写哪几块。普通回合与对话推进轮都要有动作与台词：
# 只有台词没有动作的回合读起来像角色在自言自语，不是一段互动。
STRUCTURE_MODE_REQUIREMENTS = {
    "": (
        "【动作】块与【对话】块都要写："
        "动作块写这一格实际发生的事，对话块写角色说出口的话。"
        "台词只写在【对话】块里。"
    ),
    "continue": (
        "【动作】块与【对话】块都要写："
        "角色主动开口，动作块写这一格实际发生的事，"
        "对话块写角色对用户说出口的话。"
        "台词只写在【对话】块里。"
    ),
    "describe": "【场景】块要写清楚此刻周围的环境。",
}


def _auto_turn_structure(names: list[str], auto_turn: str) -> list[str]:
    """自动回合本轮该写哪几块。非自动回合或没声明结构时原样返回。"""
    allowed = AUTO_TURN_BLOCKS.get(str(auto_turn or ""))
    if not allowed:
        return names
    narrowed = [name for name in names if name in allowed]
    return narrowed or names


def render_output_spec(spec: dict[str, Any], auto_turn: str = "") -> str:
    """把 output_spec 渲染成回合要求文本。

    只渲染显式声明的字段；spec 为空或字段全缺时返回空串。

    auto_turn 非空时按自动回合模式收窄块：那一轮只写该模式该写的那块，
    提示里不出现别的块，模型就不会先写出来再被丢掉。
    """
    if not isinstance(spec, dict) or not spec:
        return ""

    parts: list[str] = ["本次回复是一个完整回合。"]

    min_chars = _as_int(spec.get("min_chars"))
    max_chars = _as_int(spec.get("max_chars"))

    structure = spec.get("structure")
    all_names = block_names(structure) if isinstance(structure, list) else []
    names = all_names
    if auto_turn:
        names = _auto_turn_structure(names, auto_turn)
    else:
        # 提示里只讲该讲的块。解析仍按 structure 全量认块：
        # 模型先写的那句环境会落进场景块，不会丢。
        prompt_set = spec.get("structure_prompt")
        if isinstance(prompt_set, list):
            narrowed = [name for name in all_names if name in block_names(prompt_set)]
            names = narrowed or names
    if names:
        # 声明结构时：块提示在前，长度按正文块合计。
        # 句式与实测版本（backend/tools/probe_json_output.py 的 markers 变体）
        # 逐字一致，那组五轮字数带内 5/5。
        parts.append(_render_structure(names, auto_turn))
        # 台词要对着用户说。这句写进回合要求（附在本轮输入末尾），
        # 比只写系统段更管用：实测见 backend/tools/probe_interaction.py。
        if "dialogue" in names:
            parts.append(_render_interaction(auto_turn))
        # 状态块是元数据，不计入正文字数：实测（backend/tools/probe_state_block.py）
        # 把三块一起算时，场景与对白被状态块挤到 100 到 148 字，带内率只有 1/8。
        body_names = [name for name in names if name != "state"]
        word = _body_label(names)
        if min_chars and max_chars:
            parts.append(f"{word}块正文合计不少于 {min_chars} 字且不超过 {max_chars} 字。")
        elif max_chars:
            parts.append(f"{word}块正文合计不超过 {max_chars} 字。")
        elif min_chars:
            parts.append(f"{word}块正文合计不少于 {min_chars} 字。")
        if len(body_names) != len(names):
            parts.append("状态按本回合实际情况写，不要照抄上一轮。")
    else:
        if min_chars and max_chars:
            parts.append(f"全文不少于 {min_chars} 字且不超过 {max_chars} 字。")
        elif max_chars:
            parts.append(f"全文不超过 {max_chars} 字。")
        elif min_chars:
            parts.append(f"全文不少于 {min_chars} 字。")
        if spec.get("require_dialogue"):
            parts.append("包含场景描写、人物动作和一到三句对白，对白必须用中文双引号，不要用英文引号。")
        if spec.get("single_paragraph"):
            parts.append("写成完整一段，不分行。")
    if spec.get("advance_plot"):
        # 推进与承接要留出闲聊的余地：用户发一句「你好」时，强行补一段
        # 环境与剧情会让回合变长变假。这里也不提场景：提示里一提，模型就写。
        # 闲聊可以短，但不能不回应：原来「简短回就行」等于许可一句台词应付，
        # 模型就顺着历史里上一句继续答旧问题，用户说的话落不到回合里。
        parts.append(
            "如果是剧情推进，这一格真实发生的事写清楚就好，"
            "不必每轮都推进一步；"
            "如果是闲聊或一句简单回应，对白可以短，但仍然要回应用户这一句，"
            "不要硬凑别的内容。"
        )
        # 接戏要求：没有这句时模型每轮都像重新开场，会重复走已经走过的路。
        parts.append(
            "承接上一轮继续写，人物的位置和刚做过的事不要重来，"
            "不要重描上一轮写过的环境。"
        )
    if spec.get("forbid_meta"):
        parts.append("不要写提纲、旁白总结或 OOC 说明。")

    return "".join(parts)


def _render_structure(names: list[str], mode: str = "") -> str:
    """把结构声明渲染成回合要求里的一段说明。

    块的顺序由模型自选：可以先开口说话、可以先写动作、也可以只回一句。
    回合要求不再规定「先动作后对白」：固定顺序把每轮都写成同一个模子。

    状态块仍要求写在最后：它要被机器解析出五个字段，放在中间容易被正文冲掉。
    """
    blocks = []
    for name in names:
        marker, hint = block_entry(str(name), mode)
        if not marker:
            continue
        blocks.append(marker + hint)
    if not blocks:
        return ""
    body = [name for name in names if name != "state"]
    labels = [STRUCTURE_BLOCKS[name][0].strip("【】") for name in body]
    if len(labels) >= 2:
        order = "与".join(labels) + "块的顺序由你定"
    else:
        order = "块的顺序由你定"
    parts = [
        "可以按需要分段，用标记分块写："
        + "，".join(blocks)
        + "。" + order + "；状态块写在最后。"
    ]
    requirement = STRUCTURE_MODE_REQUIREMENTS.get(str(mode or ""), "")
    if requirement:
        parts.append(requirement)
    else:
        parts.append("不需要的块可以不写。")
    return "".join(parts)


def _render_interaction(auto_turn: str) -> str:
    """台词怎么对用户说的那一段要求。

    回合要求附在本轮输入末尾（实测比只写系统段更管用）。
    用户在说话时要求接住用户这一句；用户没说话时要求角色主动开口搭话，
    两种都要点明「对用户说」，否则模型写成自说自话的旁观感慨。
    """
    if auto_turn == "continue":
        return (
            "这一轮用户没有说话，角色要主动开口接着往下说："
            "台词是对用户说的，像在跟对方搭话，不要自言自语。"
        )
    # 这段要求原本与对白块提示里的「台词是对用户说的，像在跟对方搭话，
    # 不要自言自语，也不要只写旁观式的感慨」整句重复，审计后只留一次。
    return (
        "台词是对用户说的：先接住用户这一轮说的话或刚做的动作再往下写，"
        "像在跟对方搭话；不要自言自语，也不要只写旁观式的感慨。"
    )


# 每块字数下限占总下限的比例。实测（backend/tools/probe_struct_directives.py，
# 五轮单变量对照）：只写合计下限时带内率 1/5，追加每块下限后 5/5；
# 下限比例再测三档，场景 100 对白 50 一档带内率最高、均长 182 字。
# 比例之和为 1，两块下限之和正好等于总下限：下限之和大于总下限时
# 要求自相矛盾，模型够不到就触发长度闸门重写（实测三轮里两轮触发）。
# 这组下限只用于长度闸门的重写指令（按块点名差多少），不再进提示词：
# 提示词里同时给分块下限与合计下限，4B 模型会取最保守的那个，正文越写越短。
STRUCTURE_BLOCK_MIN_RATIO = {"action": 0.25, "scene": 0.25, "dialogue": 0.5}

# 状态块是元数据，不分配正文字数下限：它不计入正文篇幅要求。


def structure_minimums(spec: dict[str, Any], min_chars: int) -> dict[str, int]:
    """每块的字数下限。

    声明优先于按比例推导；没有比例的块（状态块）不在其中。
    """
    if min_chars <= 0:
        return {}
    declared = spec.get("structure_min")
    declared = declared if isinstance(declared, dict) else {}
    floors: dict[str, int] = {}
    for name in block_names(spec.get("structure")):
        value = declared.get(name)
        try:
            floor = int(value)
        except (TypeError, ValueError):
            floor = 0
        if floor <= 0:
            ratio = STRUCTURE_BLOCK_MIN_RATIO.get(name)
            if ratio is None:
                continue
            floor = max(20, round(min_chars * ratio))
        floors[name] = floor
    return floors


def _render_block_minimums(
    names: list[str],
    min_chars: int,
    spec: dict[str, Any],
) -> str:
    """渲染每块的字数下限。

    只写「两块正文合计不少于 150 字」时，模型会把两块都写短，实测带内率
    1/5；追加每块下限后带内率 5/5，模型也不再在结尾追加总结。

    每块下限可由 output_spec.structure_min 显式声明，声明优先于按比例推导。
    """
    if not names or min_chars <= 0:
        return ""
    floors = structure_minimums(spec, min_chars)
    parts: list[str] = []
    for name in names:
        floor = floors.get(name)
        if not floor:
            continue
        label = STRUCTURE_BLOCKS[name][0].strip("【】")
        parts.append(f"{label}块至少 {floor} 字")
    if not parts:
        return ""
    return "，".join(parts) + "，都不要只写一句概述。"


def _body_label(names: list[str]) -> str:
    """正文块的中文写法。

    带状态块时直接点名「场景与对白」，避免结构句说三块、长度句说两块，
    模型会以为状态也占正文字数。
    """
    body = [name for name in names if name != "state"]
    if len(body) == len(names):
        return _count_word(len(body))
    labels = [STRUCTURE_BLOCKS[name][0].strip("【】") for name in body]
    if len(labels) == 1:
        return labels[0] + "块"
    return "与".join(labels) + _count_word(len(labels)) + "块"


# 块数的中文写法，与实测指令逐字对齐。
STRUCTURE_COUNT_WORDS = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五"}


def _count_word(count: int) -> str:
    """块数转中文写法，超出已知范围时退回阿拉伯数字。"""
    return STRUCTURE_COUNT_WORDS.get(count, str(count))


def _as_int(value: Any) -> int:
    """把字段读成正整数，非法值按 0 处理。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
