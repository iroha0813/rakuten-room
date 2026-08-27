"""候補のスコアリングと選定。

score = w_review_count * log10(reviewCount + 1)
      + w_review_avg   * (reviewAverage - 3.0)
      + w_rank         * ランキング掲載ボーナス
      + w_price_fit    * 価格帯適合度
      + w_commission   * log10(想定報酬 + 1)
      - w_shop_repeat  * 直近の同一ショップ出現回数
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

# ランキングボーナスが0になる順位。これより下位は「今売れている」根拠として弱い。
RANK_BONUS_FLOOR = 60.0


def price_fit(price: int, price_min: int, price_max: int) -> float:
    """設定価格帯の中央に近いほど1.0、外れるほど0に近づく。範囲外は0。"""
    if price_max <= price_min:
        return 0.0
    if not price_min <= price <= price_max:
        return 0.0
    center = (price_min + price_max) / 2
    half = (price_max - price_min) / 2
    return max(0.0, 1.0 - abs(price - center) / half)


def rank_bonus(rank: int | None) -> float:
    """ランキング掲載なら順位に応じたボーナス。非掲載は0。"""
    if not rank or rank < 1:
        return 0.0
    return max(0.0, (RANK_BONUS_FLOOR + 1 - rank) / RANK_BONUS_FLOOR)


def score_item(
    item: dict[str, Any],
    *,
    weights: dict[str, float],
    price_min: int,
    price_max: int,
    commission_rate: float,
    shop_counts: Counter[str],
) -> float:
    review_count = int(item.get("reviewCount") or 0)
    review_average = float(item.get("reviewAverage") or 0.0)
    price = int(item.get("itemPrice") or 0)
    shop_code = item.get("shopCode") or ""

    return (
        weights.get("review_count", 0.0) * math.log10(review_count + 1)
        + weights.get("review_avg", 0.0) * (review_average - 3.0)
        + weights.get("rank", 0.0) * rank_bonus(item.get("_rank"))
        + weights.get("price_fit", 0.0) * price_fit(price, price_min, price_max)
        + weights.get("commission", 0.0) * math.log10(price * commission_rate + 1)
        - weights.get("shop_repeat", 0.0) * shop_counts.get(shop_code, 0)
    )


def is_eligible(
    item: dict[str, Any],
    *,
    excluded_codes: set[str],
    min_review_average: float,
    min_review_count: int,
    price_min: int,
    price_max: int,
) -> bool:
    """投稿候補として出してよいか。除外理由は _reject に記録する。"""
    reasons: list[str] = []

    if item.get("itemCode") in excluded_codes:
        reasons.append("提示済み")
    if int(item.get("availability") or 0) != 1:
        reasons.append("在庫なし")
    if int(item.get("imageFlag") or 0) != 1:
        reasons.append("画像なし")
    if float(item.get("reviewAverage") or 0.0) < min_review_average:
        reasons.append("レビュー評価が基準未満")
    if int(item.get("reviewCount") or 0) < min_review_count:
        reasons.append("レビュー件数が基準未満")
    price = int(item.get("itemPrice") or 0)
    if not price_min <= price <= price_max:
        reasons.append("価格帯外")

    item["_reject"] = reasons
    return not reasons


def _allocate(daily_count: int, genres: dict[str, Any]) -> dict[str, int]:
    """share比率を daily_count に按分する。端数はshareの大きいジャンルへ。"""
    shares = {key: float(cfg.get("share", 0)) for key, cfg in genres.items()}
    total = sum(shares.values())
    if total <= 0:
        return {key: 0 for key in genres}

    quota = {key: int(daily_count * share / total) for key, share in shares.items()}
    remainder = daily_count - sum(quota.values())
    for key in sorted(shares, key=lambda k: shares[k], reverse=True):
        if remainder <= 0:
            break
        quota[key] += 1
        remainder -= 1
    return quota


def select(
    candidates: list[dict[str, Any]],
    *,
    settings: dict[str, Any],
    weights: dict[str, float],
    excluded_codes: set[str],
    shop_counts: Counter[str],
) -> list[dict[str, Any]]:
    """候補プールから当日分を選ぶ。

    candidates の各要素は collect.py が付与した _genre_key / _rank を持つ。
    """
    genres = settings["genres"]
    collect_cfg = settings.get("collect", {})
    selection_cfg = settings.get("selection", {})
    sale_boost = settings.get("sale_boost", {}) or {}
    commission_rate = float(settings.get("affiliate", {}).get("commission_rate", 0.02))
    max_per_shop = int(selection_cfg.get("max_per_shop", 1))
    daily_count = int(settings.get("daily_count", 5))

    # ジャンルごとに適格判定とスコアリング
    by_genre: dict[str, list[dict[str, Any]]] = {key: [] for key in genres}
    seen_codes: set[str] = set()
    for item in candidates:
        genre_key = item.get("_genre_key")
        genre_cfg = genres.get(genre_key)
        if genre_cfg is None:
            continue
        code = item.get("itemCode")
        if code in seen_codes:
            continue  # 同一商品がランキングと検索の両方から来ることがある
        seen_codes.add(code)

        price_min = int(genre_cfg["price_min"])
        price_max = int(genre_cfg["price_max"])
        if not is_eligible(
            item,
            excluded_codes=excluded_codes,
            min_review_average=float(collect_cfg.get("min_review_average", 0.0)),
            min_review_count=int(collect_cfg.get("min_review_count", 0)),
            price_min=price_min,
            price_max=price_max,
        ):
            continue

        item["_score"] = score_item(
            item,
            weights=weights,
            price_min=price_min,
            price_max=price_max,
            commission_rate=commission_rate,
            shop_counts=shop_counts,
        )
        if sale_boost.get("enabled") and sale_boost.get("genre_key") == genre_key:
            item["_score"] *= float(sale_boost.get("multiplier", 1.0))
        by_genre[genre_key].append(item)

    for items in by_genre.values():
        items.sort(key=lambda i: i["_score"], reverse=True)

    quota = _allocate(daily_count, genres)
    chosen: list[dict[str, Any]] = []
    used_shops: Counter[str] = Counter()

    def take(pool: list[dict[str, Any]], limit: int) -> None:
        for item in pool:
            if limit <= 0:
                return
            if item in chosen:
                continue
            shop = item.get("shopCode") or ""
            if used_shops[shop] >= max_per_shop:
                continue
            chosen.append(item)
            used_shops[shop] += 1
            limit -= 1

    for genre_key, limit in quota.items():
        take(by_genre[genre_key], limit)

    # ジャンル配分で埋まりきらなかった分は全体の上位から補充する
    if len(chosen) < daily_count:
        leftovers = sorted(
            (i for items in by_genre.values() for i in items),
            key=lambda i: i["_score"],
            reverse=True,
        )
        take(leftovers, daily_count - len(chosen))

    chosen.sort(key=lambda i: i["_score"], reverse=True)
    return chosen
