"""楽天ウェブサービス APIクライアント。

現行APIは applicationId に加えて accessKey が必須（2026-07-01版で追加）。
エンドポイントはAPIごとにホストが異なるので定数で持つ。
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import requests

from . import config

ENDPOINTS = {
    "item_search": "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701",
    "item_ranking": "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601",
    "genre_search": "https://openapi.rakuten.co.jp/ichibagt/api/IchibaGenre/Search/20260701",
}

# レスポンスを軽くするために取得フィールドを絞る。
ITEM_ELEMENTS = ",".join(
    [
        "itemCode",
        "itemName",
        "itemPrice",
        "itemUrl",
        "itemCaption",
        "catchcopy",
        "shopName",
        "shopCode",
        "genreId",
        "availability",
        "imageFlag",
        "mediumImageUrls",
        "reviewCount",
        "reviewAverage",
        "pointRate",
    ]
)


class RakutenApiError(RuntimeError):
    """楽天APIが回復不能なエラーを返した。"""


class RakutenClient:
    """レート制限とリトライを内蔵した薄いクライアント。

    楽天APIは概ね1秒1リクエストが上限とされるため、呼び出し間隔を必ず空ける。
    """

    def __init__(
        self,
        application_id: str,
        access_key: str,
        affiliate_id: str | None = None,
        interval_sec: float = 1.0,
        max_retries: int = 3,
    ) -> None:
        self.application_id = application_id
        self.access_key = access_key
        self.affiliate_id = affiliate_id
        self.interval_sec = interval_sec
        self.max_retries = max_retries
        self._session = requests.Session()
        self._last_call_at = 0.0

    @classmethod
    def from_env(cls, settings: dict[str, Any] | None = None) -> "RakutenClient":
        settings = settings or config.load_settings()
        collect = settings.get("collect", {})
        creds = config.credentials()
        return cls(
            application_id=creds["application_id"],
            access_key=creds["access_key"],
            affiliate_id=creds.get("affiliate_id"),
            interval_sec=float(collect.get("request_interval_sec", 1.0)),
            max_retries=int(collect.get("max_retries", 3)),
        )

    # -- 内部 ---------------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self.interval_sec:
            time.sleep(self.interval_sec - elapsed)

    def _get(self, endpoint_key: str, params: dict[str, Any]) -> dict[str, Any]:
        url = ENDPOINTS[endpoint_key]
        query = {
            "applicationId": self.application_id,
            "accessKey": self.access_key,
            "format": "json",
            "formatVersion": 2,
            **{k: v for k, v in params.items() if v is not None},
        }

        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self._session.get(url, params=query, timeout=30)
            except requests.RequestException as exc:
                last_error = f"通信エラー: {exc}"
            else:
                self._last_call_at = time.monotonic()
                if response.status_code == 200:
                    return response.json()
                # 400番台のうち429以外はリトライしても直らない
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise RakutenApiError(
                        f"{endpoint_key} が {response.status_code} を返しました: "
                        f"{response.text[:300]}"
                    )
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"

            if attempt < self.max_retries:
                time.sleep(self.interval_sec * (2**attempt))

        raise RakutenApiError(f"{endpoint_key} へのリクエストが失敗しました。{last_error}")

    # -- 公開API ------------------------------------------------------------

    def search_items(
        self,
        *,
        genre_id: str | int | None = None,
        keyword: str | None = None,
        hits: int = 30,
        page: int = 1,
        sort: str = "standard",
        min_price: int | None = None,
        max_price: int | None = None,
        min_review_average: float | None = None,
        min_review_count: int | None = None,
    ) -> list[dict[str, Any]]:
        """商品検索API。formatVersion=2 なので Items は商品dictの配列。"""
        if genre_id is None and keyword is None:
            raise ValueError("genre_id か keyword のどちらかは必須です。")
        payload = self._get(
            "item_search",
            {
                "genreId": genre_id,
                "keyword": keyword,
                "hits": hits,
                "page": page,
                "sort": sort,
                "minPrice": min_price,
                "maxPrice": max_price,
                "reviewAverage": min_review_average,
                "reviewCount": min_review_count,
                "elements": ITEM_ELEMENTS,
                "affiliateId": self.affiliate_id,
            },
        )
        return list(payload.get("Items") or [])

    def ranking_items(
        self,
        *,
        genre_id: str | int | None = None,
        page: int = 1,
        period: str | None = None,
    ) -> list[dict[str, Any]]:
        """ランキングAPI。返り値の各要素は rank を持つ。"""
        payload = self._get(
            "item_ranking",
            {
                "genreId": genre_id,
                "page": page,
                "period": period,
                "elements": ITEM_ELEMENTS + ",rank",
                "affiliateId": self.affiliate_id,
            },
        )
        return list(payload.get("Items") or [])

    def search_genre(self, genre_id: str | int = 0) -> dict[str, Any]:
        """ジャンル検索API。genre / children / ancestors / siblings を返す。"""
        return self._get("genre_search", {"genreId": genre_id})


def _smoke() -> int:
    """資格情報とエンドポイントの疎通を最小リクエストで確認する。"""
    settings = config.load_settings()
    client = RakutenClient.from_env(settings)
    failures = 0

    checks: list[tuple[str, Any]] = [
        ("ジャンル検索API", lambda: client.search_genre(0)),
        ("ランキングAPI", lambda: client.ranking_items()),
        ("商品検索API", lambda: client.search_items(keyword="キッチン", hits=3)),
        (
            "商品検索API(レビュー絞り込み付き)",
            lambda: client.search_items(
                keyword="キッチン",
                hits=3,
                sort="-reviewCount",
                min_review_average=4.0,
                min_review_count=20,
            ),
        ),
    ]

    for label, call in checks:
        try:
            result = call()
        except Exception as exc:  # noqa: BLE001 - 疎通確認なので全部拾って報告する
            failures += 1
            print(f"[NG] {label}: {exc}")
        else:
            size = len(result) if isinstance(result, list) else len(result.keys())
            print(f"[OK] {label} (要素数 {size})")

    if failures:
        print(f"\n{failures} 件のチェックが失敗しました。")
        print("401/403 なら applicationId / accessKey を、400 ならパラメータ名を確認してください。")
    else:
        print("\nすべての疎通確認に成功しました。")
    return 1 if failures else 0


@config.cli
def main() -> int:
    parser = argparse.ArgumentParser(description="楽天APIクライアントの疎通確認")
    parser.add_argument("--smoke", action="store_true", help="全エンドポイントの疎通を確認する")
    args = parser.parse_args()
    if args.smoke:
        return _smoke()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
