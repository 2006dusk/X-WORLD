"""记忆抽取。

对话结束后从消息中抽取事实、事件与关系。抽取与主生成请求解耦，
失败只记录日志，不影响交互链路。
"""

from __future__ import annotations

import re
import threading
from typing import Any, Protocol, Sequence

_QUOTED = re.compile(r"[「『\"“]([^」』\"”]{1,12})[」』\"”]")
_SPEECH = re.compile(r"([\u4e00-\u9fff]{2,4})(?:说道|说|道|回答|表示|提到|喊道)")
_LATIN_NAME = re.compile(r"\b([A-Z][a-z]{2,15})\b")
_FACT = re.compile(r"(.{4,60}?)(?:是|叫|名为|来自|住在|拥有)(.{2,30})")
_RELATION = re.compile(r"(.{2,20}?)(?:喜欢|讨厌|害怕|信任|属于|跟随|是)(.{2,20})")
_IMPORTANT = re.compile(r"(记住|约定|重要|秘密|名字是|生日|死亡|发誓)")
_EVENT = re.compile(r"(去了|到了|离开|回来|出发|到达|发生|战斗|会议|考试)(.{0,30})")


class Extractor(Protocol):
    """抽取器接口。"""

    name: str

    def extract(
        self,
        messages: Sequence[dict[str, Any]],
        character_id: str | None,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        ...


class RuleExtractor:
    """规则式抽取器。不依赖任何模型。"""

    name = "rule"

    def extract(
        self,
        messages: Sequence[dict[str, Any]],
        character_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def push(kind: str, content: str, importance: float, source: str) -> None:
            text = content.strip()
            if len(text) < 2:
                return
            key = f"{kind}:{text}"
            if key in seen:
                return
            seen.add(key)
            items.append(
                {
                    "kind": kind,
                    "content": text,
                    "importance": importance,
                    "character_id": character_id,
                    "session_id": session_id,
                    "meta": {"source": source},
                }
            )

        for message in messages:
            content = str(message.get("content") or "")
            if not content:
                continue
            role = str(message.get("role") or "unknown")

            for match in _QUOTED.findall(content):
                push("fact", f"提到 {match}", 0.55, role)
            for match in _SPEECH.findall(content):
                push("fact", f"{match} 说过话", 0.5, role)
            for match in _LATIN_NAME.findall(content):
                push("fact", f"出现人名 {match}", 0.45, role)
            for subject, predicate in _FACT.findall(content):
                push("fact", f"{subject.strip()} {predicate.strip()}", 0.6, role)
            for subject, predicate in _RELATION.findall(content):
                push("relation", f"{subject.strip()} 对 {predicate.strip()}", 0.55, role)
            for verb, tail in _EVENT.findall(content):
                push("event", f"{verb}{tail}".strip(), 0.5, role)

            if _IMPORTANT.search(content):
                push("fact", content[:80], 0.85, role)
        return items


_EXTRACTOR: Extractor | None = None


def get_extractor() -> Extractor:
    global _EXTRACTOR
    if _EXTRACTOR is None:
        _EXTRACTOR = RuleExtractor()
    return _EXTRACTOR


def extract_from_messages(
    messages: Sequence[dict[str, Any]],
    character_id: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """同步抽取，返回记忆候选。"""
    return get_extractor().extract(messages, character_id, session_id)


def extract_in_background(
    messages: Sequence[dict[str, Any]],
    character_id: str | None = None,
    session_id: str | None = None,
    on_done: Any = None,
) -> threading.Thread:
    """后台抽取，不阻塞交互响应。"""
    from app.memory.store import save_memory

    def worker() -> None:
        try:
            for item in extract_from_messages(messages, character_id, session_id):
                saved = save_memory(item)
                if on_done is not None:
                    on_done(saved)
        except Exception:
            return

    thread = threading.Thread(target=worker, name="xworld-memory-extract", daemon=True)
    thread.start()
    return thread

# 人名候选抽取。中文没有词边界，人名只能靠上下文位置推断：
# 说话动词之前、称谓之后、「名字叫」之后、领属结构里。
# 抽出来的是「像人名的词」，不代表真有人——是不是配角由用户确认。
_NAME_ADVERB = (
    "低声|大声|轻轻|缓缓|慢慢|连忙|赶忙|微微|淡淡|幽幽|冷冷|犹豫|迟疑|突然|忽然|随即|不禁"
)
# 代称形态：「角色X」这类「类别 + 字母」的写法，是数据里对真实姓名的替身。
# 抽取器要认得它，否则数据改成代称之后，自动建卡整条链路会失灵。
_ALIAS_TOKEN = r"角色[A-Z]"
_ALIAS_NAME = re.compile(r"角色[A-Z]")
_NAME_SPEECH = re.compile(
    r"((?:[\u4e00-\u9fff]{2,5}?|" + _ALIAS_TOKEN + r"))"
    r"(?:" + _NAME_ADVERB + r")?"
    r"(?:地|的|着|了|[，,、；;：:])?"
    r"(?:说道|问道|答道|回答|喊道|低语|开口|说|道)"
)
_NAME_CALL = re.compile(
    r"(?:叫做|名叫|名字叫|姓名是|自称|叫)"
    r"((?:[\u4e00-\u9fff]{2,4}?|" + _ALIAS_TOKEN + r"))"
    r"(?:的|说|道|喊|问|，|。|！|？|、|,|\s|$)"
)
_NAME_POSS = re.compile(
    r"((?:[\u4e00-\u9fff]{2,4}|" + _ALIAS_TOKEN + r"))的(?:声音|手|眼睛|目光|视线|脸色|笑容|背影|侧脸|名字|语气|嘴角|表情|肩膀|衣服|发丝|指尖)"
)
_NAME_TITLE = re.compile(
    r"((?:[\u4e00-\u9fff]{2,4}|" + _ALIAS_TOKEN + r"))(?:先生|女士|老师|同学|医生|护士|队长|经理|主管|警官|大夫|阿姨|叔叔|哥哥|姐姐|弟弟|妹妹|前辈|学长|学姐|班长|校长|院长|科长|组长|行长|老板|司机|厨师|保姆)"
)
# 动作词定位的名字，前一个字必须是标点或句首：叙述句中间切出来的多半是
# 词组（「把今晚打乱」），不是人名。
_NAME_ACTION = re.compile(
    r"(^|[^\u4e00-\u9fff])((?:[\u4e00-\u9fff]{2,4}?|" + _ALIAS_TOKEN + r"))"
    r"(?:走过来|走近|上前|皱眉|转身|推开|递过|接过|回头|抬头)"
)
# 强模式：说话人、自报姓名、领属结构、称谓，命中即可信。
_NAME_STRONG_PATTERNS = (_NAME_SPEECH, _NAME_CALL, _NAME_POSS, _NAME_TITLE)
# 弱模式：只靠一个动作动词定位（「X 走过来」），要求名字紧跟在标点或句首
# 之后，叙述句中间切出来的多半是词组。
_NAME_WEAK_PATTERNS = (_NAME_ACTION,)
_NAME_PATTERNS = _NAME_STRONG_PATTERNS + _NAME_WEAK_PATTERNS

# 头部要去掉的代词与副词：它们常紧贴人名（「她轻声说道」里的「她轻声」）。
_NAME_PREFIX_STOP = (
    "她们", "他们", "我们", "你们", "自己", "对方", "众人", "大家", "旁人", "那人",
    "此人", "某人", "这人", "有人", "他人", "别人", "二人", "随即", "接着", "随后",
    "这时", "同时", "然后", "于是", "只是", "依旧", "连忙", "赶忙", "犹豫", "迟疑",
    "闻言", "说完", "稍后", "最后", "起初", "轻轻", "低声", "大声", "缓缓", "慢慢",
    "忽然", "突然", "不禁", "微微", "淡淡", "悄悄", "径直", "幽幽", "冷冷", "不甘",
    "无奈", "便", "也", "又", "才", "就", "被", "把", "让", "跟", "和", "与",
    "从", "对", "而", "的", "了", "着", "是", "很", "更", "都", "在",
    "她", "他", "它", "我", "你", "自称", "名字", "姓名", "叫做",
)

# 尾部要去掉的虚词：名字后面常跟着助词或动词（「角色D的」）。
_NAME_TAIL_CHARS = "的了着过吗呢吧呀啊哦嗯是在和与并而"

# 名字末尾是这些字，多半是动作词把名字切错了（「角色D点」）。
_NAME_TAIL_VERBS = frozenset("点走来去推递转回抬皱摇递给上前到过")

# 尾部要去掉的副词：「角色A淡淡地开口」里的「淡淡」。
_NAME_TAIL_ADVERBS = (
    "低声", "大声", "轻轻", "缓缓", "慢慢", "连忙", "赶忙", "微微", "淡淡",
    "幽幽", "冷冷", "犹豫", "迟疑", "突然", "忽然", "随即", "不禁",
)

# 整个词就是这些的时候，不是人名。
_NAME_STOPWORDS = frozenset(
    {
        "她", "他", "它", "我", "你", "您", "自己", "对方", "众人", "大家", "有人",
        "那人", "此人", "某人", "这人", "他人", "旁人", "别人", "二人", "三人",
        "姑娘", "少年", "少女", "男人", "女人", "孩子", "好友", "同伴", "身影",
        "声音", "语气", "目光", "视线", "眼神", "脸色", "笑容", "背影", "侧脸",
        "名字", "嘴角", "表情", "肩膀", "衣服", "发丝", "指尖",
        "老师", "主任", "队长", "经理", "主管", "警官", "大夫", "医生", "护士",
        "同学", "先生", "女士", "阿姨", "叔叔", "哥哥", "姐姐", "弟弟", "妹妹",
        "前辈", "学长", "学姐", "班长", "校长", "院长", "科长", "组长", "行长",
        "老板", "司机", "厨师", "保姆", "学生", "客人", "来客",
        "接着", "于是", "然后", "随后", "这时", "同时", "轻声", "大声", "缓缓",
        "慢慢", "忽然", "突然", "不禁", "微微", "淡淡", "悄悄", "径直", "幽幽",
        "冷冷", "连忙", "赶忙", "犹豫", "迟疑", "闻言", "说完", "稍后", "最后",
        "起初", "一声", "片刻", "半晌", "心头", "眼底", "唇角", "手心", "四周",
        "周围", "外面", "里面", "上面", "下面", "前面", "后面", "中间", "旁边",
        "东方", "西方", "南方", "北方", "今天", "明天", "昨天", "现在", "刚才",
        "既然", "但是", "可是", "如果", "因为", "所以", "然后", "而且", "不过",
        "只是", "已经", "仍然", "于是", "并且", "尽管", "哪怕", "就算", "即使",
        "除非", "除了", "或者", "不仅", "而是", "难道", "听说", "别说", "好像",
        "似乎", "大概", "也许", "或许", "反正", "到底", "究竟", "简直", "根本",
        "明明", "偏偏", "居然", "竟然", "原来", "终于", "始终", "一直", "从来",
        "一向", "正好", "恰好", "顺便", "接着", "紧接着", "随后", "紧接着",
    }
)

# 出现在人名里就说明抽错了：这些字几乎不出现在中文人名中。
_NAME_BAD_CHARS = frozenset(
    "那个们的那这某些每之了是在和就也都又不没把被从向对与但而或只会能要想说道来去时地得他她它我你您"
)

# 英文人名：首字母大写的词，但常见虚词与月份要排掉。
_LATIN_STOPWORDS = frozenset(
    {
        "The", "She", "He", "They", "We", "You", "His", "Her", "Him", "Their",
        "Its", "This", "That", "These", "Those", "There", "Here", "Then", "When",
        "What", "Where", "Who", "Why", "How", "And", "But", "For", "With", "From",
        "Into", "Out", "Has", "Had", "Was", "Were", "Been", "Being", "Are", "Not",
        "Now", "New", "Old", "Yes", "Still", "Only", "Just", "Also", "Because",
        "While", "After", "Before", "Over", "Under", "Again", "Very", "Much",
        "Many", "Some", "Any", "All", "Both", "Each", "Few", "More", "Most",
        "Other", "Such", "Own", "Same", "Too", "Can", "Will", "Don", "Should",
        "Would", "Could", "Must", "Might", "Shall", "May", "Let", "Get", "Got",
        "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "January",
        "February", "March", "April", "June", "July", "August", "September",
        "October", "November", "December", "Monday", "Tuesday", "Wednesday",
        "Thursday", "Friday", "Saturday", "Sunday", "Oh", "Hello", "Please",
        "Thank", "Sorry", "Well", "Anyway", "Meanwhile",
    }
)


# 常见汉语姓氏。人名的第一个字几乎总是姓，所以「是不是人名」最省事的
# 判据是首字在不在姓氏表里。
_COMMON_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑"
    "薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹"
    "姚邵湛汪祁毛禹狄米贝臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季"
    "麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万柯管卢莫经"
    "房裘干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇邢裴陆荣翁荀羊惠甄曲家"
    "封储靳汲糜井段富巫焦牧山谷车侯班秋仲伊宫宁仇栾甘厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎印宿白怀蒲鄂索赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳姬"
    "申辅冉雍桑桂牛通边燕浦尚农温庄晏柴瞿阎充慕连茹艾鱼向古易慎戈廖步耿满"
    "弘匡国文寇广东欧沃利隆师巩聂晁敖冷辛阚简饶空沙鞠巢关蒯相查荆红游权盖"
    "桓公"
)

# 复姓与两字姓。
_COMPOUND_SURNAMES = (
    "欧阳", "司马", "上官", "诸葛", "东方", "独孤", "南宫", "西门", "皇甫", "尉迟",
    "长孙", "慕容", "司徒", "司空", "夏侯", "轩辕", "令狐", "宇文", "澹台", "公孙",
    "闻人", "端木", "太叔", "申屠", "公羊", "杞梁", "壤驷", "第五", "梁丘", "左丘",
    "东郭", "南门", "呼延", "羊舌", "微生", "岳父", "夹谷", "谷梁",
)

# 名字里几乎不出现的字：出现了多半是把词组切成了名字（「随后站」「戴着指套」）。
# 只收动作与生活类的字，形容词类的留下——那些真会出现在人名里。
_NAME_NON_NAME_CHARS = frozenset(
    "转着看站直翻拿握递推拉躲藏抓捏撕扯摸踩踏躺趴蹲迎送追跑跳吃喝睡开关"
)


def is_person_name_like(name: str) -> bool:
    """这个名字像不像一个人名。

    自动建卡没有人工确认这一步，抽出来的词组会直接变成一张卡，所以建卡前
    要过一道「像不像人名」的判据：首字是常见姓，或整体是复姓；再排掉名字
    里带动作字、虚字的情况。

    拉丁名一律算像人名——那套停用词表已经滤过一轮。
    「角色X」这类代称同样算，它是数据里对真实姓名的替身。
    """
    token = (name or "").strip()
    if not token:
        return False
    if not all("\u4e00" <= ch <= "\u9fff" for ch in token):
        return len(token) >= 2
    for compound in _COMPOUND_SURNAMES:
        if token.startswith(compound):
            return True
    if any(ch in _NAME_NON_NAME_CHARS for ch in token):
        return False
    return token[0] in _COMMON_SURNAMES


def _clean_name(token: str) -> str:
    """把一个候选词收拾成可能是人名的样子。"""
    name = token.strip()
    if _ALIAS_NAME.fullmatch(name):
        return name
    while name and name[-1] in _NAME_TAIL_CHARS:
        name = name[:-1]
    changed_tail = True
    while changed_tail and name:
        changed_tail = False
        for adverb in _NAME_TAIL_ADVERBS:
            if name.endswith(adverb) and len(name) - len(adverb) >= 2:
                name = name[: -len(adverb)]
                changed_tail = True
                break
    changed = True
    while changed and name:
        changed = False
        for prefix in _NAME_PREFIX_STOP:
            if name.startswith(prefix) and len(name) - len(prefix) >= 2:
                name = name[len(prefix):]
                changed = True
                break
    if len(name) < 2 or len(name) > 4:
        return ""
    if name in _NAME_STOPWORDS:
        return ""
    if any(ch in name for ch in _NAME_BAD_CHARS):
        return ""
    if name[-1] in _NAME_TAIL_VERBS:
        return ""
    if not all("\u4e00" <= ch <= "\u9fff" for ch in name):
        return ""
    return name


def is_self_introduced(text: str, name: str) -> bool:
    """这个名字是不是由「自称 / 叫 / 名叫」这类自报写法给出的。

    自报出来的名字可信度最高：叙述句里切出来的词组不会自称。
    """
    for match in _NAME_CALL.finditer(text):
        groups = [group for group in match.groups() if group]
        if groups and _clean_name(groups[-1]) == name:
            return True
    return False


def extract_character_names(text: str) -> list[str]:
    """从一段正文里抽人名候选，按出现顺序去重。

    抽的是「像人名的词」，不是「确认的人」。是不是配角由用户做，
    抽取器只负责把可能的名字交上来，交给用户确认。
    """
    if not text or not text.strip():
        return []
    found: list[str] = []
    seen: set[str] = set()
    weak: list[str] = []
    for pattern in _NAME_STRONG_PATTERNS:
        for token in pattern.findall(text):
            name = _clean_name(token)
            if not name or name in seen:
                continue
            seen.add(name)
            found.append(name)
    for pattern in _NAME_WEAK_PATTERNS:
        for match in pattern.finditer(text):
            groups = [group for group in match.groups() if group]
            if not groups:
                continue
            name = _clean_name(groups[-1])
            if not name or name in seen or name in weak:
                continue
            weak.append(name)
    for name in weak:
        seen.add(name)
        found.append(name)
    for token in _LATIN_NAME.findall(text):
        if token in _LATIN_STOPWORDS or token in seen:
            continue
        seen.add(token)
        found.append(token)
    return found
