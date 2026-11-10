"""自动建卡与 AI 补全机制测试。

覆盖：
- 回合末抽到新人物就自动落成配角卡，并记下来源（用户 / 模型）；
- 关系程度按出现轮数与是否开过口判定，且只升不降；
- 待完善的卡不进 supporting 段；
- 补全：能解析就填三层并摘掉待完善标记，解析不出或服务不可达就留在待完善。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import store
from app.cards import get_card
from app.cards.auto_fill import fill_card, parse_fill_payload
from app.cards.auto_intro import (
    collect_new_characters,
    list_pending_cards,
    relation_level,
    refresh_relations,
)

from conftest import FakeAdapter

MAIN_CARD = "c_main_001"
SESSION = "s_auto_001"


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """预置一张主角卡。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    store.write_json(
        store.card_dir(MAIN_CARD) / "card.json",
        {
            "id": MAIN_CARD,
            "name": "角色A",
            "spec_version": "v1",
            "data": {
                "name": "角色A",
                "identity": "主角身份",
                "behavior": "主角行为",
                "speech": "主角说话",
            },
        },
    )
    return tmp_path


def test_relation_levels() -> None:
    """关系程度三档：出现轮数加上他有没有开过口。"""
    assert relation_level(1, False) == "相逢"
    assert relation_level(1, True) == "相识"
    assert relation_level(2, False) == "相识"
    assert relation_level(3, False) == "相熟"


def test_user_introduced_character_is_created(data_dir: Path) -> None:
    """用户点出名字就自动落卡，来源记成用户。"""
    user_text = "（那人自称角色I，说有份急件要交给你）"
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, user_text, "")
    assert len(cards) == 1
    data = cards[0]["data"]
    assert data["role"] == "supporting"
    assert data["introduced_by"] == "user"
    assert data["relation"] == "相逢"
    assert data["pending"] is True


def test_model_introduced_character_is_created(data_dir: Path) -> None:
    """模型自己带出来的角色，来源记成模型。"""
    assistant_text = "门外的人自称角色I，说有份急件要交给你。"
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, "", assistant_text)
    assert len(cards) == 1
    assert cards[0]["data"]["introduced_by"] == "ai"


def test_existing_character_is_not_created_twice(data_dir: Path) -> None:
    """同一个名字不重复建卡。"""
    user_text = "（那人自称角色I，说有份急件要交给你）"
    collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, user_text, "")
    again = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, user_text, "")
    assert again == []


def test_relation_ignores_model_repeating_name(data_dir: Path) -> None:
    """模型自己在回复里反复提他，不该把档位顶上去。

    这是实测踩出来的自增强闭环：配角段注入后模型每轮都提他，
    按模型输出计数的话档位永远升到「相熟」，分不出认识程度。
    """
    line = "角色I走近两步，角色I站在门口。"
    store.append_message(SESSION, MAIN_CARD, {"role": "assistant", "content": line})
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, "", line)
    for _ in range(5):
        store.append_message(
            SESSION, MAIN_CARD, {"role": "assistant", "content": "角色I还站在门口。"}
        )
    refresh_relations(MAIN_CARD, SESSION, "角色I又来了。")
    assert get_card(cards[0]["id"])["data"]["relation"] == "相逢"


def test_relation_counts_user_mentions(data_dir: Path) -> None:
    """用户提他两次是相识，提三次是相熟。"""
    intro = "（那人自称角色I，说有份急件要交给你）"
    store.append_message(SESSION, MAIN_CARD, {"role": "user", "content": intro})
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, intro, "")
    assert cards[0]["data"]["relation"] == "相逢"

    store.append_message(SESSION, MAIN_CARD, {"role": "user", "content": "角色I呢"})
    refresh_relations(MAIN_CARD, SESSION, "角色I呢")
    assert get_card(cards[0]["id"])["data"]["relation"] == "相识"

    store.append_message(SESSION, MAIN_CARD, {"role": "user", "content": "角色I还在吗"})
    refresh_relations(MAIN_CARD, SESSION, "角色I还在吗")
    assert get_card(cards[0]["id"])["data"]["relation"] == "相熟"


def test_relation_recognises_his_speech(data_dir: Path) -> None:
    """他开口说过话，一轮也算相识：被记住的不只是名字。"""
    intro = "（那人自称角色I，说有份急件要交给你）"
    store.append_message(SESSION, MAIN_CARD, {"role": "user", "content": intro})
    store.append_message(
        SESSION,
        MAIN_CARD,
        {"role": "assistant", "content": "“角色I说，信得先过一遍。”"},
    )
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, intro, "")
    assert cards[0]["data"]["relation"] == "相识"


def test_pending_cards_are_listed(data_dir: Path) -> None:
    """待完善的卡单独能列出来。"""
    user_text = "（那人自称角色I，说有份急件要交给你）"
    collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, user_text, "")
    pending = list_pending_cards()
    assert [item["name"] for item in pending] == ["角色I"]


