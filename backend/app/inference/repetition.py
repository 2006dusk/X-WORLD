"""复读检测。

模型自己的上一轮回复就在上下文里，容易把上一轮的句子整句搬过来换个头继续说。
整轮相似度对这种「换头留尾」的复读不敏感：实测同一段里只有两句照搬时，
整轮相似度仍低于复读阈值。

这里按句检查：上一轮里的长句是否在新一轮里逐字出现。
判定只用确定性比对，不依赖模型判断。
"""

from __future__ import annotations

SENTENCE_END = "。！？"
MIN_SENTENCE_CHARS = 15

# 换头留尾的复读：上一轮的一整句被改了个开头照搬过来，整句比对看不出来。
# 实测（backend/tools/probe_story_run.py，四张卡各六回合）：角色上一轮说
# 「抬起头来看着我，记住这张脸，从今天起……」，下一轮把后半段原样搬过来，
# 只是前面换成了别的字。整句比对要求整句逐字出现，这种搬法漏判。
# 改成看最长公共子串：连续十八字以上逐字照搬就是复读。够长的逐字照搬
# 不是巧合，十八字在中文里已经是一个完整分句。
FRAGMENT_MIN_CHARS = 18


def split_sentences(text: str) -> list[str]:
    """按句末标点切句，保留标点。"""
    sentences: list[str] = []
    current: list[str] = []
    for char in text:
        current.append(char)
        if char in SENTENCE_END:
            sentences.append("".join(current))
            current = []
    if current:
        sentences.append("".join(current))
    return [item.strip() for item in sentences if item.strip()]


def _compact(text: str) -> str:
    """去掉空白，避免换行与空格影响逐字比对。"""
    return "".join(text.split())


def longest_common_substring(left: str, right: str) -> str:
    """两个字符串的最长公共子串。"""
    if not left or not right:
        return ""
    best = ""
    previous = [0] * (len(right) + 1)
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        for j in range(1, len(right) + 1):
            if left[i - 1] == right[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > len(best):
                    best = left[i - current[j]:i]
        previous = current
    return best


def copied_fragment(
    new_text: str,
    previous_text: str,
    min_chars: int = FRAGMENT_MIN_CHARS,
) -> str:
    """上一轮里被新一轮逐字搬走的最长片段，换头留尾也算。"""
    if not new_text or not previous_text:
        return ""
    piece = longest_common_substring(_compact(previous_text), _compact(new_text))
    return piece if len(piece) >= min_chars else ""


def copied_sentences(
    new_text: str,
    previous_text: str,
    min_chars: int = MIN_SENTENCE_CHARS,
) -> list[str]:
    """上一轮里被新一轮逐字搬过来的长句。"""
    if not new_text or not previous_text:
        return []
    copied: list[str] = []
    for sentence in split_sentences(previous_text):
        core = sentence.rstrip(SENTENCE_END).strip()
        if len(core) < min_chars:
            continue
        if core in new_text and core not in copied:
            copied.append(core)
    return copied


def is_repetitive(
    new_text: str,
    previous_text: str,
    min_chars: int = MIN_SENTENCE_CHARS,
) -> bool:
    """新一轮是否照搬了上一轮的内容。

    两种搬法都算：整句逐字照搬，或长片段换头留尾地照搬。
    片段阈值比整句阈值高：整句至少十五字，长片段要十八字。
    """
    if copied_sentences(new_text, previous_text, min_chars):
        return True
    return bool(copied_fragment(new_text, previous_text))
