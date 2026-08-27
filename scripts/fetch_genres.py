"""ジャンルIDを楽天のジャンル検索APIから引き当てて config/genres.yaml に書き出す。

ジャンルIDはハードコードしない。楽天側で変わる可能性があるうえ、
記憶や推測で書いた数字は静かに間違うため。

    python scripts/fetch_genres.py            # 引き当てて書き出す
    python scripts/fetch_genres.py --list     # ルートジャンル一覧を表示するだけ

settings.yaml の genres.<key>.match に書いたキーワードで、楽天市場の
ルートジャンル名を部分一致検索する。ヒットしない・意図と違う場合は
--list で一覧を見て match を直すか、genres.yaml を手で編集する。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from room import config  # noqa: E402
from room.rakuten import RakutenClient  # noqa: E402


def fetch_root_genres(client: RakutenClient) -> list[dict[str, Any]]:
    """楽天市場のルート直下ジャンル一覧。"""
    payload = client.search_genre(0)
    children = payload.get("children") or []
    normalized: list[dict[str, Any]] = []
    for child in children:
        # formatVersion=2 では平坦、1 では {"child": {...}} で包まれる
        node = child.get("child", child) if isinstance(child, dict) else {}
        genre_id = node.get("genreId")
        name = node.get("genreName") or node.get("nameJa")
        if genre_id and name:
            normalized.append({"id": str(genre_id), "name": str(name)})
    return normalized


def match_genres(
    root_genres: list[dict[str, Any]], settings: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """settings の match キーワードでルートジャンルを引き当てる。"""
    resolved: dict[str, Any] = {}
    warnings: list[str] = []

    for key, cfg in settings["genres"].items():
        keywords = cfg.get("match") or []
        if not keywords:
            warnings.append(f"{key}: settings.yaml に match キーワードがありません。")
            continue

        hits: list[dict[str, Any]] = []
        for keyword in keywords:
            found = [g for g in root_genres if keyword in g["name"]]
            if not found:
                warnings.append(f"{key}: キーワード「{keyword}」に一致するジャンルがありません。")
            for genre in found:
                if genre not in hits:
                    hits.append(genre)

        if hits:
            resolved[key] = {"label": cfg.get("label", key), "genre_ids": hits}
        else:
            warnings.append(f"{key}: ジャンルIDを1つも引き当てられませんでした。")

    return resolved, warnings


@config.cli
def main() -> int:
    parser = argparse.ArgumentParser(description="ジャンルIDの引き当て")
    parser.add_argument(
        "--list", action="store_true", help="ルートジャンル一覧を表示するだけ（書き出さない）"
    )
    args = parser.parse_args()

    settings = config.load_settings()
    client = RakutenClient.from_env(settings)

    root_genres = fetch_root_genres(client)
    if not root_genres:
        print("ルートジャンルを取得できませんでした。applicationId / accessKey を確認してください。")
        return 1

    if args.list:
        print(f"楽天市場のルートジャンル {len(root_genres)} 件:\n")
        for genre in root_genres:
            print(f"  {genre['id']:>8}  {genre['name']}")
        return 0

    resolved, warnings = match_genres(root_genres, settings)

    for warning in warnings:
        print(f"[warn] {warning}")

    if not resolved:
        print("\n引き当てに失敗しました。`--list` で一覧を見て settings.yaml の match を直してください。")
        return 1

    out_path = config.CONFIG_DIR / "genres.yaml"
    header = (
        "# scripts/fetch_genres.py が生成。手で編集してもよい。\n"
        "# genre_ids は楽天市場のルートジャンル。1キーに複数のジャンルを割り当てられる。\n"
    )
    out_path.write_text(
        header + yaml.safe_dump(resolved, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    print(f"\n書き出しました: {out_path}\n")
    for key, entry in resolved.items():
        names = " / ".join(f"{g['name']}({g['id']})" for g in entry["genre_ids"])
        print(f"  {key} [{entry['label']}]: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
