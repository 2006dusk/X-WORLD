"""文件化数据层测试：角色卡、世界书、预设与会话。"""

from __future__ import annotations

import json

import pytest

from app.cards import (
    export_card_json,
    export_card_markdown,
    get_card,
    import_card_json,
    list_cards,
    save_card,
)
from app.presets import list_prompt_groups, list_regex_rules, save_preset, split_prompt_groups
from app.worldbooks import (
    create_entry,
    delete_entry,
    get_book,
    list_entries,
    match_entries,
    save_book,
    update_entry,
)
from app import store as file_store


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    return tmp_path


V2_CARD = {
    "name": "角色K",
    "description": "在地址N经营地址U的调音师",
    "personality": "沉默、观察力强",
    "scenario": "雨夜的地址N",
    "first_mes": "门铃响了一声。",
    "mes_example": "<START>",
    "creator": "tester",
    "character_version": "1.0",
}

V3_CARD = {
    "spec": "charcard_spec_v3",
    "spec_version": "3.0",
    "data": {
        "name": "角色L",
        "description": "喜欢在雨天散步的录音师",
        "personality": "温和",
        "scenario": "雨夜",
        "first_mes": "雨停了。",
        "mes_example": "<START>",
        "creator": "tester",
        "character_version": "2.0",
    },
}

V1_CARD = {
    "char_name": "旧卡角色",
    "char_persona": "来自 V1 规范",
    "world_scenario": "旧世界",
    "char_greeting": "你好。",
}


class TestCards:
    def test_import_v2_and_read_back(self):
        card = import_card_json(V2_CARD)
        assert card["id"]
        assert card["data"]["name"] == "角色K"
        # 外部卡导入后一律落成内部 v1 三层：格式不再分叉。
        assert card["spec_version"] == "v1"
        stored = get_card(card["id"])
        assert stored is not None
        assert stored["data"]["identity"].startswith("在地址N")
        assert stored["data"]["behavior"] == "沉默、观察力强"
        # 说话方式层外部卡没有，用默认那条补上。
        assert stored["data"]["speech"]
        # 旧字段名不再留在卡上。
        assert "description" not in stored["data"]
        assert "personality" not in stored["data"]
        # 本项目不存的字段：场景、开场白、创作者、角色版本。
        for key in ("scenario", "first_mes", "creator", "character_version"):
            assert key not in stored["data"]

    def test_import_v3_uses_data_field(self):
        card = import_card_json(V3_CARD)
        assert card["data"]["name"] == "角色L"
        # V3 卡的 data 字段照样转成内部 v1 三层。
        assert card["spec_version"] == "v1"
        assert card["data"]["identity"].startswith("喜欢在雨天散步")
        assert card["data"]["behavior"] == "温和"

    def test_import_v1_maps_fields(self):
        card = import_card_json(V1_CARD)
        assert card["data"]["name"] == "旧卡角色"
        assert card["data"]["identity"] == "来自 V1 规范"

    def test_tags_normalized_to_controlled_vocabulary(self):
        """标签归一：别名归正名、去重、按受控词表排序，词表外的保留。

        标签进提示词时是「角色类型标签（必须符合）」，写什么就是给模型下了
        什么类型的约束。同义写法（4i 与四爱）不归一的话，四张卡各写各的，
        同一类关系会有好几个名字。
        """
        card = import_card_json(
            {
                "name": "标签角色",
                "description": "描述",
                "personality": "性格",
                "tags": ["4i", "脚下玩法", "女S", "四爱", "自定义标签"],
            }
        )
        assert card["data"]["tags"] == [
            "四爱",
            "女性支配",
            "足控调教",
            "自定义标签",
        ]

    def test_json_export_roundtrip_is_stable(self):
        card = import_card_json(V2_CARD)
        exported = export_card_json(card["id"])
        data = json.loads(exported.decode("utf-8"))
        again = import_card_json(data["data"])
        assert again["data"] == card["data"]

    def test_markdown_export_has_front_matter_and_sections(self):
        card = import_card_json(V2_CARD)
        text = export_card_markdown(card["id"])
        assert text.startswith("---")
        assert "name: 角色K" in text
        assert "## 身份与处境" in text
        assert "## 行为与细节" in text
        assert "## 说话方式" in text
        assert "## 对话示例" in text
        assert "## 场景" not in text

    def test_list_cards_returns_summaries(self):
        import_card_json(V2_CARD)
        import_card_json(V3_CARD)
        listed = list_cards()
        assert len(listed) == 2
        assert {item["name"] for item in listed} == {"角色K", "角色L"}

    def test_card_files_land_on_disk(self):
        card = import_card_json(V2_CARD)
        path = file_store.card_dir(card["id"]) / "card.json"
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8"))["name"] == "角色K"

    def test_save_card_rejects_unknown_spec(self):
        from app.errors import ApiError

        with pytest.raises(ApiError):
            save_card({"not": "a card"})


