"""履歴管理のテスト。"""

from __future__ import annotations

import json
from datetime import date

from room import store


def write_history(tmp_path, records):
    path = tmp_path / "posted.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


class TestLoadHistory:
    def test_missing_file_is_empty(self, tmp_path):
        assert store.load_history(tmp_path / "nope.jsonl") == []

    def test_skips_broken_lines(self, tmp_path, capsys):
        path = tmp_path / "posted.jsonl"
        path.write_text('{"itemCode": "a"}\nこれはJSONではない\n', encoding="utf-8")
        records = store.load_history(path)
        assert len(records) == 1
        assert "スキップ" in capsys.readouterr().out


class TestPresentedItemCodes:
    def test_recent_items_are_excluded(self, tmp_path):
        records = [
            {"itemCode": "recent", "presentedAt": "2026-08-20"},
            {"itemCode": "old", "presentedAt": "2025-01-01"},
        ]
        codes = store.presented_item_codes(records, ttl_days=180, today=date(2026, 8, 27))
        assert codes == {"recent"}

    def test_unparseable_date_is_kept(self, tmp_path):
        # 日付が壊れている履歴は「まだ新しい」側に倒して重複提示を防ぐ
        records = [{"itemCode": "x", "presentedAt": "???"}]
        codes = store.presented_item_codes(records, ttl_days=180, today=date(2026, 8, 27))
        assert codes == {"x"}


class TestRecentShopCounts:
    def test_counts_only_within_window(self):
        records = [
            {"shopCode": "a", "presentedAt": "2026-08-26"},
            {"shopCode": "a", "presentedAt": "2026-08-25"},
            {"shopCode": "a", "presentedAt": "2026-01-01"},
            {"shopCode": "b", "presentedAt": "2026-08-27"},
        ]
        counts = store.recent_shop_counts(records, days=30, today=date(2026, 8, 27))
        assert counts["a"] == 2
        assert counts["b"] == 1


class TestAppendPresented:
    def test_writes_expected_fields(self, tmp_path):
        path = tmp_path / "posted.jsonl"
        items = [
            {
                "itemCode": "shop:1",
                "itemName": "商品",
                "shopCode": "shop",
                "shopName": "ショップ",
                "genreId": "100804",
                "itemPrice": 1980,
                "reviewCount": 120,
                "reviewAverage": 4.4,
                "_genre_key": "daily_goods",
                "_rank": 5,
                "_score": 3.14159,
            }
        ]
        assert store.append_presented(items, path) == 1

        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["itemCode"] == "shop:1"
        assert record["price"] == 1980
        assert record["genreKey"] == "daily_goods"
        assert record["reviewCount"] == 120
        assert record["rank"] == 5
        assert record["score"] == 3.1416
        assert record["presentedAt"] == date.today().isoformat()

    def test_appends_without_truncating(self, tmp_path):
        path = tmp_path / "posted.jsonl"
        store.append_presented([{"itemCode": "a"}], path)
        store.append_presented([{"itemCode": "b"}], path)
        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_roundtrip_blocks_duplicate_next_day(self, tmp_path):
        """提示 → 履歴読み込み → 除外集合、という往復が繋がっていること。"""
        path = tmp_path / "posted.jsonl"
        store.append_presented([{"itemCode": "shop:1", "_genre_key": "daily_goods"}], path)
        history = store.load_history(path)
        codes = store.presented_item_codes(history, ttl_days=180)
        assert "shop:1" in codes
