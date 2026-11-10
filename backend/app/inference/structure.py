"""结构化回合的分块解析。

回合要求声明结构时，模型按标记分块输出，当前是【场景】【对话】【状态】三块。
这里负责把模型输出拆成结构化的块：

- split_blocks：一次性解析，用于最终结果与非流式路径。
- StructureSplitter：流式解析。标记可能落在分片边界上，分片器逐片判定归属，
  被切开的半个标记留到下一片再判，不会误切。

块的名称与标记是客户端自己的输出契约，不涉及模型专属概念。
实测（backend/tools/probe_json_output.py，五轮对照）：标记式两块齐全 5/5、
对白带中文引号 5/5、字数带内 5/5，耗时 2.81 秒与纯文本持平；
严格 JSON 语法约束下对白带引号只有 0 到 1/5 且耗时翻倍，因此不采用。
"""

from __future__ import annotations

# 块定义：块名 -> (标记, 写法提示)。标记是模型要写的块头，提示是该块写什么。
# 动作块与场景块分开：普通回合与对话推进轮写动作（角色这一轮做了什么），
# 场景推进轮写环境（此刻周围是什么样子）。两者混在一块时模型会顺手写成
# 环境长段，回合就从互动退成一段描述（实测每轮先写一段光线与氛围）。
STRUCTURE_BLOCKS: dict[str, tuple[str, str]] = {
    # 动作块写这一格实际发生的事，一句话。
    # 口径从「角色本人的动作或反应」改成「实际发生的事」：用户用圆括号指定的
    # 剧情要落在动作块里。旧口径下模型只写角色摆姿势（惬意地靠在软榻上、
    # 慵懒地倚着锦缎），场面全被挤进对白变成「光说不做」。
    # 实测（backend/tools/probe_paren_v3.py，两种子各六轮）：只改这一句，
    # 括号落实率从 0.25 升到 0.75。
    # 明确不写对白：实测（backend/tools/probe_state_block.py 同条件对照）
    # 动作块里混入对白后，对白块会把同一段话再写一遍，回合因此超长。
    "action": (
        "【动作】",
        "写这一格实际发生的事，一句话，最多 35 字："
        "用户指定过的动作照原样写进来——谁对谁做了什么、道具落在谁身上；"
        "在场的人对用户做了什么也写进来。"
        "用第三人称写在场的人，提到用户时写「你」或「对方」；"
        "不要写光线、雨声、窗外、夜色这类环境词；"
        "不要在这里写任何引号里的台词。",
    ),
    # 场景块写环境，只在场景推进轮出现。
    "scene": (
        "【场景】",
        "写此刻周围的环境：光线、声音、器物、气味与气氛，最多两三句。"
        "用第三人称叙述，不要出现「你」或「我」；"
        "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
        "不重复上一轮已经写过的环境。",
    ),
    # 对白必须直接回应用户这一轮，并且至少两句。
    # 实测（backend/tools/probe_uptake_directive.py）：对白只有一句时模型没有
    # 落点回应用户，会顺着历史里上一句继续答旧问题——用户说「晚上好」，
    # 回的是「有……威士忌。」。要求写两句后对白均值从 11 到 21 字涨到 43 字，
    # 句数从 1.5 涨到 2.25，并出现了真正的问候。
    "dialogue": (
        "【对话】",
        "只写角色自己说出口的台词，可以先开口；"
        "用中文双引号不要用英文引号；"
        "台词里的自称按角色卡写明的来，卡里没写才按身份与礼制推；"
        "不要替对方说话或回答，也不要给对方的动作配音；"
        "不要在这里写状态行，状态只写在【状态】块里；"
        "至少写两句，合计不少于 100 字；"
        "台词只说此刻角色会对用户说出口的话："
        "不解释角色自己的心思与立场，不总结道理，"
        "不重复上一轮说过的威胁或叮嘱，说一个新的进展；"
        "角色不必每轮都强势或训话，带锋芒的话偶尔一句就够，"
        "不要连着两轮都用同一种劲头，多数回合说具体的事；"
        "写够字数的办法是写具体的当下——动作、物件、空间的变化——不是写道理。",
    ),
    "state": (
        "【状态】",
        "写六行状态，每行是「键：值」，键依次为当前位置、人物位置、人物物品、"
        "场景物品、地图、时间，每行尽量短；"
        "人物物品只写角色自己随身带的私人物品，店里的器具、机器、食材、订单等不属于角色；"
        "场景物品写当前场景里存在的物件，只写角色卡与世界书里写到过的那些，没有就写「无」；"
        "物品之间用顿号分开，每件只写物件名本身，不加括号说明、注释或代称；"
        "只写这六行，不要写别的内容；"
        "人物位置写角色在这个地点里的具体站位如靠窗的位子、吧台前、门口，不要写地点名称",
    ),
}


# 自动回合模式下的块提示改写。同一块在不同轮次写什么不一样：
# 对话推进轮用户没有说话，台词不能写成「回应用户这一轮」，
# 改成「接着上一轮往下说，但仍然是对用户说的」。
# 标记不随模式变，只有写法变：解析与前端展示都按同一套标记。
STRUCTURE_BLOCK_MODE_HINTS: dict[str, dict[str, tuple[str, str]]] = {
    "continue": {
        "dialogue": (
            "【对话】",
            "只写角色自己说出口的台词；"
            "用中文双引号不要用英文引号；"
            "台词里的自称按角色卡写明的来，卡里没写才按身份与礼制推；"
            "不要替对方说话或回答，也不要给对方的动作配音；"
            "这一轮用户没有说话，台词接着上一轮的语境往下说，"
            "至少写两句，合计不少于 100 字；"
            "台词仍然是对用户说的，像在跟对方搭话，不要自言自语；"
            "台词只说此刻角色会对用户说出口的话："
            "不解释角色自己的心思与立场，不总结道理，"
            "不重复上一轮说过的威胁或叮嘱，说一个新的进展。",
        ),
    },
}


