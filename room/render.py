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
  button.primary { background: var(--accent); color: var(--accent-text); border-color: transparent; }
  button.copied { background: var(--ok); color: var(--accent-text); border-color: transparent; }
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
    <p>使い方: 紹介文をコピー → 「楽天市場で開く」→ 商品ページから「コレ！」して貼り付け。</p>
    {{ARCHIVE}}
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

    document.querySelectorAll("button.copy").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var text = btn.getAttribute("data-pitch") || "";
        var onDone = function () {
          var original = btn.textContent;
          btn.textContent = "コピーしました";
          btn.classList.add("copied");
          setTimeout(function () {
            btn.textContent = original;
            btn.classList.remove("copied");
          }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(onDone, function () {
            fallbackCopy(text, onDone);
          });
        } else {
          fallbackCopy(text, onDone);
        }
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

    pitch = item.get("_pitch")
    if pitch:
        pitch_html = '<div class="pitch">' + esc(pitch) + "</div>"
        actions = (
            '<button class="primary copy" data-pitch="'
            + esc(pitch, quote=True)
            + '">紹介文をコピー</button>'
        )
    else:
        pitch_html = (
            '<div class="pitch missing">紹介文の生成に失敗しました。'
            "商品ページを見て自分で書くか、明日の候補に回してください。</div>"
        )
        actions = ""

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
      <a class="btn" href="{url}" target="_blank" rel="noopener">楽天市場で開く</a>
    </div>
    <div class="done-row">
      <label><input type="checkbox"> ROOMに投稿した</label>
    </div>
  </article>"""


def _archive_links(day: date) -> str:
    """当日分を除いた過去ページへのリンク。"""
    archive_dir = config.DOCS_DIR / "archive"
    if not archive_dir.exists():
        return ""
    links = [
        f'<a href="archive/{f.name}">{f.stem}</a>'
        for f in sorted(archive_dir.glob("*.html"), reverse=True)
        if f.stem != day.isoformat()
    ][:ARCHIVE_KEEP_DAYS]
    if not links:
        return ""
    return "<p>過去の候補: " + " ・ ".join(links) + "</p>"


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

    # アーカイブリンクは当日分を書き込む前に確定させる（自分自身へのリンクを避ける）
    archive_html = _archive_links(day)

    page = (
        TEMPLATE.replace("{{TITLE}}", f"ROOM投稿候補 {day.isoformat()}")
        .replace("{{DATE}}", day.isoformat())
        .replace("{{COUNT}}", str(len(items)))
        .replace("{{CARDS}}", cards)
        .replace("{{ARCHIVE}}", archive_html)
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
