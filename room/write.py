"""Claude API による商品プレゼン文の生成。

1商品1リクエスト。1日5件なら Batch API のオーバーヘッドの方が大きい。
将来20件超に増やすなら Batch API（50%オフ）に切り替える。
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from . import config, store

SYSTEM_PROMPT = """あなたは楽天ROOMで商品を紹介する日本語のライターです。
与えられた商品情報だけをもとに、ROOMのコメント欄に載せる紹介文を1本書いてください。

守ること:
- {max_chars}字以内。日本語。
- 自然な口語。適度に絵文字と改行を使い、読みやすくする。
- 「指定された切り口」を軸に据えて書く。毎回同じ構成にしない。
- 価格・レビュー評価・レビュー件数など、与えられた事実だけを根拠にする。
- **実際に自分が使った体験を創作しない。** 「届いてすぐ使ってます」のような未確認の
  体験談は書かない。レビューの傾向や商品の特徴として伝える。
- 効果・効能の断定（「必ず痩せる」「絶対に壊れない」など）はしない。
- 商品名をそのまま全部書き写さない。要点を自分の言葉にする。
- ハッシュタグは最大3個まで、末尾にまとめる。

出力は紹介文の本文のみ。前置き・見出し・囲みの記号は付けない。"""

# 同じ構成の繰り返しを避けるため、商品ごとに切り口を変える。
ANGLES = [
    "この価格でこの内容という納得感",
    "レビュー評価の高さと支持されている理由",
    "どんな場面で使うと便利かの具体的な提案",
    "贈り物・ギフトとしての使いどころ",
    "仕様やスペック面の強み",
    "定番として選ばれ続けている安心感",
]


def build_prompt(item: dict[str, Any], genre_label: str, angle: str) -> str:
    facts = {
        "商品名": item.get("itemName"),
        "価格": f"{int(item.get('itemPrice') or 0):,}円",
        "ジャンル": genre_label,
        "ショップ名": item.get("shopName"),
        "レビュー平均": item.get("reviewAverage"),
        "レビュー件数": item.get("reviewCount"),
        "キャッチコピー": item.get("catchcopy") or None,
        "商品説明": (item.get("itemCaption") or "")[:400] or None,
    }
    lines = [f"- {k}: {v}" for k, v in facts.items() if v not in (None, "")]
    return "商品情報:\n" + "\n".join(lines) + f"\n\n指定された切り口: {angle}"


def _client():
    config.load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise config.SetupError(
            "ANTHROPIC_API_KEY が未設定です。\n"
            f"プロジェクト直下の .env に記入してください（テンプレート: .env.example）。\n"
            f"  {config.DOTENV_PATH}\n"
            "キーは console.anthropic.com → Settings → API keys で発行できます。"
        )
    from anthropic import Anthropic

    return Anthropic()


def generate(
    items: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """各商品に `_pitch` を付与する。1件の失敗で全体を落とさない。"""
    llm_cfg = settings.get("llm", {})
    max_chars = int(llm_cfg.get("max_chars", 200))
    system = SYSTEM_PROMPT.format(max_chars=max_chars)
    genres = settings["genres"]

    client = None if dry_run else _client()

    for index, item in enumerate(items):
        genre_label = genres.get(item.get("_genre_key"), {}).get("label", "")
        angle = ANGLES[index % len(ANGLES)]
        prompt = build_prompt(item, genre_label, angle)
        item["_angle"] = angle

        if dry_run:
            print("=" * 70)
            print(f"[{index + 1}] {item.get('itemName')}")
            print("-" * 70)
            print(prompt)
            item["_pitch"] = None
            continue

        try:
            response = client.messages.create(
                model=str(llm_cfg.get("model", "claude-haiku-4-5")),
                max_tokens=int(llm_cfg.get("max_tokens", 1024)),
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            ).strip()
            item["_pitch"] = text or None
            if not text:
                print(f"[warn] {item.get('itemName')}: 空の応答が返りました。")
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で他の生成を止めない
            print(f"[warn] {item.get('itemName')}: プレゼン文の生成に失敗しました ({exc})")
            item["_pitch"] = None

    return items


@config.cli
def main() -> int:
    parser = argparse.ArgumentParser(description="プレゼン文の生成")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="APIを呼ばずプロンプトだけ表示する（キーを使う前の確認用）",
    )
    parser.add_argument(
        "--input",
        help="候補JSON（data/candidates/YYYY-MM-DD.json）。省略時は最新を使う",
    )
    args = parser.parse_args()

    settings = config.load_settings()
    if args.input:
        path = config.ROOT / args.input
    else:
        files = sorted(store.CANDIDATES_DIR.glob("*.json"))
        if not files:
            print("候補JSONがありません。先に `python -m room.pipeline --local` を実行してください。")
            return 1
        path = files[-1]

    items = json.loads(path.read_text(encoding="utf-8"))
    generate(items, settings, dry_run=args.dry_run)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