def block_entry(name: str, mode: str = "") -> tuple[str, str]:
    """块名对应的标记与写法提示。

    mode 是自动回合模式。该模式下有专属提示时用专属的，
    没有的用块默认提示。标记不随模式变。
    """
    overrides = STRUCTURE_BLOCK_MODE_HINTS.get(str(mode or ""), {})
    entry = overrides.get(str(name))
    if entry is not None:
        return entry
    return STRUCTURE_BLOCKS.get(str(name), ("", ""))


def block_names(structure: object) -> list[str]:
    """从 output_spec.structure 取出合法块名，保持声明顺序并去重。"""
    if not isinstance(structure, list):
        return []
    names: list[str] = []
    for item in structure:
        name = str(item)
        if name in STRUCTURE_BLOCKS and name not in names:
            names.append(name)
    return names


def display_labels(names: list[str]) -> dict[str, str]:
    """块名到展示标签的对应表。标签去掉标记的括号，交给前端直接显示。"""
    labels: dict[str, str] = {}
    for name in names:
        marker = STRUCTURE_BLOCKS.get(name, ("", ""))[0]
        labels[name] = marker.strip("【】")
    return labels


def _label_pairs(names: list[str]) -> list[tuple[str, str]]:
    """块名与标记的对应表，按声明顺序。"""
    return [(name, STRUCTURE_BLOCKS[name][0]) for name in names if name in STRUCTURE_BLOCKS]


def _find_marker(text: str, pairs: list[tuple[str, str]]) -> tuple[int, str, str]:
    """找最早的标记，返回 (位置, 块名, 标记)；找不到时位置为 -1。"""
    best = -1
    best_name = ""
    best_label = ""
    for name, label in pairs:
        index = text.find(label)
        if index == -1:
            continue
        if best == -1 or index < best:
            best = index
            best_name = name
            best_label = label
    return best, best_name, best_label


def split_blocks(text: str, names: list[str]) -> dict[str, str]:
    """把正文拆成块。

    容错口径：第一个标记之前的文字算第一块；整段没有标记时全部算第一块；
    某个块没有内容时它的值为空串。
    """
    blocks = {name: "" for name in names}
    if not names or not text:
        return blocks
    pairs = _label_pairs(names)
    current = names[0]
    rest = text
    order: list[str] = []
    while True:
        index, name, label = _find_marker(rest, pairs)
        if index == -1:
            blocks[current] = (blocks[current] + rest).strip()
            break
        blocks[current] = (blocks[current] + rest[:index]).strip()
        rest = rest[index + len(label):]
        if name not in order:
            order.append(name)
        current = name
    if not order:
        return blocks
    # 按模型实际写出的块顺序返回，前端据此按模型的选择顺序渲染。
    # 没写出来的块排在后面，键集合不变。
    ordered = {name: blocks[name] for name in order}
    for name in names:
        if name not in ordered:
            ordered[name] = blocks[name]
    return ordered


def strip_labels(text: str, names: list[str]) -> str:
    """去掉标记只留正文。落盘与历史装配用，避免标记被当成内容照抄。"""
    blocks = split_blocks(text, names)
    return chr(10).join(part for part in blocks.values() if part)


class StructureSplitter:
    """流式分块器。

    feed 吃掉一段增量，返回 [(块名, 文本), ...]，文本已去掉标记。
    标记以【开头、】结尾：缓冲区末尾若是还没闭合的【，它可能是被切开的
    标记开头，留到下一片再判。
    """

    def __init__(self, names: list[str]) -> None:
        self._names = list(names)
        self._pairs = _label_pairs(self._names)
        self._buffer = ""
        self._current = self._names[0] if self._names else ""
        self._at_start = False

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """吃掉一段增量，返回归属到各块的文本片段。"""
        if not chunk or not self._pairs:
            return []
        self._buffer += chunk
        out: list[tuple[str, str]] = []
        while True:
            index, name, label = _find_marker(self._buffer, self._pairs)
            if index == -1:
                hold = self._hold_len()
                cut = len(self._buffer) - hold
                if cut > 0:
                    piece = self._buffer[:cut]
                    if self._at_start:
                        piece = piece.lstrip()
                    if piece:
                        out.append((self._current, piece))
                        self._at_start = False
                    self._buffer = self._buffer[cut:]
                return out
            if index > 0:
                piece = self._buffer[:index].rstrip()
                if piece:
                    out.append((self._current, piece))
                    self._at_start = False
            self._buffer = self._buffer[index + len(label):]
            self._current = name
            self._at_start = True

    def flush(self) -> list[tuple[str, str]]:
        """结束流时吐出剩余文本。"""
        if not self._buffer or not self._pairs:
            return []
        piece = self._buffer
        if self._at_start:
            piece = piece.lstrip()
        piece = piece.rstrip()
        self._buffer = ""
        if not piece:
            return []
        return [(self._current, piece)]

    def _hold_len(self) -> int:
        """缓冲区末尾要留到下一片的尾巴长度。

        两种情况：
        - 末尾是被切开的标记（以【开头、还没闭合），连同它前面的空白一起留；
        - 末尾是空白字符，留到下一片，块首块尾的空白由最终解析统一裁掉。

        这样流式拼出来的块与 split_blocks 的结果一致。
        """
        buffer = self._buffer
        index = buffer.rfind("【")
        if index != -1 and "】" not in buffer[index:]:
            head = buffer[:index].rstrip()
            return len(buffer) - len(head)
        stripped = buffer.rstrip()
        return len(buffer) - len(stripped)
