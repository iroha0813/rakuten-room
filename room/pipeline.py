"""1日分の投稿候補を作るパイプライン。

    収集 → 選定 → プレゼン文生成 → ページ生成 → 履歴更新

GitHub Actions が毎朝これを走らせ、docs/ と data/ をコミットする。
"""

from __future__ import annotations

import argparse
from datetime import date

from . import collect, config, render, score, store, write
from .rakuten import RakutenClient


def run(
    *,
    use_llm: bool = True,
    update_history: bool = True,
    day: date | None = None,
) -> int:
    day = day or date.today()
    settings = config.load_settings()
    weights = config.load_weights()
    genre_ids = config.load_genres()

    client = RakutenClient.from_env(settings)

    print("== 収集 ==")
    pool = collect.collect_all(client, settings, genre_ids)
    if not pool:
        print("候補を1件も取得できませんでした。API資格情報とジャンル設定を確認してください。")
        return 1
    print(f"候補プール: {len(pool)} 件")

    print("\n== 選定 ==")
    history = store.load_history()
    ttl = int(settings.get("selection", {}).get("history_ttl_days", 180))
    chosen = score.select(
        pool,
        settings=settings,
        weights=weights,
        excluded_codes=store.presented_item_codes(history, ttl, today=day),
        shop_counts=store.recent_shop_counts(history, today=day),
    )
    print(f"選定: {len(chosen)} / {settings.get('daily_count')} 件")
    for item in chosen:
        print(f"  [{item['_score']:.2f}] {item.get('itemName', '')[:50]}")

    if not chosen:
        print(
            "\n条件を満たす候補がありませんでした。"
            "config/settings.yaml のレビュー基準や価格帯を緩めてください。"
        )

    print("\n== プレゼン文生成 ==")
    if use_llm and chosen:
        write.generate(chosen, settings)
        generated = sum(1 for item in chosen if item.get("_pitch"))
        print(f"生成: {generated} / {len(chosen)} 件")
    else:
        for item in chosen:
            item["_pitch"] = None
        print("LLMをスキップしました（--no-llm）。")

    print("\n== ページ生成 ==")
    snapshot = store.save_candidates(chosen, day=day)
    output = render.render(chosen, settings, day=day)
    print(f"候補スナップショット: {snapshot}")
    print(f"候補ページ: {output}")

    if update_history and chosen:
        written = store.append_presented(chosen)
        print(f"\n履歴に {written} 件を追記しました（明日以降は重複提示されません）。")
    elif not update_history:
        print("\n履歴は更新していません（--no-history）。")

    return 0


@config.cli
def main() -> int:
    parser = argparse.ArgumentParser(description="1日分の投稿候補を生成する")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Claude APIを呼ばずに選定とページ生成だけ行う（動作確認用）",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="posted.jsonl に追記しない（お試し実行用）",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="ローカルでのお試し実行。--no-llm --no-history と同じ",
    )
    args = parser.parse_args()

    return run(
        use_llm=not (args.no_llm or args.local),
        update_history=not (args.no_history or args.local),
    )


if __name__ == "__main__":
    raise SystemExit(main())
