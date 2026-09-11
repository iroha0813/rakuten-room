"""スコアリングと選定のテスト。ネットワークは一切叩かない。"""

from __future__ import annotations

from collections import Counter

import pytest

from room import score


def make_item(**overrides):
    item = {
        "itemCode": "shop:item1",
        "itemName": "テスト商品",
        "itemPrice": 3000,
        "shopCode": "shop",
        "availability": 1,
        "imageFlag": 1,
        "reviewCount": 100,
        "reviewAverage": 4.5,
        "_genre_key": "daily_goods",
        "_rank": None,
    }
    item.update(overrides)
    return item


SETTINGS = {
    "daily_count": 4,
    "affiliate": {"commission_rate": 0.02},
    "collect": {"min_review_average": 4.0, "min_review_count": 20},
    "selection": {"max_per_shop": 1},
    "genres": {
        "daily_goods": {"label": "日用品", "share": 2, "price_min": 1000, "price_max": 8000},
        "gadget": {"label": "ガジェット", "share": 2, "price_min": 2000, "price_max": 30000},
    },
}

WEIGHTS = {
    "review_count": 1.0,
    "review_avg": 0.8,
    "rank": 1.2,
    "price_fit": 0.6,
    "commission": 0.9,
    "shop_repeat": 1.5,
    "type_repeat": 1.2,
    "repeat_bonus": 1.0,
}


class TestPriceFit:
    def test_center_is_best(self):
        assert score.price_fit(5000, 1000, 9000) == pytest.approx(1.0)

    def test_edges_are_zero(self):
        assert score.price_fit(1000, 1000, 9000) == pytest.approx(0.0)
        assert score.price_fit(9000, 1000, 9000) == pytest.approx(0.0)

    def test_out_of_range_is_zero(self):
        assert score.price_fit(500, 1000, 9000) == 0.0
        assert score.price_fit(99999, 1000, 9000) == 0.0

    def test_degenerate_range(self):
        assert score.price_fit(1000, 1000, 1000) == 0.0


class TestRankBonus:
    def test_top_rank_is_highest(self):
        assert score.rank_bonus(1) == pytest.approx(1.0)

    def test_missing_rank_is_zero(self):
        assert score.rank_bonus(None) == 0.0
        assert score.rank_bonus(0) == 0.0

    def test_bonus_decreases(self):
        assert score.rank_bonus(1) > score.rank_bonus(10) > score.rank_bonus(50)

    def test_beyond_floor_is_zero(self):
        assert score.rank_bonus(200) == 0.0


class TestCategorize:
    CATEGORIES = {
        "dish_rack": ["水切りラック", "水切り"],
        "rack": ["ラック"],
        "mattress": ["マットレス"],
    }

    def test_matches_keyword(self):
        assert score.categorize("高反発マットレス 三つ折り", self.CATEGORIES) == "mattress"

    def test_no_match_is_none(self):
        assert score.categorize("全く関係ない商品名", self.CATEGORIES) is None

    def test_first_matching_category_wins(self):
        # "水切りラック"は dish_rack にも rack にも部分一致するが、
        # categories の記載順（dish_rackが先）を優先する
        assert score.categorize("水切りラック シンク上", self.CATEGORIES) == "dish_rack"

    def test_empty_categories_is_none(self):
        assert score.categorize("マットレス", {}) is None


class TestEligibility:
    def _check(self, item, excluded=frozenset()):
        return score.is_eligible(
            item,
            excluded_codes=set(excluded),
            min_review_average=4.0,
            min_review_count=20,
            price_min=1000,
            price_max=8000,
        )

    def test_accepts_good_item(self):
        assert self._check(make_item()) is True

    def test_rejects_already_presented(self):
        item = make_item()
        assert self._check(item, excluded={"shop:item1"}) is False
        assert "提示済み" in item["_reject"]

    def test_rejects_out_of_stock(self):
        item = make_item(availability=0)
        assert self._check(item) is False
        assert "在庫なし" in item["_reject"]

    def test_rejects_no_image(self):
        item = make_item(imageFlag=0)
        assert self._check(item) is False
        assert "画像なし" in item["_reject"]

    def test_rejects_low_review_metrics(self):
        item = make_item(reviewAverage=3.2, reviewCount=3)
        assert self._check(item) is False
        assert "レビュー評価が基準未満" in item["_reject"]
        assert "レビュー件数が基準未満" in item["_reject"]

    def test_rejects_price_out_of_band(self):
        item = make_item(itemPrice=50000)
        assert self._check(item) is False
        assert "価格帯外" in item["_reject"]

    def test_handles_missing_fields(self):
        # 楽天APIが elements で絞られてフィールドを欠く場合でも例外を出さない
        assert self._check({"itemCode": "x"}) is False


class TestScoreItem:
    def test_more_reviews_scores_higher(self):
        low = score.score_item(
            make_item(reviewCount=30),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
        )
        high = score.score_item(
            make_item(reviewCount=3000),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
        )
        assert high > low

    def test_recent_shop_is_penalized(self):
        clean = score.score_item(
            make_item(),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
        )
        repeated = score.score_item(
            make_item(),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter({"shop": 3}),
        )
        assert repeated < clean

    def test_recent_type_is_penalized(self):
        clean = score.score_item(
            make_item(_product_type="mattress"),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
            type_counts=Counter(),
        )
        repeated = score.score_item(
            make_item(_product_type="mattress"),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
            type_counts=Counter({"mattress": 2}),
        )
        assert repeated < clean

    def test_repeat_type_is_boosted(self):
        clean = score.score_item(
            make_item(_repeat_type=None),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
        )
        repeat = score.score_item(
            make_item(_repeat_type="rice"),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
        )
        assert repeat > clean

    def test_untyped_item_is_not_penalized(self):
        # _product_type が None（分類対象外）なら type_counts があっても影響しない
        result = score.score_item(
            make_item(_product_type=None),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
            type_counts=Counter({"mattress": 99}),
        )
        baseline = score.score_item(
            make_item(_product_type=None),
            weights=WEIGHTS,
            price_min=1000,
            price_max=8000,
            commission_rate=0.02,
            shop_counts=Counter(),
            type_counts=Counter(),
        )
        assert result == baseline


