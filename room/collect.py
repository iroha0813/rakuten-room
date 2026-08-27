"""候補商品の収集。

ジャンルごとに2系統から集める:
  1. ランキングAPI — 今売れているもの
  2. 商品検索API   — レビューが多く評価が固まっているもの
"""

from __future__ import annotations

import argparse
import math
from typing import Any

from . import config, score, store
from .rakuten import RakutenClient


def _genre_target(daily_count: int, multiplier: int, genres: dict[str, Any], key: str) -> int:
    """このジャンルで何件の候補を集めれば十分かの目安。"""
    total_share = sum(float(g.get("share", 0)) for g in genres.values()) or 1.0
    share = float(genres[key].get("share", 0))
    return max(5, math.ceil(daily_count * multiplier * share / total_share))


def collect_genre(
    client: RakutenClient,
    *,
    genre_key: str,
    genre_id: str | int,
    genre_cfg: dict[str, Any],
    collect_cfg: dict[str, Any],
    target: int,
) -> list[dict[str, Any]]:
    """1ジャンル分の候補を集める。API失敗時は空ではなく取れた分を返す。"""
    items: list[dict[str, Any]] = []

    def tag(raw: list[dict[str, Any]]) -> None:
        for entry in raw:
            entry["_genre_key"] = genre_key
            entry["_rank"] = entry.get("rank")
            items.append(entry)

    try:
        tag(client.ranking_items(genre_id=genre_id)[: int(collect_cfg.get("ranking_hits", 30))])
    except Exception as exc:  # noqa: BLE001 - 片方が落ちても他方で続行する
        print(f"[warn] {genre_key}: ランキング取得に失敗しました ({exc})")

    max_pages = int(collect_cfg.get("search_max_pages", 3))
    for page in range(1, max_pages + 1):
        if len(items) >= target * 2:
            break
        try:
            tag(
                client.search_items(
                    genre_id=genre_id,
                    hits=int(collect_cfg.get("search_hits", 30)),
                    page=page,
                    sort=str(collect_cfg.get("search_sort", "standard")),
                    min_price=int(genre_cfg["price_min"]),
                    max_price=int(genre_cfg["price_max"]),
                    min_review_average=float(collect_cfg.get("min_review_average", 0.0)),
                    min_review_count=int(collect_cfg.get("min_review_count", 0)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {genre_key}: 商品検索 page={page} に失敗しました ({exc})")
            break

    return items


def collect_all(
    client: RakutenClient,
    settings: dict[str, Any],
    genre_ids: dict[str, Any],
) -> list[dict[str, Any]]:
    genres = settings["genres"]
    collect_cfg = settings.get("collect", {})
    daily_count = int(settings.get("daily_count", 5))
    multiplier = int(settings.get("candidate_pool_multiplier", 5))

    pool: list[dict[str, Any]] = []
    for genre_key, genre_cfg in genres.items():
        entry = genre_ids.get(genre_key) or {}
        rakuten_genres = entry.get("genre_ids") or []
        if not rakuten_genres:
            print(
                f"[warn] {genre_key} のジャンルIDが config/genres.yaml にありません。"
                " scripts/fetch_genres.py を実行してください。"
            )
            continue

        # 1つのキーに複数の楽天ジャンルが割り当たるので、目標件数は分割する
        target = max(
            5, _genre_target(daily_count, multiplier, genres, genre_key) // len(rakuten_genres)
        )
        found: list[dict[str, Any]] = []
        for rakuten_genre in rakuten_genres:
            found.extend(
                collect_genre(
                    client,
                    genre_key=genre_key,
                    genre_id=rakuten_genre["id"],
                    genre_cfg=genre_cfg,
                    collect_cfg=collect_cfg,
                    target=target,
                )
            )
        print(f"[info] {genre_key} ({genre_cfg['label']}): {len(found)} 件取得")
        pool.extend(found)
    return pool


@config.cli
def main() -> int:
    parser = argparse.ArgumentParser(description="候補商品の収集")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="収集して件数と除外理由の内訳を表示するだけ（履歴は更新しない）",
    )
    args = parser.parse_args()

    settings = config.load_settings()
    genre_ids = config.load_genres()
    client = RakutenClient.from_env(settings)
    pool = collect_all(client, settings, genre_ids)
    print(f"\n候補プール合計: {len(pool)} 件")

    if args.dry_run:
        history = store.load_history()
        ttl = int(settings.get("selection", {}).get("history_ttl_days", 180))
        excluded = store.presented_item_codes(history, ttl)
        collect_cfg = settings.get("collect", {})
        reasons: dict[str, int] = {}
        eligible = 0
        for item in pool:
            genre_cfg = settings["genres"][item["_genre_key"]]
            ok = score.is_eligible(
                item,
                excluded_codes=excluded,
                min_review_average=float(collect_cfg.get("min_review_average", 0.0)),
                min_review_count=int(collect_cfg.get("min_review_count", 0)),
                price_min=int(genre_cfg["price_min"]),
                price_max=int(genre_cfg["price_max"]),
            )
            if ok:
                eligible += 1
            for reason in item.get("_reject", []):
                reasons[reason] = reasons.get(reason, 0) + 1

        print(f"適格: {eligible} 件")
        print("除外理由の内訳:")
        for reason, count in sorted(reasons.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {reason}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
