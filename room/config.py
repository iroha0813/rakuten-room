"""設定ファイルの読み込み。

設定はすべて config/*.yaml に置き、コードには定数を埋め込まない。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
LOCAL_DIR = ROOT / "local"


class SetupError(RuntimeError):
    """セットアップ不足。ユーザーが直せる問題なので traceback は見せない。"""


def cli(func):
    """CLI の main を包み、セットアップ不足を読める1行で返す。"""

    def wrapper() -> int:
        try:
            return func()
        except SetupError as exc:
            print(f"\n[セットアップが必要です]\n{exc}")
            return 1

    return wrapper


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SetupError(
            f"設定ファイルが見つかりません: {path}\n"
            "config/ 以下のファイルが揃っているか確認してください。"
        )
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings() -> dict[str, Any]:
    return _load_yaml(CONFIG_DIR / "settings.yaml")


def load_weights() -> dict[str, float]:
    return {k: float(v) for k, v in _load_yaml(CONFIG_DIR / "weights.yaml").items()}


def load_genres() -> dict[str, Any]:
    """scripts/fetch_genres.py が生成したジャンルIDマップ。"""
    path = CONFIG_DIR / "genres.yaml"
    if not path.exists():
        raise SetupError(
            "config/genres.yaml がありません。\n"
            "初回セットアップとして `python scripts/fetch_genres.py` を実行してください。"
        )
    return _load_yaml(path)


def credentials() -> dict[str, str | None]:
    """楽天APIの資格情報。GitHub Secrets / 環境変数から読む。

    現行の楽天ウェブサービスAPIは applicationId と accessKey の両方が必須。
    """
    app_id = os.environ.get("RAKUTEN_APP_ID")
    access_key = os.environ.get("RAKUTEN_ACCESS_KEY")
    missing = [
        name
        for name, value in (("RAKUTEN_APP_ID", app_id), ("RAKUTEN_ACCESS_KEY", access_key))
        if not value
    ]
    if missing:
        raise SetupError(
            f"環境変数が未設定です: {', '.join(missing)}\n"
            "Rakuten Developers のアプリ管理画面で applicationId と accessKey を確認し、\n"
            "ローカルでは環境変数、GitHub Actions では Secrets に設定してください。"
        )
    return {
        "application_id": app_id,
        "access_key": access_key,
        # ROOM投稿では不要（ROOMが自動でリンク化する）。ブログ展開時のみ使う。
        "affiliate_id": os.environ.get("RAKUTEN_AFFILIATE_ID"),
    }
