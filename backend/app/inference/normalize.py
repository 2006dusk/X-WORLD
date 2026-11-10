"""模型输出的排版归一。

中文角色扮演的回合要求写明对白用中文引号，但本地模型在部分轮次会写成英文双引号，
多轮里还会跟着自己上一轮的写法继续写英文引号。提示层已经尽力，实测仍有约三成轮次
落回英文引号，因此在推理适配层做一次确定性的排版归一。

归一只改引号样式与段落结构，不动文字内容与标点。

引号配平也算样式：模型偶尔自带一个没有配对开引号的中文右引号，
实测八轮里两轮出现，这种右引号直接删掉。
"""

from __future__ import annotations

OPEN_QUOTE = "“"
CLOSE_QUOTE = "”"

# 模型被推离分布时会自己续写多轮对话，把模板标记当正文吐出来。
TEMPLATE_MARKERS = (
    "<|im_start|>",
    "<|im_end|>",
    "<|im_sep|>",
    "<|endoftext|>",
)
MAX_MARKER_LEN = max(len(marker) for marker in TEMPLATE_MARKERS)


def cut_at_template_marker(text: str) -> str:
    """在第一个模板标记处截断。

    标记之后的内容是模型伪装的下一轮：它自己替用户写了一句，再自己接。
    这不是剧情内容，留在气泡里既扎眼，又会被当成用户说的话写进历史。
    """
    if not text:
        return text
    positions = [text.find(marker) for marker in TEMPLATE_MARKERS]
    positions = [pos for pos in positions if pos != -1]
    if not positions:
        return text
    return text[: min(positions)]


def split_marker_tail(buffer: str) -> tuple[str, str]:
    """把可能是标记前缀的尾巴切出来，等下一片到达再判。

    标记可能跨分片边界：只按整串匹配会漏掉 "<|im_st" + "art|>" 这种切法。
    """
    for size in range(min(len(buffer), MAX_MARKER_LEN - 1), 0, -1):
        tail = buffer[-size:]
        for marker in TEMPLATE_MARKERS:
            if marker.startswith(tail):
                return buffer[:-size], tail
    return buffer, ""


def _convert(text: str, opened: bool) -> tuple[str, bool]:
    """把英文双引号按开合顺序换成中文引号，返回结果与新的开合状态。"""
    out: list[str] = []
    for char in text:
        if char != '"':
            out.append(char)
            continue
        if opened:
            out.append(CLOSE_QUOTE)
            opened = False
        else:
            out.append(OPEN_QUOTE)
            opened = True
    return "".join(out), opened


def normalize_cjk_quotes(text: str) -> str:
    """把成对的英文双引号换成中文引号，奇数个时最后一个原样保留。"""
    if '"' not in text:
        return close_unclosed_openers(drop_stray_closers(text)[0])
    converted, _ = _convert(text, False)
    return close_unclosed_openers(drop_stray_closers(converted)[0])


def drop_stray_closers(text: str, balance: int = 0) -> tuple[str, int]:
    """删掉没有配对开引号的右引号。

    模型偶尔自带一个中文右引号，开引号却在前一轮或没有出现。实测八轮里
    有两轮多出一个右引号，开头就是 」 或 」，在气泡里很明显。归一层的
    职责是引号样式，这类没有配对的右引号按样式处理：删掉，不动文字。
    返回处理后的文本与剩余未闭合的开引号数。
    """
    if not text:
        return text, balance
    out: list[str] = []
    for char in text:
        if char == OPEN_QUOTE:
            balance += 1
            out.append(char)
        elif char == CLOSE_QUOTE:
            if balance > 0:
                balance -= 1
                out.append(char)
        else:
            out.append(char)
    return "".join(out), balance


def close_unclosed_openers(text: str) -> str:
    """把没配上的开引号改成右引号。

    模型偶尔在句中连开两个左引号：第一个其实想写的是右引号。
    实测（角色B）：「利息是今晚的夜，还是……“她指尖顺着你的喉结往下滑」，
    这一个左引号再没有配对的右引号，留在气泡里看起来像少半个引号。

    判定是确定的：从左往右扫，遇到左引号时若已经有一个没配上，
    它就是那个想写成右引号的，按右引号处理。中式嵌套引号另有
    『』与「」，不会走到这条规则上。
    """
    if "\u201c" not in text:
        return text
    out: list[str] = []
    opened = False
    for char in text:
        if char == OPEN_QUOTE:
            if opened:
                # 上一个左引号没配上，这个就是它的右引号。
                out.append(CLOSE_QUOTE)
                continue
            opened = True
            out.append(char)
            continue
        if char == CLOSE_QUOTE:
            if opened:
                opened = False
            out.append(char)
            continue
        out.append(char)
    return "".join(out)


