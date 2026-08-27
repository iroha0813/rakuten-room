"""候補ページ生成のテスト。"""

from __future__ import annotations

import re
from datetime import date

import pytest

from room import render

SETTINGS = {"genres": {"food": {"label": "食品"}, "gadget": {"label": "ガジェット"}}}


def make_item(**overrides):
    item = {
        "itemCode": "hseason:10000260",
        "itemName": "無洗米 あきたこまち 10kg",
        "itemPrice": 5950,
        "itemUrl": "https://item.rakuten.co.jp/hseason/865804/",
        "shopName": "ハーベストシーズン",
        "reviewCount": 65235,
        "reviewAverage": 4.68,
        "mediumImageUrls": ["https://thumbnail.image.rakuten.co.jp/x.jpg"],
        "_genre_key": "food",
        "_pitch": "山形産あきたこまち、無洗米で手軽です。\n#無洗米",
    }
    item.update(overrides)
    return item


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setattr(render.config, "DOCS_DIR", tmp_path / "docs")

    def build(items, day=date(2026, 8, 27)):
        path = render.render(items, SETTINGS, day=day)
        return path.read_text(encoding="utf-8")

    return build


class TestRoomPostLink:
    def test_links_to_room_compose_screen_with_item_code(self, page):
        html = page([make_item()])
        assert "https://room.rakuten.co.jp/mix?itemcode=hseason:10000260" in html

    def test_post_link_carries_the_pitch_for_clipboard(self, page):
        html = page([make_item()])
        match = re.search(r'<a class="btn primary post"[^>]*data-pitch="([^"]*)"', html)
        assert match, "ROOM投稿リンクに data-pitch がありません"
        assert "あきたこまち" in match.group(1)

    def test_falls_back_to_plain_room_link_without_a_pitch(self, page):
        html = page([make_item(_pitch=None)])
        assert "https://room.rakuten.co.jp/mix?itemcode=hseason:10000260" in html
        assert 'class="btn primary post"' not in html
        assert "紹介文の生成に失敗しました" in html

    def test_keeps_the_ichiba_link(self, page):
        html = page([make_item()])
        assert "https://item.rakuten.co.jp/hseason/865804/" in html


class TestTermsCompliance:
    """楽天ウェブサービス利用規約 第8条4項。

    ウェブサービスを使用した部分には楽天サイト以外へのリンクを置かない。
    """

    def test_no_links_outside_rakuten(self, page, tmp_path):
        # 前日分のアーカイブがあっても、そこへのリンクは張らない
        archive = tmp_path / "docs" / "archive"
        archive.mkdir(parents=True)
        (archive / "2026-08-26.html").write_text("old", encoding="utf-8")

        html = page([make_item()])
        hrefs = re.findall(r'href="([^"]+)"', html)
        assert hrefs, "リンクが1つもない"
        assert all("rakuten.co.jp" in href for href in hrefs), hrefs
        assert "archive/2026-08-26.html" not in html


class TestEscaping:
    def test_escapes_markup_in_product_names(self, page):
        html = page([make_item(itemName='<script>alert(1)</script>お米')])
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_escapes_quotes_in_pitch_attribute(self, page):
        html = page([make_item(_pitch='彼は"最高"と言った')])
        assert 'data-pitch="彼は"最高"と言った"' not in html
        assert "&quot;" in html


class TestArchive:
    def test_writes_both_index_and_dated_archive(self, tmp_path, monkeypatch):
        docs = tmp_path / "docs"
        monkeypatch.setattr(render.config, "DOCS_DIR", docs)
        render.render([make_item()], SETTINGS, day=date(2026, 8, 27))
        assert (docs / "index.html").exists()
        assert (docs / "archive" / "2026-08-27.html").exists()

    def test_prunes_beyond_keep_days(self, tmp_path, monkeypatch):
        docs = tmp_path / "docs"
        archive = docs / "archive"
        archive.mkdir(parents=True)
        for day in range(1, 13):
            (archive / f"2026-08-{day:02d}.html").write_text("x", encoding="utf-8")
        monkeypatch.setattr(render.config, "DOCS_DIR", docs)

        render.render([make_item()], SETTINGS, day=date(2026, 8, 27))
        assert len(list(archive.glob("*.html"))) == render.ARCHIVE_KEEP_DAYS


class TestEmptyDay:
    def test_explains_what_to_do(self, page):
        html = page([])
        assert "条件を満たす候補が見つかりませんでした" in html
        assert "settings.yaml" in html
