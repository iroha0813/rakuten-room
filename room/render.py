"""投稿候補ページ（静的HTML）の生成。

外部CDNに依存しない単一HTML。スマホ1カラム。ライト/ダーク両対応。
投稿済みチェックは localStorage に保存する（閲覧者ごとのメモ）。
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import date
from pathlib import Path
from typing import Any

from . import config

ARCHIVE_KEEP_DAYS = 7

# ROOMの投稿画面を商品指定で開くURL。末尾に itemCode を連結して使う。
# 楽天市場の商品ページを経由せずに「コレ！」画面へ直接飛べる。
ROOM_POST_URL = "https://room.rakuten.co.jp/mix?itemcode="

TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{{TITLE}}</title>
<style>
  :root {
    --bg: #f6f6f4;
    --surface: #ffffff;
    --border: #e2e1dc;
    --text: #22211e;
    --muted: #6b6862;
    --accent: #bf0000;
    --accent-text: #ffffff;
    --ok: #2f7d4f;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #171614;
      --surface: #211f1c;
      --border: #35322d;
      --text: #ece9e3;
      --muted: #a09b92;
      --accent: #ff5a5a;
      --accent-text: #171614;
      --ok: #6fcf97;
    }
  }
  :root[data-theme="dark"] {
    --bg: #171614;
    --surface: #211f1c;
    --border: #35322d;
    --text: #ece9e3;
    --muted: #a09b92;
    --accent: #ff5a5a;
    --accent-text: #171614;
    --ok: #6fcf97;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 0 16px 64px;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic UI",
      "Noto Sans JP", sans-serif;
    line-height: 1.7;
    -webkit-text-size-adjust: 100%;
  }
  .wrap { max-width: 560px; margin: 0 auto; }
  header { padding: 28px 0 16px; }
  h1 { font-size: 1.25rem; margin: 0 0 4px; letter-spacing: .01em; }
  .sub { color: var(--muted); font-size: .85rem; margin: 0; }
  .progress { color: var(--muted); font-size: .85rem; margin: 10px 0 0; }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px;
    margin: 0 0 16px;
  }
  .card.done { opacity: .5; }
  .head { display: flex; gap: 12px; align-items: flex-start; }
  .thumb {
    width: 84px; height: 84px; flex: 0 0 84px;
    object-fit: contain; background: #fff; border-radius: 8px;
    border: 1px solid var(--border);
  }
  .name { font-size: .95rem; font-weight: 600; margin: 0 0 4px; }
  .meta { color: var(--muted); font-size: .8rem; margin: 0; }
  .price { color: var(--accent); font-weight: 700; }
  .pitch {
    white-space: pre-wrap;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
    margin: 14px 0 12px;
    font-size: .92rem;
  }
  .pitch.missing { color: var(--muted); font-style: italic; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  button, .btn {
    font: inherit; font-size: .88rem; cursor: pointer;
    border-radius: 999px; padding: 9px 16px; border: 1px solid var(--border);
    background: var(--surface); color: var(--text); text-decoration: none;
    display: inline-block;
  }
  .primary {
    background: var(--accent); color: var(--accent-text); border-color: transparent;
    flex: 1 1 100%; text-align: center; font-weight: 600;
  }
  .copied { background: var(--ok); color: var(--accent-text); border-color: transparent; }
  .done-row { margin-top: 12px; font-size: .85rem; color: var(--muted); }
  .done-row label { cursor: pointer; user-select: none; }
  footer { color: var(--muted); font-size: .8rem; padding-top: 12px; }
  footer a { color: var(--muted); }
  .empty { text-align: center; color: var(--muted); padding: 48px 0; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>今日の楽天ROOM投稿候補</h1>
    <p class="sub">{{DATE}} ・ {{COUNT}}件</p>
    <p class="progress" id="progress"></p>
  </header>
{{CARDS}}
  <footer>
    <p>「文章をコピーしてROOMで開く」を押すと、紹介文がクリップボードに入り、
       ROOMの投稿画面がその商品で開きます。コメント欄に貼り付け、内容を確認して投稿してください。</p>
  </footer>
</div>
<script>
  var STORAGE_KEY = "room-done-{{DATE}}";

  function readDone() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); }
    catch (e) { return {}; }
  }
  function writeDone(state) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
    catch (e) { /* プライベートウィンドウ等では保存できない。表示は続行する。 */ }
  }
  function updateProgress() {
    var total = document.querySelectorAll(".card").length;
    var done = document.querySelectorAll(".card.done").length;
    var el = document.getElementById("progress");
    if (el) { el.textContent = total ? "投稿済み " + done + " / " + total : ""; }
  }
  function fallbackCopy(text, onDone) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); onDone(); } catch (e) { /* コピー不可 */ }
    document.body.removeChild(ta);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var state = readDone();

    document.querySelectorAll(".card").forEach(function (card) {
      var key = card.getAttribute("data-key");
      var box = card.querySelector("input[type=checkbox]");
      if (state[key]) {
        card.classList.add("done");
        if (box) { box.checked = true; }
      }
      if (box) {
        box.addEventListener("change", function () {
          var s = readDone();
          if (box.checked) { s[key] = 1; card.classList.add("done"); }
          else { delete s[key]; card.classList.remove("done"); }
          writeDone(s);
          updateProgress();
        });
      }
    });

    function copyPitch(el, onDone) {
      var text = el.getAttribute("data-pitch") || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        // await しない。ここで待つと a要素のネイティブ遷移がブロックされうる。
        navigator.clipboard.writeText(text).then(onDone, function () {
          fallbackCopy(text, onDone);
        });
      } else {
        fallbackCopy(text, onDone);
      }
    }

    function flash(el, message) {
      var original = el.textContent;
      el.textContent = message;
      el.classList.add("copied");
      setTimeout(function () {
        el.textContent = original;
        el.classList.remove("copied");
      }, 1600);
    }

    document.querySelectorAll("button.copy").forEach(function (btn) {
      btn.addEventListener("click", function () {
        copyPitch(btn, function () { flash(btn, "コピーしました"); });
      });
    });

    // ROOMの投稿画面を開くリンク。preventDefault しないので遷移はそのまま走り、
    // 開いた先で貼り付けるだけで済むようクリップボードに文章を入れておく。
    document.querySelectorAll("a.post").forEach(function (link) {
      link.addEventListener("click", function () {
        copyPitch(link, function () {});
      });
    });

    updateProgress();
  });
</script>
</body>
</html>
"""