class TestAllocate:
    def test_splits_by_share(self):
        quota = score._allocate(4, SETTINGS["genres"])
        assert sum(quota.values()) == 4
        assert quota == {"daily_goods": 2, "gadget": 2}

    def test_remainder_goes_to_bigger_share(self):
        genres = {
            "a": {"share": 3},
            "b": {"share": 1},
        }
        quota = score._allocate(5, genres)
        assert sum(quota.values()) == 5
        assert quota["a"] > quota["b"]

    def test_zero_share_is_safe(self):
        quota = score._allocate(5, {"a": {"share": 0}})
        assert quota == {"a": 0}


class TestSelect:
    def _pool(self):
        return [
            make_item(
                itemCode=f"shop{i}:item{i}",
                shopCode=f"shop{i}",
                reviewCount=100 * (i + 1),
                _genre_key="daily_goods" if i % 2 == 0 else "gadget",
                itemPrice=3000,
            )
            for i in range(8)
        ]

    def test_returns_daily_count(self):
        chosen = score.select(
            self._pool(),
            settings=SETTINGS,
            weights=WEIGHTS,
            excluded_codes=set(),
            shop_counts=Counter(),
        )
        assert len(chosen) == SETTINGS["daily_count"]

    def test_excludes_history(self):
        excluded = {f"shop{i}:item{i}" for i in range(6)}
        chosen = score.select(
            self._pool(),
            settings=SETTINGS,
            weights=WEIGHTS,
            excluded_codes=excluded,
            shop_counts=Counter(),
        )
        assert all(item["itemCode"] not in excluded for item in chosen)
        assert len(chosen) == 2  # 残り2件しか適格がない

    def test_respects_max_per_shop(self):
        pool = [
            make_item(itemCode=f"same:item{i}", shopCode="same", reviewCount=100 * (i + 1))
            for i in range(5)
        ]
        chosen = score.select(
            pool,
            settings=SETTINGS,
            weights=WEIGHTS,
            excluded_codes=set(),
            shop_counts=Counter(),
        )
        assert len(chosen) == 1

    def test_deduplicates_same_item_from_both_sources(self):
        # ランキングと商品検索の両方から同じ商品が来ることがある
        duplicated = [make_item(), make_item(_rank=3)]
        chosen = score.select(
            duplicated,
            settings=SETTINGS,
            weights=WEIGHTS,
            excluded_codes=set(),
            shop_counts=Counter(),
        )
        assert len(chosen) == 1

    def test_sorted_by_score_descending(self):
        chosen = score.select(
            self._pool(),
            settings=SETTINGS,
            weights=WEIGHTS,
            excluded_codes=set(),
            shop_counts=Counter(),
        )
        scores = [item["_score"] for item in chosen]
        assert scores == sorted(scores, reverse=True)

    def test_respects_max_per_type(self):
        pool = [
            make_item(
                itemCode=f"shop{i}:item{i}",
                shopCode=f"shop{i}",
                itemName="高反発マットレス 三つ折り",
                reviewCount=100 * (i + 1),
            )
            for i in range(5)
        ]
        settings = {
            **SETTINGS,
            "selection": {"max_per_shop": 1, "max_per_type": 1},
            "product_type": {"categories": {"mattress": ["マットレス"]}},
        }
        chosen = score.select(
            pool,
            settings=settings,
            weights=WEIGHTS,
            excluded_codes=set(),
            shop_counts=Counter(),
        )
        assert len(chosen) == 1

    def test_type_repeat_penalizes_across_days(self):
        # 過去に何度も出たタイプは、同じスコアなら後回しになる
        pool = [
            make_item(
                itemCode="a:1", shopCode="a", itemName="高反発マットレス", reviewCount=500
            ),
            make_item(
                itemCode="b:1", shopCode="b", itemName="よく眠れる枕", reviewCount=500
            ),
        ]
        settings = {
            **SETTINGS,
            "daily_count": 1,
            "selection": {"max_per_shop": 1, "max_per_type": 1},
            "product_type": {"categories": {"mattress": ["マットレス"], "pillow": ["枕"]}},
        }
        chosen = score.select(
            pool,
            settings=settings,
            weights=WEIGHTS,
            excluded_codes=set(),
            shop_counts=Counter(),
            type_counts=Counter({"mattress": 5}),
        )
        assert chosen[0]["itemCode"] == "b:1"

    def test_sale_boost_applies_to_target_genre(self):
        settings = {**SETTINGS, "sale_boost": {"enabled": True, "genre_key": "gadget", "multiplier": 5.0}}
        chosen = score.select(
            self._pool(),
            settings=settings,
            weights=WEIGHTS,
            excluded_codes=set(),
            shop_counts=Counter(),
        )
        assert chosen[0]["_genre_key"] == "gadget"