class TestWorldbooks:
    def _book(self) -> dict:
        return save_book({"name": "地址N", "entries": []})

    def test_create_and_list_entries(self):
        book = self._book()
        entry = create_entry(book["id"], {"keys": ["雨夜"], "content": "地址N的雨夜很安静"})
        assert entry["id"]
        entries = list_entries(book["id"])
        assert [item["id"] for item in entries] == [entry["id"]]

    def test_update_and_delete_entry(self):
        book = self._book()
        entry = create_entry(book["id"], {"keys": ["甲"], "content": "甲"})
        updated = update_entry(book["id"], entry["id"], {"content": "乙"})
        assert updated["content"] == "乙"
        assert delete_entry(book["id"], entry["id"]) is True
        assert list_entries(book["id"]) == []

    def test_match_keyword_and_priority(self):
        entries = [
            {"id": "a", "keys": ["王都"], "content": "a", "priority": 10},
            {"id": "b", "keys": ["王都"], "content": "b", "priority": 90},
            {"id": "c", "keys": ["无关"], "content": "c", "priority": 99},
        ]
        hits = match_entries(entries, "我们前往王都")
        assert [item["id"] for item in hits] == ["b", "a"]

    def test_constant_entry_outranks_keyword(self):
        entries = [
            {"id": "k", "keys": ["王都"], "content": "k", "priority": 80},
            {"id": "c", "keys": [], "content": "c", "priority": 50, "constant": True},
        ]
        hits = match_entries(entries, "我们前往王都")
        assert [item["id"] for item in hits] == ["c", "k"]

    def test_selective_requires_secondary(self):
        entries = [
            {
                "id": "s",
                "keys": ["王都"],
                "secondary_keys": ["深夜"],
                "selective": True,
                "content": "s",
                "priority": 90,
            },
        ]
        assert match_entries(entries, "我们前往王都") == []
        hits = match_entries(entries, "我们前往王都", secondary_text="深夜时分")
        assert [item["id"] for item in hits] == ["s"]

    def test_case_sensitive_and_regex(self):
        entries = [{"id": "a", "keys": ["Rain"], "content": "a", "case_sensitive": True}]
        assert match_entries(entries, "rainy night") == []
        entries = [{"id": "a", "keys": ["Rain"], "content": "a"}]
        assert [item["id"] for item in match_entries(entries, "rainy night")] == ["a"]
        entries = [{"id": "a", "keys": [r"rain\w+"], "content": "a", "match_regex": True}]
        assert [item["id"] for item in match_entries(entries, "rainy night")] == ["a"]

    def test_book_file_lands_on_disk(self):
        book = self._book()
        path = file_store.world_books_dir() / f"{book['id']}.json"
        assert path.exists()
        assert get_book(book["id"])["name"] == "地址N"


class TestPresets:
    def test_save_and_split_prompt_groups(self):
        preset = save_preset(
            {
                "name": "基础预设",
                "prompts": [
                    {"name": "主提示", "content": "你是一个角色", "role": "system", "order": 0},
                    {"name": "补充", "content": "补充说明", "role": "user", "order": 1},
                ],
            }
        )
        system, others = split_prompt_groups(preset["id"])
        assert [item["name"] for item in system] == ["主提示"]
        assert [item["name"] for item in others] == ["补充"]
        groups = list_prompt_groups(preset["id"])
        assert [item["order"] for item in groups] == sorted(item["order"] for item in groups)

    def test_regex_rules_roundtrip(self):
        preset = save_preset(
            {
                "name": "带正则",
                "prompts": [],
                "regex_rules": [
                    {"name": "去思考标签", "pattern": "<think>.*?</think>", "replacement": "", "target": "output"}
                ],
            }
        )
        rules = list_regex_rules(preset["id"])
        assert rules[0]["name"] == "去思考标签"