def _card(item: dict[str, Any], genres: dict[str, Any]) -> str:
    esc = html.escape
    name = esc(str(item.get("itemName") or ""))
    price = int(item.get("itemPrice") or 0)
    shop = esc(str(item.get("shopName") or ""))
    review_avg = item.get("reviewAverage") or 0
    review_count = int(item.get("reviewCount") or 0)
    url = esc(str(item.get("itemUrl") or ""), quote=True)
    code = esc(str(item.get("itemCode") or ""), quote=True)
    genre_label = esc(str(genres.get(item.get("_genre_key"), {}).get("label", "")))

    images = item.get("mediumImageUrls") or []
    image = images[0] if images else ""
    if isinstance(image, dict):  # formatVersion=1 の形も一応受ける
        image = image.get("imageUrl", "")
    image = esc(str(image), quote=True)

    # ROOMの「コレ！して投稿する」画面を商品指定で直接開けるURL。
    # itemcode は楽天APIが返す itemCode をそのまま使える。
    # コメントの事前入力はROOM側が受け付けないため、貼り付けは手作業になる。
    room_url = esc(ROOM_POST_URL + str(item.get("itemCode") or ""), quote=True)

    pitch = item.get("_pitch")
    if pitch:
        pitch_attr = esc(pitch, quote=True)
        pitch_html = '<div class="pitch">' + esc(pitch) + "</div>"
        actions = (
            f'<a class="btn primary post" href="{room_url}" target="_blank" rel="noopener"'
            f' data-pitch="{pitch_attr}">文章をコピーしてROOMで開く</a>\n'
            f'      <button class="copy" data-pitch="{pitch_attr}">文章だけコピー</button>'
        )
    else:
        pitch_html = (
            '<div class="pitch missing">紹介文の生成に失敗しました。'
            "商品ページを見て自分で書くか、明日の候補に回してください。</div>"
        )
        actions = (
            f'<a class="btn primary" href="{room_url}" target="_blank" rel="noopener">'
            "ROOMで開く</a>"
        )

    thumb = f'<img class="thumb" src="{image}" alt="" loading="lazy">' if image else ""

    return f"""  <article class="card" data-key="{code}">
    <div class="head">
      {thumb}
      <div>
        <p class="name">{name}</p>
        <p class="meta"><span class="price">{price:,}円</span> ・ ★{review_avg} ({review_count}件)</p>
        <p class="meta">{genre_label} ・ {shop}</p>
      </div>
    </div>
    {pitch_html}
    <div class="actions">
      {actions}
      <a class="btn" href="{url}" target="_blank" rel="noopener">楽天市場</a>
    </div>
    <div class="done-row">
      <label><input type="checkbox"> ROOMに投稿した</label>
    </div>
  </article>"""


def _prune_archive(archive_dir: Path) -> None:
    for stale in sorted(archive_dir.glob("*.html"), reverse=True)[ARCHIVE_KEEP_DAYS:]:
        stale.unlink()


def render(
    items: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    day: date | None = None,
    output: Path | None = None,
) -> Path:
    day = day or date.today()
    genres = settings["genres"]

    if items:
        cards = "\n".join(_card(item, genres) for item in items)
    else:
        cards = (
            '  <p class="empty">今日は条件を満たす候補が見つかりませんでした。<br>'
            "config/settings.yaml のレビュー基準や価格帯を緩めてみてください。</p>"
        )

    # 過去ページへのリンクは張らない。楽天ウェブサービス利用規約 第8条4項が
    # 「ウェブサービスを使用した部分に楽天サイト以外へのリンクを設置すること」を
    # 禁じているため。過去分は docs/archive/YYYY-MM-DD.html に直接アクセスする。
    page = (
        TEMPLATE.replace("{{TITLE}}", f"ROOM投稿候補 {day.isoformat()}")
        .replace("{{DATE}}", day.isoformat())
        .replace("{{COUNT}}", str(len(items)))
        .replace("{{CARDS}}", cards)
    )

    archive_dir = config.DOCS_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"{day.isoformat()}.html").write_text(page, encoding="utf-8")
    _prune_archive(archive_dir)

    output = output or (config.DOCS_DIR / "index.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return output


@config.cli
def main() -> int:
    """候補JSONからページだけを作り直す。デザイン調整時に使う。"""
    parser = argparse.ArgumentParser(description="候補ページの再生成")
    parser.add_argument("input", help="data/candidates/YYYY-MM-DD.json")
    args = parser.parse_args()

    path = Path(args.input)
    items = json.loads(path.read_text(encoding="utf-8"))
    settings = config.load_settings()
    output = render(items, settings, day=date.fromisoformat(path.stem))
    print(f"生成しました: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