def collapse_paragraphs(text: str) -> str:
    """把换行与空行压掉，让整段回合成为一段。

    回合要求写明「写成完整一段，不分行」，但本地模型在提示层没完全照办：
    去掉预设里的格式示例后，实测六轮里五轮会自行分段。段落是排版要求，
    不是文字内容，因此在归一层确定性地压平，不改任何字词。

    只删换行符，不插入空格：中文正文删换行后仍是通顺的连续句子，
    代价是极少数纯英文词被换行拆开时会粘在一起。
    """
    if not text:
        return text
    if "\n" not in text and "\r" not in text:
        return text
    flat = text.replace("\r\n", "").replace("\r", "").replace("\n", "")
    while "  " in flat:
        flat = flat.replace("  ", " ")
    return flat


# 回合末尾的说明性括号行。模型看到字数与结构约束后，有时会在末尾追加
# 一行括号写的合规说明，例如「（注意：以上为上一回合的延续与扩展，符合字数要求并推动剧情发展。）」。
# 这类说明不是剧情内容，命中说明性词的一律删掉。
ANNOTATION_MARKERS = (
    "注意",
    "新信息",
    "以上为",
    "符合",
    "字数",
    "要求",
    "推进剧情",
    "延续与扩展",
    "说明",
    "备注",
    "总结",
    "提示",
)


def drop_annotation_lines(text: str) -> str:
    """删掉回合末尾的说明性括号行。

    判定口径：整行是中文括号包裹的，且命中说明性词。行内的正常括号不受影响，
    只处理末尾的连续几行。删过之后把尾部空行一并去掉。
    """
    if not text or "（" not in text:
        return text
    lines = text.split("\n")
    removed = False
    while lines:
        last = lines[-1].strip()
        if (
            last.startswith("（")
            and last.endswith("）")
            and any(marker in last for marker in ANNOTATION_MARKERS)
        ):
            lines.pop()
            removed = True
            continue
        break
    if not removed:
        return text
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# 帝王自称。宫廷场景里模型会滑到这一组词：卡里写明角色自称什么，
# 但场景先验与对话示例都把她往皇帝那边推，提示层怎么写都拉不回来。
IMPERIAL_SELF_REFERENCES = ("\u6715", "\u5152\u4eba", "\u672c\u5bab")


def normalize_self_reference(text: str, pronoun: str) -> str:
    """把台词里的帝王自称换成角色卡声明的自称。

    实测（角色A，地址A）：卡里写「她自称我」，对话示例、场景与用户
    称呼都把她往「陛下」那边推，提示层三种位置都试过（卡内说话层、system
    段的规则、最后一轮输入的回合要求），自称一次都没纠正过来，四轮里
    「朕」出现六到十次。这层按卡片的声明做确定性替换。
    """
    if not text or not pronoun:
        return text
    for word in IMPERIAL_SELF_REFERENCES:
        if word == pronoun:
            continue
        text = text.replace(word, pronoun)
    return text


class QuoteStreamNormalizer:
    """流式场景下的引号归一。"""

    # 分片边界可能把一对引号拆开，因此保留尾部未闭合的半个引号，
    # 等下一片到达再补齐；开合状态在分片之间延续，
    # 保证客户端拼出的整体文本与一次性归一结果一致。

    def __init__(self) -> None:
        self._carry = ""
        self._opened = False
        self._cjk_balance = 0
        self._marker_carry = ""
        self._truncated = False

    def feed(self, chunk: str) -> str:
        """吃掉一段增量，返回可以安全发给客户端的文本。"""
        if self._truncated:
            return ""
        if not chunk:
            return ""
        buffer = self._marker_carry + chunk
        buffer, self._marker_carry = split_marker_tail(buffer)
        marker_at = -1
        for marker in TEMPLATE_MARKERS:
            found = buffer.find(marker)
            if found != -1 and (marker_at == -1 or found < marker_at):
                marker_at = found
        if marker_at != -1:
            # 模板标记之后是模型伪造的下一轮，从这里开始的内容全丢。
            self._truncated = True
            buffer = buffer[:marker_at]
            self._marker_carry = ""
        buffer = self._carry + buffer
        tail = buffer.rfind('"')
        if tail == -1:
            self._carry = ""
            converted, self._opened = _convert(buffer, self._opened)
            return self._drop_stray(converted)
        if tail == len(buffer) - 1:
            self._carry = buffer[tail:]
            converted, self._opened = _convert(buffer[:tail], self._opened)
            return self._drop_stray(converted)
        self._carry = ""
        converted, self._opened = _convert(buffer, self._opened)
        return self._drop_stray(converted)

    def _drop_stray(self, text: str) -> str:
        """删掉没有配对开引号的右引号，开合状态在分片之间延续。"""
        text, self._cjk_balance = drop_stray_closers(text, self._cjk_balance)
        return close_unclosed_openers(text)

    def flush(self) -> str:
        """结束流时把保留的半个引号补上。"""
        self._marker_carry = ""
        rest = self._carry
        self._carry = ""
        converted, self._opened = _convert(rest, self._opened)
        return self._drop_stray(converted)
