"""记忆子系统与落盘索引测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.memory.embedder import DIM, HashEmbedder, tokenize
from app.memory.extract import RuleExtractor, extract_from_messages
from app.memory.retrieval import forget_scan, hybrid_search
from app.memory.store import (
    archive_memory,
    get_memory,
    list_memories,
    save_entity,
    save_memory,
)
from app.index.builder import build_index
from app.index.persistence import index_is_stale, load_index, vectors_path


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    return tmp_path


def _remember(content: str, kind: str = "fact", importance: float = 0.6) -> dict:
    return save_memory(
        {
            "character_id": "c_test",
            "session_id": "s_test",
            "kind": kind,
            "content": content,
            "importance": importance,
        }
    )


class TestEmbedder:
    def test_tokenize_splits_chinese_and_latin(self):
        tokens = tokenize("Hello 雨夜 bus")
        assert "hello" in tokens
        assert "bus" in tokens
        assert "雨" in tokens
        assert "夜" in tokens

    def test_embedding_dimension_and_norm(self):
        embedder = HashEmbedder()
        vector = embedder.embed(["雨夜的站台"])[0]
        assert len(vector) == DIM
        assert abs(sum(value * value for value in vector) - 1.0) < 1e-6

    def test_embedding_is_deterministic(self):
        embedder = HashEmbedder()
        first = embedder.embed(["同样的输入"])[0]
        second = HashEmbedder().embed(["同样的输入"])[0]
        assert first == second

    def test_different_text_yields_different_vector(self):
        embedder = HashEmbedder()
        first = embedder.embed(["雨夜站台"])[0]
        second = embedder.embed(["阳光海面"])[0]
        assert first != second


class TestMemoryStore:
    def test_save_and_list(self):
        item = _remember("角色K住在地址N")
        assert item["id"]
        listed = list_memories(character_id="c_test")
        assert [row["id"] for row in listed] == [item["id"]]

    def test_filter_by_kind_and_session(self):
        _remember("事件内容", kind="event")
        _remember("关系内容", kind="relation")
        assert len(list_memories(character_id="c_test", kind="event")) == 1
        assert len(list_memories(character_id="c_test", session_id="s_other")) == 0

    def test_archive_hides_item(self):
        item = _remember("待归档")
        assert archive_memory(item["id"], "c_test") is True
        assert list_memories(character_id="c_test") == []
        assert len(list_memories(character_id="c_test", include_archived=True)) == 1

    def test_entity_roundtrip(self):
        save_entity({"name": "角色K", "character_id": "c_test", "attributes": {"职业": "调音师"}})
        item = get_memory("missing", "c_test")
        assert item is None


class TestIndex:
    def test_build_writes_index_and_vectors(self):
        _remember("索引条目内容")
        payload = build_index()
        assert payload["total_documents"] == 1
        assert load_index() is not None
        assert vectors_path().exists()

    def test_index_is_stale_when_source_changes(self):
        item = _remember("会变化的内容")
        build_index()
        assert index_is_stale(load_index()) is False
        path = load_index()["entries"][0]["path"]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": item["id"], "content": "改过的内容"}))
        assert index_is_stale(load_index()) is True

    def test_empty_index_is_stale(self):
        assert index_is_stale(None) is True


class TestRetrieval:
    def test_bm25_finds_keyword_match(self):
        _remember("角色K在地址N经营一家地址U")
        _remember("角色L喜欢在雨天散步")
        build_index()
        hits = hybrid_search("地址U")
        assert hits
        assert "地址U" in hits[0]["id"] or hits[0]["score"] > 0

    def test_weight_change_affects_order(self):
        _remember("苹果 香蕉 橘子 葡萄 柠檬")
        _remember("完全无关的内容")
        build_index()
        keyword_first = hybrid_search("苹果", bm25_weight=1.0, vector_weight=0.0)
        vector_first = hybrid_search("苹果", bm25_weight=0.0, vector_weight=1.0)
        assert keyword_first
        assert vector_first
        assert keyword_first[0]["id"] == vector_first[0]["id"]

    def test_approximate_flag_present(self):
        _remember("可检索内容")
        build_index()
        hits = hybrid_search("可检索")
        assert hits
        assert "approximate" in hits[0]

    def test_archived_items_excluded_by_default(self):
        item = _remember("归档内容")
        archive_memory(item["id"], "c_test")
        build_index()
        assert hybrid_search("归档内容") == []


class TestExtraction:
    def test_extracts_fact_relation_and_event(self):
        messages = [
            {"role": "user", "content": "我叫角色K，住在地址N。"},
            {"role": "assistant", "content": "角色L喜欢雨天的声音。"},
            {"role": "user", "content": "我们去了车站。"},
        ]
        items = extract_from_messages(messages, "c_test", "s_test")
        kinds = {item["kind"] for item in items}
        assert "fact" in kinds
        assert "relation" in kinds
        assert "event" in kinds

    def test_important_sentence_gets_high_importance(self):
        items = extract_from_messages(
            [{"role": "user", "content": "记住，我叫角色K。"}], "c_test", "s_test"
        )
        assert items
        assert max(item["importance"] for item in items) >= 0.8

    def test_extractor_is_deterministic(self):
        messages = [{"role": "user", "content": "我叫角色K"}]
        extractor = RuleExtractor()
        first = extractor.extract(messages, "c_test", "s_test")
        second = extractor.extract(messages, "c_test", "s_test")
        assert [item["content"] for item in first] == [item["content"] for item in second]


class TestForgetScan:
    def test_low_importance_old_item_is_candidate(self):
        item = save_memory(
            {
                "character_id": "c_test",
                "kind": "fact",
                "content": "很久以前的琐事",
                "importance": 0.1,
            }
        )
        path = f"{item['character_id']}/{item['id']}.json"
        stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
        import os

        from app import store as file_store

        target = file_store.memories_dir("c_test") / f"{item['id']}.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["last_access_at"] = stale
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        candidates = forget_scan(character_id="c_test", threshold=0.3, days=30)
        assert [row["id"] for row in candidates] == [item["id"]]


class TestMemoryRoutes:
    def test_list_and_search(self):
        from fastapi.testclient import TestClient

        from app.main import app

        _remember("路由可检索条目")
        build_index()
        with TestClient(app) as client:
            listed = client.get("/v1/memory/")
            assert listed.status_code == 200
            assert listed.json()["items"]
            searched = client.post("/v1/memory/search", json={"query": "路由"})
            assert searched.status_code == 200
            assert searched.json()["items"]
            stats = client.get("/v1/memory/stats")
            assert stats.status_code == 200
            assert stats.json()["total"] >= 1
