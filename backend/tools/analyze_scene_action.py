r"""把模型输出拆成场景描写、动作描写与对白三块并量化。

这一轮新增的评估维度。做法是确定性规则，不用裁判：

1. 先按引号把对白整段摘出来，对白单独计量；
2. 剩下的叙述部分按句切分，逐句判定归属：
   - 场景句：命中环境与空间类词，且不含身体动作类词；
   - 动作句：命中身体动作类词；
   - 叙述句：两类都没命中；
3. 输出三块的字数占比、句数占比，以及每百字的动作具体度。

用法：
    python tools\analyze_scene_action.py
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
EXPL = DATA / "explicit_5set.json"

# 环境与空间类词。命中且不含动作词，判为场景句。
SCENE_WORDS = (
    "雨", "雨声", "雨点", "灯", "台灯", "红灯", "光", "影子", "暗", "黑暗", "亮",
    "声", "声音", "响", "静", "空气", "温度", "暖", "冷", "凉", "潮", "湿", "干",
    "气味", "味", "房间", "屋子", "窗外", "门外", "巷", "街", "夜", "雨夜", "深夜",
    "地址U", "控制台", "调音台", "推子", "监听", "音箱", "红灯", "瓷砖", "地址T",
    "镜子", "镜", "雾", "沙发", "弹簧", "床垫", "地址W", "楼梯", "小车", "车", "车厢",
    "座椅", "扶手", "金属", "面板", "毯", "窗帘", "缝", "角", "天花板", "地面", "地板",
    "空调", "暖风", "安全带", "皮带", "方向盘", "门", "锁", "台面", "桌面", "墙",
)

# 身体动作类词。命中即判为动作句。
ACTION_WORDS = (
    "手", "手指", "指尖", "掌心", "手腕", "臂", "肩", "腰", "胯", "腿", "膝", "脚",
    "背", "脊", "胸", "腹", "肚", "颈", "喉", "唇", "嘴", "齿", "牙", "舌", "下巴",
    "头发", "发根", "鬓角", "脸颊", "眼", "眼睫", "眉", "鼻", "耳", "身体", "身子",
    "吻", "亲", "咬", "舔", "含", "吞", "呼吸", "喘", "闷哼", "呼", "吸",
    "抱", "搂", "推", "压", "按", "扣", "抓", "握", "捏", "掐", "揉", "摸", "抚",
    "摩挲", "探", "伸", "插", "塞", "解", "扯", "脱", "披", "扣子", "拉链",
    "抬", "低", "俯", "倾", "转", "跨坐", "跨", "骑", "俯身", "仰", "弯", "伸进",
    "往下", "往上", "向前", "向后", "贴", "靠", "抵", "压住", "按住", "捂住", "握住",
)

DIALOGUE_RE = re.compile(r"[“「『]([^”」』]*)[”」』]")
ASCII_DQ_RE = re.compile(r'"([^"\n]{2,400})"')
SENTENCE_SPLIT = re.compile(r"[。！？!?…\n]+")


def hit_words(text: str, words: tuple[str, ...]) -> list[str]:
    return [w for w in words if w in text]


def split_parts(text: str) -> dict[str, object]:
    """把一段输出拆成对白、场景句、动作句、叙述句。"""
    dialogues = DIALOGUE_RE.findall(text)
    dialogues += ASCII_DQ_RE.findall(text)
    narration = DIALOGUE_RE.sub("", text)
    narration = ASCII_DQ_RE.sub("", narration)
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(narration) if s.strip()]
    scene_sent = []
    action_sent = []
    plain_sent = []
    for s in sentences:
        actions = hit_words(s, ACTION_WORDS)
        scenes = hit_words(s, SCENE_WORDS)
        if actions:
            action_sent.append((s, actions, scenes))
        elif scenes:
            scene_sent.append((s, scenes))
        else:
            plain_sent.append(s)
    dialogue_chars = sum(len(d) for d in dialogues)
    scene_chars = sum(len(s) for s, _ in scene_sent)
    action_chars = sum(len(s) for s, _, _ in action_sent)
    plain_chars = sum(len(s) for s in plain_sent)
    total = max(len(text), 1)
    return {
        "dialogue_count": len(dialogues),
        "dialogue_chars": dialogue_chars,
        "dialogue_ratio": round(dialogue_chars / total, 3),
        "scene_sentences": len(scene_sent),
        "scene_chars": scene_chars,
        "scene_ratio": round(scene_chars / total, 3),
        "action_sentences": len(action_sent),
        "action_chars": action_chars,
        "action_ratio": round(action_chars / total, 3),
        "plain_sentences": len(plain_sent),
        "plain_chars": plain_chars,
        "plain_ratio": round(plain_chars / total, 3),
        "scene_text": [s for s, _ in scene_sent],
        "action_text": [s for s, _, _ in action_sent],
        "dialogue_text": dialogues,
        "action_specific_hits": sorted({w for _, ws, _ in action_sent for w in ws}),
        "scene_specific_hits": sorted({w for _, ws in scene_sent for w in ws}),
    }


def main() -> int:
    rows = json.loads(EXPL.read_text(encoding="utf-8"))
    print("== 场景描写与动作描写拆分 ==")
    for row in rows:
        scenes = row.get("scenes") or []
        if not scenes:
            continue
        print("==", row.get("name"), row.get("params"), row.get("quant"))
        all_parts = []
        per_scene = []
        for scene in scenes:
            parts = []
            for turn in scene.get("turns") or []:
                p = split_parts(str(turn.get("content") or ""))
                p["turn"] = turn.get("turn")
                p["scene_id"] = scene.get("id")
                p["chars"] = int((turn.get("metrics") or {}).get("chars") or 0)
                parts.append(p)
            all_parts.extend(parts)
            per_scene.append((scene, parts))
        n = len(all_parts)
        if not n:
            continue
        scene_ratio = statistics.fmean([p["scene_ratio"] for p in all_parts])
        action_ratio = statistics.fmean([p["action_ratio"] for p in all_parts])
        dialogue_ratio = statistics.fmean([p["dialogue_ratio"] for p in all_parts])
        plain_ratio = statistics.fmean([p["plain_ratio"] for p in all_parts])
        scene_sent = statistics.fmean([p["scene_sentences"] for p in all_parts])
        action_sent = statistics.fmean([p["action_sentences"] for p in all_parts])
        no_action = sum(1 for p in all_parts if p["action_sentences"] == 0)
        no_scene = sum(1 for p in all_parts if p["scene_sentences"] == 0)
        no_dialogue = sum(1 for p in all_parts if p["dialogue_count"] == 0)
        print("  场景字数占比 {:.0%}  动作字数占比 {:.0%}  对白占比 {:.0%}  无归属占比 {:.0%}".format(
            scene_ratio, action_ratio, dialogue_ratio, plain_ratio))
        print("  每轮场景句 {:.1f}  动作句 {:.1f}".format(scene_sent, action_sent))
        print("  完全无动作句 {}/{}  完全无场景句 {}/{}  完全无对白 {}/{}".format(
            no_action, n, no_scene, n, no_dialogue, n))
        print("")
        for scene, parts in per_scene:
            sr = statistics.fmean([p["scene_ratio"] for p in parts]) if parts else 0
            ar = statistics.fmean([p["action_ratio"] for p in parts]) if parts else 0
            print("  场景 {} {}：场景占比 {:.0%}  动作占比 {:.0%}".format(scene.get("id"), scene.get("name"), sr, ar))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
