"""提示済み商品の履歴管理。

SQLiteではなくJSONLを使う。GitHub Actions が毎日コミットするため、
バイナリDBだとgit差分が読めずコンフリクトも解決できないため。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import config, score

POSTED_PATH = config.DATA_DIR / "posted.jsonl"
CANDIDATES_DIR = config.DATA_DIR / "candidates"


def _parse_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


def load_history(path: Path | None = None) -> list[dict[str, Any]]:
    """posted.jsonl を全件読む。壊れた行は握りつぶさず警告して飛ばす。"""
    path = path or POSTED_PATH
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"[warn] {path.name}:{lineno} を JSON として読めませんでした。スキップします。")
    return records


def presented_item_codes(
    history: Iterable[dict[str, Any]], ttl_days: int, today: date | None = None
) -> set[str]:
    """重複判定に使う itemCode 集合。

    ttl_days より古い提示は対象外にする（時間が経てば再提示してよい）。
    """
    today = today or date.today()
    cutoff = today - timedelta(days=ttl_days)
    codes: set[str] = set()
    for record in history:
        presented = _parse_date(record.get("presentedAt", ""))
        if presented is not None and presented < cutoff:
            continue
        code = record.get("itemCode")
        if code:
            codes.add(code)
    return codes


def recent_shop_counts(
    history: Iterable[dict[str, Any]], days: int = 30, today: date | None = None
) -> Counter[str]:
    """直近 days 日で各ショップを何回出したか。同一ショップ偏重の減点に使う。"""
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    counts: Counter[str] = Counter()
    for record in history:
        presented = _parse_date(record.get("presentedAt", ""))
        if presented is None or presented < cutoff:
            continue
        shop = record.get("shopCode")
        if shop:
            counts[shop] += 1
    return counts


def recent_type_counts(
    history: Iterable[dict[str, Any]],
    categories: dict[str, list[str]],
    days: int,
    today: date | None = None,
) -> Counter[str]:
    """直近 days 日で各商品タイプ（product_type参照）が何回出たか。

    同じような商品（マットレスばかり等）が繰り返し出るのを防ぐ減点に使う。
    """
    counts: Counter[str] = Counter()
    if not categories:
        return counts
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    for record in history:
        presented = _parse_date(record.get("presentedAt", ""))
        if presented is None or presented < cutoff:
            continue
        item_type = score.categorize(record.get("itemName") or "", categories)
        if item_type:
            counts[item_type] += 1
    return counts


def append_presented(items: Iterable[dict[str, Any]], path: Path | None = None) -> int:
    """選定した商品を履歴に追記する。「提示した＝投稿したとみなす」割り切り。"""
    path = path or POSTED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    written = 0
    with path.open("a", encoding="utf-8") as fh:
        for item in items:
            record = {
                "itemCode": item.get("itemCode"),
                "itemName": item.get("itemName"),
                "shopCode": item.get("shopCode"),
                "shopName": item.get("shopName"),
                "genreKey": item.get("_genre_key"),
                "genreId": item.get("genreId"),
                "price": item.get("itemPrice"),
                # レビュー指標も残す。report.py が「何が売れたか」を分析するのに必要。
                "reviewCount": item.get("reviewCount"),
                "reviewAverage": item.get("reviewAverage"),
                "rank": item.get("_rank"),
                # 切り口と型。翌日以降、直近で使ったものを後回しにするために見る。
                "angle": item.get("_angle"),
                "format": item.get("_format"),
                "score": round(float(item.get("_score", 0.0)), 4),
                "presentedAt": today,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    return written


def save_candidates(items: list[dict[str, Any]], day: date | None = None) -> Path:
    """その日の候補スナップショット。後から選定の妥当性を検証できるように残す。"""
    day = day or date.today()
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDIDATES_DIR / f"{day.isoformat()}.json"
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