def test_non_person_names_are_not_created(data_dir: Path) -> None:
    """不像人名的词组不建卡。

    自动建卡没有人工确认这一步，词组直接落卡就是污染用户卡库。
    实测踩到过：「怎么知」「随后站」「看完再」「直接」「戴着指套」
    「下转着笔」——全是从叙述句里切出来的碎片。
    """
    assistant_text = (
        "她没有立刻拆开信封，只是把信纸抚平了一遍。"
        "你是怎么知道这栋楼今晚只有我加班？"
        "角色I走近两步，指节扣着门框。"
        "看完再说吧。角色I没有动。"
    )
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, "", assistant_text)
    names = [str(card["data"]["name"]) for card in cards]
    assert "角色I" in names
    for noise in ("怎么知", "看完再", "直接", "随后站", "戴着指套", "下转着笔"):
        assert noise not in names


def test_person_name_like_accepts_real_names() -> None:
    """像人名的判据：常见姓开头，或复姓。"""
    from app.memory.extract import is_person_name_like

    for good in ("角色I", "角色A", "角色C", "角色D", "角色B", "角色Q", "Alice"):
        assert is_person_name_like(good) is True
    for bad in ("怎么知", "随后站", "看完再", "直接", "戴着指套", "下转着笔", "", "x"):
        assert is_person_name_like(bad) is False


def test_parse_fill_payload_handles_markers() -> None:
    """标记式是主路径：推理适配器把 ASCII 引号归一成中文，JSON 解析不了。"""
    body = (
        "【身份与处境】出版社夜班编辑，负责赶稿。\n"
        "【行为与细节】说话短，手里总捏着校样。\n"
        "【说话方式】短句，办事口吻。\n"
        "【所在位置】地址Q\n"
        "【当前心情】紧绷\n"
        "【随身物品】校样、钢笔"
    )
    data = parse_fill_payload(body)
    assert data["identity"] == "出版社夜班编辑，负责赶稿。"
    assert data["behavior"] == "说话短，手里总捏着校样。"
    assert data["speech"] == "短句，办事口吻。"
    assert data["state"]["location"] == "地址Q"
    assert data["state"]["mood"] == "紧绷"
    assert data["state"]["items"] == ["校样", "钢笔"]


def test_parse_fill_payload_handles_json_and_garbage() -> None:
    """模型偶尔自己给 JSON，也认；认不出就返回空。"""
    assert parse_fill_payload('{"identity": "夜班编辑"}')["identity"] == "夜班编辑"
    fenced = '```json\n{"identity": "夜班编辑"}\n```'
    assert parse_fill_payload(fenced)["identity"] == "夜班编辑"
    assert parse_fill_payload("我看不太懂这个请求") == {}
    assert parse_fill_payload("") == {}




def test_fill_card_completes_the_card(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """补全成功：三层与状态填进去，待完善标记摘掉。"""
    from app.cards import auto_fill

    user_text = "（那人自称角色I，说有份急件要交给你）"
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, user_text, "")
    payload = (
        "【身份与处境】出版社夜班编辑，负责赶稿，常年值夜。\n"
        "【行为与细节】说话短，手里总捏着校样。\n"
        "【说话方式】短句，办事口吻，不寒暄。\n"
        "【所在位置】地址Q\n"
        "【当前心情】紧绷\n"
        "【随身物品】校样"
    )
    monkeypatch.setattr(auto_fill, "LlamaCppAdapter", lambda: FakeAdapter(payload))
    result = fill_card(str(cards[0]["id"]), "深夜的地址R")
    assert result["ok"] is True
    data = get_card(cards[0]["id"])["data"]
    assert data["identity"] == "出版社夜班编辑，负责赶稿，常年值夜。"
    assert data["behavior"] == "说话短，手里总捏着校样。"
    assert data["speech"] == "短句，办事口吻，不寒暄。"
    assert data["state"]["location"] == "地址Q"
    assert data["pending"] is False


def test_fill_failure_keeps_card_pending(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """补不成：卡留在待完善，并记下原因。"""
    from app.cards import auto_fill

    user_text = "（那人自称角色I，说有份急件要交给你）"
    cards = collect_new_characters(MAIN_CARD, SESSION, MAIN_CARD, user_text, "")
    monkeypatch.setattr(
        auto_fill,
        "LlamaCppAdapter",
        lambda: FakeAdapter(error=RuntimeError("推理服务不可用")),
    )
    result = fill_card(str(cards[0]["id"]), "深夜的地址R")
    assert result["ok"] is False
    data = get_card(cards[0]["id"])["data"]
    assert data["pending"] is True
    assert "推理服务不可用" in str(data.get("fill_error") or "")
