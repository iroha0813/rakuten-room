"""成果CSV取り込みのテスト。列マッピングが壊れたときに黙って通らないことを確認する。"""

from __future__ import annotations

import pytest

from room import report

SETTINGS = {
    "report": {
        "encoding": "utf-8",
        "columns": {
            "date": "発生日",
            "item_name": "商品名",
            "item_code": "商品ID",
            "price": "商品価格",
            "reward": "報酬額",
            "count": "件数",
        },
    },
    "genres": {"daily_goods": {"label": "日用品"}},
}

GOOD_CSV = """発生日,商品名,商品ID,商品価格,報酬額,件数
2026-08-01,テスト商品A,shop:1,"3,980円","79円",1
2026-08-02,テスト商品B,shop:2,"12,800円","256円",2
"""


class TestToNumber:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1,280円", 1280.0), ("¥980", 980.0), ("", 0.0), (None, 0.0), ("-", 0.0), ("12.5", 12.5)],
    )
    def test_parses_japanese_currency(self, raw, expected):
        assert report._to_number(raw) == expected


class TestLoadConversions:
    def test_reads_rows(self, tmp_path):
        path = tmp_path / "r.csv"
        path.write_text(GOOD_CSV, encoding="utf-8")
        rows = report.load_conversions(path, SETTINGS)
        assert len(rows) == 2
        assert rows[0]["itemCode"] == "shop:1"
        assert rows[0]["reward"] == 79.0
        assert rows[1]["count"] == 2

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            report.load_conversions(tmp_path / "nope.csv", SETTINGS)

    def test_unexpected_columns_fail_loudly(self, tmp_path):
        """列名が変わったら黙って0件にせず、実際の列名を添えて落ちること。"""
        path = tmp_path / "r.csv"
        path.write_text("date,name,reward\n2026-08-01,A,79\n", encoding="utf-8")
        with pytest.raises(report.ReportFormatError) as excinfo:
            report.load_conversions(path, SETTINGS)
        assert "商品名" in str(excinfo.value)
        assert "name" in str(excinfo.value)

    def test_wrong_encoding_gives_actionable_error(self, tmp_path):
        path = tmp_path / "r.csv"
        path.write_bytes(GOOD_CSV.encode("utf-8"))
        settings = {**SETTINGS, "report": {**SETTINGS["report"], "encoding": "ascii"}}
        with pytest.raises(report.ReportFormatError) as excinfo:
            report.load_conversions(path, settings)
        assert "report.encoding" in str(excinfo.value)


class TestMergeConversions:
    def test_does_not_double_count_same_rows(self, tmp_path):
        path = tmp_path / "conversions.jsonl"
        rows = [{"itemName": "A", "reward": 79.0}]
        assert report.merge_conversions(rows, path) == 1
        assert report.merge_conversions(rows, path) == 0
        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 1


class TestMatchToHistory:
    def test_matches_by_item_code(self):
        history = [{"itemCode": "shop:1", "itemName": "テスト商品A", "genreKey": "daily_goods"}]
        conversions = [{"itemCode": "shop:1", "itemName": "別名でも", "reward": 79.0, "count": 1}]
        matched, unmatched = report.match_to_history(conversions, history)
        assert len(matched) == 1
        assert not unmatched

    def test_falls_back_to_name(self):
        history = [{"itemCode": "shop:1", "itemName": "【送料無料】テスト商品A 2個セット"}]
        conversions = [{"itemCode": None, "itemName": "送料無料 テスト商品A 2個セット", "count": 1}]
        matched, _ = report.match_to_history(conversions, history)
        assert len(matched) == 1

    def test_reports_unmatched(self):
        history = [{"itemCode": "shop:1", "itemName": "テスト商品A"}]
        conversions = [{"itemCode": "other:9", "itemName": "無関係な商品", "count": 1}]
        matched, unmatched = report.match_to_history(conversions, history)
        assert not matched
        assert len(unmatched) == 1


class TestAnalyze:
    def test_aggregates_by_genre_and_band(self):
        history = [
            {"itemCode": "shop:1", "genreKey": "daily_goods", "price": 3980, "reviewCount": 120},
            {"itemCode": "shop:2", "genreKey": "daily_goods", "price": 1500, "reviewCount": 30},
        ]
        matched = [
            {
                "itemCode": "shop:1",
                "reward": 79.0,
                "count": 1,
                "history": history[0],
            }
        ]
        result = report.analyze(matched, history, SETTINGS)
        assert result["presented_total"] == 2
        assert result["conversion_total"] == 1
        assert result["reward_total"] == 79.0
        assert result["by_genre"]["daily_goods"]["presented"] == 2
        assert result["by_price_band"]["2,000〜5,000円"]["count"] == 1
        assert result["by_review_band"]["50〜200件"]["count"] == 1


class TestSuggestWeights:
    def test_declines_on_small_sample(self):
        history = [{"itemCode": f"s:{i}"} for i in range(4)]
        advice = report.suggest_weights([], history, {"review_count": 1.0})
        assert advice["available"] is False
        assert "サンプルが少なすぎる" in advice["reason"]

    def test_suggests_raising_effective_weight(self):
        converted = [
            {"itemCode": f"c:{i}", "reviewCount": 2000, "reviewAverage": 4.6, "price": 5000}
            for i in range(5)
        ]
        others = [
            {"itemCode": f"o:{i}", "reviewCount": 50, "reviewAverage": 4.2, "price": 5000}
            for i in range(5)
        ]
        history = converted + others
        matched = [{"history": r, "itemCode": r["itemCode"]} for r in converted]
        advice = report.suggest_weights(matched, history, {"review_count": 1.0, "commission": 1.0})
        assert advice["available"] is True
        assert advice["suggestions"]["review_count"]["direction"] == "上げる"
        # 価格に差がなければ据え置きになること
        assert advice["suggestions"]["commission"]["direction"] == "そのまま"