class TestSessions:
    def test_create_append_and_read(self):
        session = file_store.create_session("c_test", "p_test")
        file_store.append_message(session["id"], "c_test", {"role": "user", "content": "你好"})
        file_store.append_message(session["id"], "c_test", {"role": "assistant", "content": "在"})
        messages = file_store.get_messages(session["id"], "c_test")
        assert [item["content"] for item in messages] == ["你好", "在"]

    def test_session_file_is_jsonl(self):
        session = file_store.create_session("c_test")
        file_store.append_message(session["id"], "c_test", {"role": "user", "content": "x"})
        path = file_store.sessions_dir("c_test") / f"{session['id']}.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["role"] == "session"

    def test_markdown_export(self):
        session = file_store.create_session("c_test")
        file_store.append_message(session["id"], "c_test", {"role": "user", "content": "你好"})
        text = file_store.export_session_markdown(session["id"], "c_test")
        assert "# 会话" in text
        assert "你好" in text

    def test_list_sessions_filters_by_character(self):
        file_store.create_session("c_a")
        file_store.create_session("c_b")
        listed = file_store.list_sessions("c_a")
        assert len(listed) == 1
        assert listed[0]["character_id"] == "c_a"


class TestEmptyCardDirs:
    """卡目录里的空壳目录清理。"""

    def test_prune_removes_empty_shell_and_keeps_real_card(self):
        cards = file_store.cards_dir()
        (cards / "c_shell").mkdir(parents=True, exist_ok=True)
        (cards / "c_real").mkdir(parents=True, exist_ok=True)
        (cards / "c_real" / "card.json").write_text("{}", encoding="utf-8")

        removed = file_store.prune_empty_card_dirs()

        assert removed >= 1
        assert not (cards / "c_shell").exists()
        assert (cards / "c_real" / "card.json").exists()

    def test_prune_is_idempotent(self):
        file_store.prune_empty_card_dirs()
        assert file_store.prune_empty_card_dirs() == 0

    def test_prune_clears_empty_character_data_dirs(self):
        """角色数据目录里的空壳也要清，global 作用域留着。"""
        state_dir = file_store.states_dir("c_prune_shell")
        sessions_dir = file_store.sessions_dir("c_prune_shell")
        entities_dir = file_store.entities_dir("c_prune_shell")
        global_state = file_store.states_dir(file_store.GLOBAL_SCOPE)

        removed = file_store.prune_empty_card_dirs()

        assert removed >= 3
        assert not state_dir.exists()
        assert not sessions_dir.exists()
        assert not entities_dir.exists()
        assert global_state.exists()


class TestCardDataCleanup:
    """删卡要带走这个角色的数据，不留孤儿聊天记录。"""

    def test_delete_card_removes_character_sessions(self):
        card = file_store.save_card({"id": "c_gone", "name": "临时卡"})
        session = file_store.create_session("c_gone")
        file_store.append_message(
            session["id"], "c_gone", {"role": "user", "content": "x"}
        )
        path = file_store.sessions_dir("c_gone") / (session["id"] + ".jsonl")
        assert path.exists()

        file_store.delete_card("c_gone")

        assert not path.exists()

    def test_delete_card_keeps_other_card_sessions(self):
        """删一张卡不动别人的会话。"""
        file_store.save_card({"id": "c_keep", "name": "留下的卡"})
        session = file_store.create_session("c_keep")
        path = file_store.sessions_dir("c_keep") / (session["id"] + ".jsonl")
        file_store.save_card({"id": "c_drop", "name": "要删的卡"})

        file_store.delete_card("c_drop")

        assert path.exists()
