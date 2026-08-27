"""成果CSVの取り込みと効果分析（半自動）。

楽天アフィリエイトの成果データに公式APIは存在しない。管理画面からの
CSVダウンロード（PCからのみ）が唯一の取得手段なので、そこだけ人力になる。

    python -m room.report import local/report_202608.csv

CSVの列名は楽天側の仕様変更で変わりうるため config/settings.yaml の
report.columns に外出ししてある。想定外の列構成なら黙って壊れず明示的に落ちる。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from . import config, store

CONVERSIONS_PATH = config.LOCAL_DIR / "conversions.jsonl"

PRICE_BANDS: list[tuple[str, int, int]] = [
    ("〜2,000円", 0, 2000),
    ("2,000〜5,000円", 2000, 5000),
    ("5,000〜10,000円", 5000, 10000),
    ("10,000〜30,000円", 10000, 30000),
    ("30,000円〜", 30000, 10**9),
]

REVIEW_BANDS: list[tuple[str, int, int]] = [
    ("〜50件", 0, 50),
    ("50〜200件", 50, 200),
    ("200〜1,000件", 200, 1000),
    ("1,000件〜", 1000, 10**9),
]

# 重み調整を提案する閾値。converted 側が非converted側より何倍あれば「効いている」とみなすか。
SUGGEST_RATIO_UP = 1.2
SUGGEST_RATIO_DOWN = 0.85


class ReportFormatError(RuntimeError):
    """成果CSVの列構成が設定と食い違っている。"""


def _to_number(value: Any) -> float:
    """「1,280円」「¥1,280」などを数値にする。読めなければ0。"""
    if value is None:
        return 0.0
    text = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(text) if text not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def _normalize_name(name: str) -> str:
    """商品名の突合用の正規化。記号と空白を落とす。"""
    return re.sub(r"[\s　【】\[\]（）()・/,、。!！?？]+", "", str(name or "")).lower()


def load_conversions(csv_path: Path, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """成果CSVを読む。列が足りなければ何が見つかったかを添えて落とす。"""
    report_cfg = settings.get("report", {})
    columns: dict[str, str] = report_cfg.get("columns", {})
    encoding = str(report_cfg.get("encoding", "cp932"))

    if not csv_path.exists():
        raise FileNotFoundError(f"CSVが見つかりません: {csv_path}")

    try:
        text = csv_path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise ReportFormatError(
            f"CSVを {encoding} として読めませんでした ({exc})。\n"
            "config/settings.yaml の report.encoding を utf-8 や utf-8-sig に変えて試してください。"
        ) from exc

    reader = csv.DictReader(text.splitlines())
    headers = reader.fieldnames or []

    required = [columns.get(key) for key in ("item_name", "reward")]
    missing = [col for col in required if col and col not in headers]
    if missing or not headers:
        raise ReportFormatError(
            "成果CSVの列構成が設定と一致しません。\n"
            f"  設定が期待する列: {[c for c in columns.values() if c]}\n"
            f"  CSVに実在する列: {headers}\n"
            "config/settings.yaml の report.columns を実際の列名に合わせてください。"
        )

    rows: list[dict[str, Any]] = []
    for raw in reader:
        item_name = raw.get(columns.get("item_name", ""), "")
        if not item_name:
            continue
        rows.append(
            {
                "date": raw.get(columns.get("date", ""), ""),
                "itemName": item_name,
                "itemCode": raw.get(columns.get("item_code", ""), "") or None,
                "price": _to_number(raw.get(columns.get("price", ""), 0)),
                "reward": _to_number(raw.get(columns.get("reward", ""), 0)),
                "count": int(_to_number(raw.get(columns.get("count", ""), 1)) or 1),
            }
        )
    return rows


def merge_conversions(rows: Iterable[dict[str, Any]], path: Path | None = None) -> int:
    """成果を local/conversions.jsonl に蓄積する。同一行の二重取り込みを避ける。"""
    path = path or CONVERSIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: set[str] = set()
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    existing.add(line)

    added = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if encoded in existing:
                continue
            fh.write(encoded + "\n")
            existing.add(encoded)
            added += 1
    return added


def load_all_conversions(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or CONVERSIONS_PATH
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def match_to_history(
    conversions: list[dict[str, Any]], history: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """成果行を提示履歴に紐づける。

    itemCode の完全一致を優先し、無ければ正規化した商品名の前方一致で拾う。
    紐づかなかった成果（ROOM以外の経路や、システム導入前の投稿）も返す。
    """
    by_code = {r["itemCode"]: r for r in history if r.get("itemCode")}
    by_name = [(_normalize_name(r.get("itemName", "")), r) for r in history]

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for conv in conversions:
        record = by_code.get(conv.get("itemCode")) if conv.get("itemCode") else None
        if record is None:
            key = _normalize_name(conv.get("itemName", ""))[:24]
            if key:
                for norm, candidate in by_name:
                    if norm.startswith(key) or key in norm:
                        record = candidate
                        break
        if record is None:
            unmatched.append(conv)
        else:
            matched.append({**conv, "history": record})

    return matched, unmatched


def _band(value: float, bands: list[tuple[str, int, int]]) -> str:
    for label, low, high in bands:
        if low <= value < high:
            return label
    return bands[-1][0]


def analyze(
    matched: list[dict[str, Any]],
    history: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    genres = settings.get("genres", {})

    presented_by_genre: dict[str, int] = defaultdict(int)
    for record in history:
        presented_by_genre[record.get("genreKey") or "unknown"] += 1

    genre_stats: dict[str, dict[str, Any]] = {}
    for conv in matched:
        key = conv["history"].get("genreKey") or "unknown"
        stat = genre_stats.setdefault(key, {"count": 0, "reward": 0.0})
        stat["count"] += conv.get("count", 1)
        stat["reward"] += conv.get("reward", 0.0)
    for key, stat in genre_stats.items():
        presented = presented_by_genre.get(key, 0)
        stat["label"] = genres.get(key, {}).get("label", key)
        stat["presented"] = presented
        stat["conversion_per_post"] = round(stat["count"] / presented, 4) if presented else None

    price_stats: dict[str, dict[str, Any]] = {}
    for conv in matched:
        band = _band(float(conv["history"].get("price") or 0), PRICE_BANDS)
        stat = price_stats.setdefault(band, {"count": 0, "reward": 0.0})
        stat["count"] += conv.get("count", 1)
        stat["reward"] += conv.get("reward", 0.0)

    review_stats: dict[str, dict[str, Any]] = {}
    for conv in matched:
        band = _band(float(conv["history"].get("reviewCount") or 0), REVIEW_BANDS)
        stat = review_stats.setdefault(band, {"count": 0, "reward": 0.0})
        stat["count"] += conv.get("count", 1)
        stat["reward"] += conv.get("reward", 0.0)

    return {
        "generated_at": date.today().isoformat(),
        "presented_total": len(history),
        "conversion_total": sum(c.get("count", 1) for c in matched),
        "reward_total": round(sum(c.get("reward", 0.0) for c in matched), 2),
        "by_genre": genre_stats,
        "by_price_band": price_stats,
        "by_review_band": review_stats,
    }


def suggest_weights(
    matched: list[dict[str, Any]],
    history: list[dict[str, Any]],
    weights: dict[str, float],
) -> dict[str, Any]:
    """成約した商品と成約しなかった商品の特徴を比べ、重みの調整方向を出す。

    件数が少ないうちは統計的に意味がない。最低件数に満たなければ提案しない。
    """
    converted_codes = {c["history"].get("itemCode") for c in matched}
    converted = [r for r in history if r.get("itemCode") in converted_codes]
    others = [r for r in history if r.get("itemCode") not in converted_codes]

    if len(converted) < 5 or len(others) < 5:
        return {
            "available": False,
            "reason": (
                f"成約 {len(converted)} 件 / 非成約 {len(others)} 件。"
                "サンプルが少なすぎるため重み調整は提案しません（各5件以上で有効になります）。"
            ),
        }

    features = {
        "review_count": "reviewCount",
        "review_avg": "reviewAverage",
        "commission": "price",
    }

    suggestions: dict[str, Any] = {}
    for weight_key, field in features.items():
        conv_values = [float(r.get(field) or 0) for r in converted]
        other_values = [float(r.get(field) or 0) for r in others]
        conv_mean = statistics.mean(conv_values) if conv_values else 0.0
        other_mean = statistics.mean(other_values) if other_values else 0.0
        ratio = conv_mean / other_mean if other_mean else 1.0

        if ratio >= SUGGEST_RATIO_UP:
            direction = "上げる"
            proposed = round(weights.get(weight_key, 1.0) * 1.2, 3)
        elif ratio <= SUGGEST_RATIO_DOWN:
            direction = "下げる"
            proposed = round(weights.get(weight_key, 1.0) * 0.8, 3)
        else:
            direction = "そのまま"
            proposed = weights.get(weight_key, 1.0)

        suggestions[weight_key] = {
            "converted_mean": round(conv_mean, 2),
            "other_mean": round(other_mean, 2),
            "ratio": round(ratio, 3),
            "direction": direction,
            "current": weights.get(weight_key, 1.0),
            "proposed": proposed,
        }

    return {"available": True, "suggestions": suggestions}


def _print_table(title: str, stats: dict[str, dict[str, Any]]) -> None:
    print(f"\n■ {title}")
    if not stats:
        print("  （該当なし）")
        return
    for key, stat in sorted(stats.items(), key=lambda kv: kv[1]["reward"], reverse=True):
        label = stat.get("label", key)
        line = f"  {label}: {stat['count']}件 / {stat['reward']:,.0f}円"
        if stat.get("conversion_per_post") is not None:
            line += f" (提示 {stat['presented']}件・成約率 {stat['conversion_per_post']:.1%})"
        print(line)


def cmd_import(args: argparse.Namespace) -> int:
    settings = config.load_settings()
    weights = config.load_weights()

    rows = load_conversions(Path(args.csv), settings)
    added = merge_conversions(rows)
    print(f"CSV {len(rows)} 行を読み込み、{added} 行を新規に取り込みました。")

    conversions = load_all_conversions()
    history = store.load_history()
    if not history:
        print("\n提示履歴 (data/posted.jsonl) が空です。分析はできません。")
        return 0

    matched, unmatched = match_to_history(conversions, history)
    result = analyze(matched, history, settings)

    print(f"\n提示済み商品: {result['presented_total']} 件")
    print(f"紐づいた成果: {result['conversion_total']} 件 / {result['reward_total']:,.0f}円")
    if unmatched:
        print(
            f"紐づかなかった成果: {len(unmatched)} 件"
            "（システム導入前の投稿やROOM以外の経路の可能性）"
        )

    _print_table("ジャンル別", result["by_genre"])
    _print_table("価格帯別", result["by_price_band"])
    _print_table("レビュー件数帯別", result["by_review_band"])

    advice = suggest_weights(matched, history, weights)
    print("\n■ スコア重みの調整提案")
    if not advice["available"]:
        print(f"  {advice['reason']}")
    else:
        for key, s in advice["suggestions"].items():
            print(
                f"  {key}: 成約平均 {s['converted_mean']} / 非成約平均 {s['other_mean']} "
                f"(比 {s['ratio']}) → {s['direction']}"
            )
            if s["direction"] != "そのまま":
                print(f"      config/weights.yaml: {key}: {s['current']} → {s['proposed']}")

    out_path = config.LOCAL_DIR / f"analysis_{date.today().isoformat()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({**result, "weight_advice": advice}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n分析結果を書き出しました: {out_path}")
    return 0


@config.cli
def main() -> int:
    parser = argparse.ArgumentParser(description="成果CSVの取り込みと効果分析")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="楽天アフィリエイトの成果CSVを取り込む")
    p_import.add_argument("csv", help="成果CSVのパス（例: local/report_202608.csv）")
    p_import.set_defaults(func=cmd_import)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
