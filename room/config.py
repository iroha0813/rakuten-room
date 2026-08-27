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
DOTENV_PATH = ROOT / ".env"


def load_dotenv(path: Path | None = None) -> list[str]:
    """.env があれば環境変数に流し込む。読み込んだキー名を返す。

    既に設定済みの環境変数は上書きしない。GitHub Actions では Secrets が
    本物の環境変数として渡るため、そちらが常に優先される。
    """
    path = path or DOTENV_PATH
    if not path.exists():
        return []

    # メモ帳で保存されるとBOM付きUTF-8やcp932になりうるので順に試す
    text = None
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SetupError(
            f"{path} の文字コードを判別できませんでした。UTF-8 で保存し直してください。"
        )

    loaded: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")  # 値に = が含まれても壊れない
        if not separator:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


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
    """楽天APIの資格情報。.env / 環境変数 / GitHub Secrets から読む。

    現行の楽天ウェブサービスAPIは applicationId と accessKey の両方が必須。
    """
    load_dotenv()
    app_id = os.environ.get("RAKUTEN_APP_ID")
    access_key = os.environ.get("RAKUTEN_ACCESS_KEY")
    missing = [
        name
        for name, value in (("RAKUTEN_APP_ID", app_id), ("RAKUTEN_ACCESS_KEY", access_key))
        if not value
    ]
    if missing:
        raise SetupError(
            f"資格情報が未設定です: {', '.join(missing)}\n"
            f"プロジェクト直下の .env に記入してください（テンプレート: .env.example）。\n"
            f"  {DOTENV_PATH}\n"
            "Rakuten Developers のアプリ管理画面で applicationId と accessKey を確認できます。\n"
            "GitHub Actions で動かす場合は同じ名前で Secrets に登録してください。"
        )
    return {
        "application_id": app_id,
        "access_key": access_key,
        # ROOM投稿では不要（ROOMが自動でリンク化する）。ブログ展開時のみ使う。
        "affiliate_id": os.environ.get("RAKUTEN_AFFILIATE_ID"),
    }
