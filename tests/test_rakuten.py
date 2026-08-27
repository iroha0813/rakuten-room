"""APIクライアントの組み立てとリクエスト組成のテスト。ネットワークは叩かない。"""

from __future__ import annotations

import pytest

from room import config, rakuten


BASE_SETTINGS = {
    "collect": {"request_interval_sec": 0.0, "max_retries": 1},
    "affiliate": {"commission_rate": 0.02, "use_affiliate_links": False},
}


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(
        config,
        "credentials",
        lambda: {
            "application_id": "app",
            "access_key": "key",
            "affiliate_id": "aff.123",
        },
    )


class TestAffiliateOptIn:
    """affiliateId を渡すと itemUrl が hb.afl.rakuten.co.jp に差し替わる。

    ROOMの「コレ！」は楽天市場の商品ページURLを前提とするため、
    既定では絶対に渡してはいけない。
    """

    def test_affiliate_id_is_not_sent_by_default(self, creds):
        client = rakuten.RakutenClient.from_env(BASE_SETTINGS)
        assert client.affiliate_id is None

    def test_affiliate_id_is_not_sent_when_key_absent(self, creds):
        settings = {**BASE_SETTINGS, "affiliate": {"commission_rate": 0.02}}
        assert rakuten.RakutenClient.from_env(settings).affiliate_id is None

    def test_affiliate_id_is_sent_only_when_explicitly_enabled(self, creds):
        settings = {**BASE_SETTINGS, "affiliate": {"use_affiliate_links": True}}
        assert rakuten.RakutenClient.from_env(settings).affiliate_id == "aff.123"


class TestRequestComposition:
    def _client(self, affiliate_id=None):
        return rakuten.RakutenClient(
            application_id="app",
            access_key="key",
            affiliate_id=affiliate_id,
            interval_sec=0.0,
        )

    def test_credentials_are_always_included(self, monkeypatch):
        captured = {}

        def fake_get(endpoint_key, params):
            captured["endpoint"] = endpoint_key
            captured["params"] = params
            return {"Items": []}

        client = self._client()
        monkeypatch.setattr(client, "_get", fake_get)
        client.search_items(keyword="テスト")

        assert captured["endpoint"] == "item_search"
        assert captured["params"]["keyword"] == "テスト"

    def test_none_affiliate_id_is_dropped_from_query(self, monkeypatch):
        """_get は None のパラメータを送信しない。"""
        captured = {}

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"Items": []}

        def fake_request(url, params=None, timeout=None):
            captured["params"] = params
            return FakeResponse()

        client = self._client(affiliate_id=None)
        monkeypatch.setattr(client._session, "get", fake_request)
        client.search_items(keyword="テスト")

        assert "affiliateId" not in captured["params"]
        assert captured["params"]["applicationId"] == "app"
        assert captured["params"]["accessKey"] == "key"

    def test_search_requires_a_criterion(self):
        with pytest.raises(ValueError):
            self._client().search_items()


class TestEndpoints:
    def test_each_api_has_its_own_host(self):
        """楽天はAPIごとにホストが異なる。取り違えると404になる。"""
        hosts = {
            key: url.split("/")[2] + "/" + url.split("/")[3]
            for key, url in rakuten.ENDPOINTS.items()
        }
        assert hosts["item_search"].endswith("ichibams")
        assert hosts["item_ranking"].endswith("ichibaranking")
        assert hosts["genre_search"].endswith("ichibagt")
