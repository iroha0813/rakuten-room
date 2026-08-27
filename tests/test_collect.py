"""収集の分岐（ジャンルID経路とキーワード経路）のテスト。ネットワークは叩かない。"""

from __future__ import annotations

from typing import Any

import pytest

from room import collect

SETTINGS: dict[str, Any] = {
    "daily_count": 5,
    "candidate_pool_multiplier": 5,
    "collect": {
        "ranking_hits": 30,
        "search_hits": 30,
        "search_max_pages": 2,
        "search_sort": "-reviewCount",
        "min_review_average": 4.0,
        "min_review_count": 20,
    },
    "genres": {
        "gadget": {
            "label": "ガジェット",
            "share": 2,
            "price_min": 2000,
            "price_max": 30000,
        },
        "food": {
            "label": "食品",
            "share": 1,
            "price_min": 2000,
            "price_max": 15000,
            "keywords": ["ふるさと納税"],
        },
    },
}

GENRE_IDS = {
    "gadget": {"genre_ids": [{"id": "100026", "name": "パソコン・周辺機器"}]},
    "food": {"genre_ids": [{"id": "100227", "name": "食品"}]},
}


class FakeClient:
    """呼び出しを記録するだけのスタブ。"""

    def __init__(self):
        self.ranking_calls: list[Any] = []
        self.search_calls: list[dict[str, Any]] = []

    def ranking_items(self, *, genre_id=None, page=1, period=None):
        self.ranking_calls.append(genre_id)
        return [{"itemCode": f"rank:{genre_id}:{i}", "rank": i + 1} for i in range(3)]

    def search_items(self, **kwargs):
        self.search_calls.append(kwargs)
        tag = kwargs.get("genre_id") or kwargs.get("keyword")
        page = kwargs.get("page", 1)
        return [{"itemCode": f"search:{tag}:{page}:{i}"} for i in range(3)]


class TestCollectKeyword:
    def test_searches_by_keyword_not_genre(self):
        client = FakeClient()
        items = collect.collect_keyword(
            client,
            genre_key="food",
            keyword="ふるさと納税",
            genre_cfg=SETTINGS["genres"]["food"],
            collect_cfg=SETTINGS["collect"],
            target=5,
        )
        assert client.ranking_calls == []  # ランキングAPIはキーワードを受け付けない
        assert all(call["keyword"] == "ふるさと納税" for call in client.search_calls)
        assert all(call.get("genre_id") is None for call in client.search_calls)
        assert all(item["_genre_key"] == "food" for item in items)
        assert all(item["_rank"] is None for item in items)

    def test_applies_price_and_review_filters(self):
        client = FakeClient()
        collect.collect_keyword(
            client,
            genre_key="food",
            keyword="ふるさと納税",
            genre_cfg=SETTINGS["genres"]["food"],
            collect_cfg=SETTINGS["collect"],
            target=5,
        )
        call = client.search_calls[0]
        assert call["min_price"] == 2000
        assert call["max_price"] == 15000
        assert call["min_review_average"] == 4.0
        assert call["min_review_count"] == 20

    def test_api_failure_returns_what_was_collected(self, capsys):
        class Failing(FakeClient):
            def search_items(self, **kwargs):
                if kwargs.get("page", 1) == 1:
                    return super().search_items(**kwargs)
                raise RuntimeError("boom")

        items = collect.collect_keyword(
            Failing(),
            genre_key="food",
            keyword="ふるさと納税",
            genre_cfg=SETTINGS["genres"]["food"],
            collect_cfg={**SETTINGS["collect"], "search_max_pages": 3},
            target=100,
        )
        assert len(items) == 3  # 1ページ目の分は残る
        assert "失敗しました" in capsys.readouterr().out


class TestCollectAll:
    def test_uses_both_genre_and_keyword_sources(self):
        client = FakeClient()
        pool = collect.collect_all(client, SETTINGS, GENRE_IDS)

        keywords_used = [c["keyword"] for c in client.search_calls if c.get("keyword")]
        genres_used = [c["genre_id"] for c in client.search_calls if c.get("genre_id")]
        assert "ふるさと納税" in keywords_used
        assert "100026" in genres_used and "100227" in genres_used
        assert {item["_genre_key"] for item in pool} == {"gadget", "food"}

    def test_keyword_only_genre_needs_no_genre_id(self, capsys):
        """ジャンルIDが無くてもキーワードがあれば収集できること。"""
        settings = {
            **SETTINGS,
            "genres": {"food": {**SETTINGS["genres"]["food"], "share": 1}},
        }
        client = FakeClient()
        pool = collect.collect_all(client, settings, {})
        assert pool
        assert "[warn]" not in capsys.readouterr().out

    def test_warns_when_no_source_at_all(self, capsys):
        settings = {
            **SETTINGS,
            "genres": {"gadget": {"label": "x", "share": 1, "price_min": 1, "price_max": 2}},
        }
        pool = collect.collect_all(FakeClient(), settings, {})
        assert pool == []
        assert "scripts/fetch_genres.py" in capsys.readouterr().out


class TestGenreTarget:
    def test_scales_with_share(self):
        big = collect._genre_target(5, 5, SETTINGS["genres"], "gadget")
        small = collect._genre_target(5, 5, SETTINGS["genres"], "food")
        assert big >= small

    def test_has_a_floor(self):
        genres = {"a": {"share": 0}, "b": {"share": 100}}
        assert collect._genre_target(5, 5, genres, "a") == 5
