"""`.env` 読み込みのテスト。"""

from __future__ import annotations

import pytest

from room import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "RAKUTEN_AFFILIATE_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path, text, encoding="utf-8"):
    path = tmp_path / ".env"
    path.write_text(text, encoding=encoding)
    return path


class TestLoadDotenv:
    def test_missing_file_is_noop(self, tmp_path):
        assert config.load_dotenv(tmp_path / ".env") == []

    def test_reads_values(self, tmp_path, monkeypatch):
        path = write_env(tmp_path, "RAKUTEN_APP_ID=abc123\nRAKUTEN_ACCESS_KEY=key456\n")
        loaded = config.load_dotenv(path)
        assert set(loaded) == {"RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY"}
        assert config.os.environ["RAKUTEN_APP_ID"] == "abc123"

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        path = write_env(tmp_path, "# コメント\n\nRAKUTEN_APP_ID=abc\n")
        assert config.load_dotenv(path) == ["RAKUTEN_APP_ID"]

    def test_ignores_empty_values(self, tmp_path):
        """テンプレートのまま値が空の行は「未設定」として扱う。"""
        path = write_env(tmp_path, "RAKUTEN_APP_ID=\nRAKUTEN_ACCESS_KEY=key\n")
        assert config.load_dotenv(path) == ["RAKUTEN_ACCESS_KEY"]

    def test_tolerates_spaces_and_quotes(self, tmp_path):
        path = write_env(tmp_path, '  RAKUTEN_APP_ID = "abc123"  \n')
        config.load_dotenv(path)
        assert config.os.environ["RAKUTEN_APP_ID"] == "abc123"

    def test_value_may_contain_equals(self, tmp_path):
        """APIキーに = が含まれても壊れないこと。"""
        path = write_env(tmp_path, "ANTHROPIC_API_KEY=sk-ant-aa==bb\n")
        config.load_dotenv(path)
        assert config.os.environ["ANTHROPIC_API_KEY"] == "sk-ant-aa==bb"

    def test_does_not_override_real_env(self, tmp_path, monkeypatch):
        """GitHub Actions の Secrets（本物の環境変数）が常に優先されること。"""
        monkeypatch.setenv("RAKUTEN_APP_ID", "from-secrets")
        path = write_env(tmp_path, "RAKUTEN_APP_ID=from-dotenv\n")
        assert config.load_dotenv(path) == []
        assert config.os.environ["RAKUTEN_APP_ID"] == "from-secrets"

    def test_reads_utf8_with_bom(self, tmp_path):
        """メモ帳で保存するとBOMが付くことがある。"""
        path = write_env(tmp_path, "RAKUTEN_APP_ID=abc\n", encoding="utf-8-sig")
        assert config.load_dotenv(path) == ["RAKUTEN_APP_ID"]
        assert config.os.environ["RAKUTEN_APP_ID"] == "abc"

    def test_reads_cp932(self, tmp_path):
        """日本語コメント入りをcp932で保存されても読めること。"""
        path = write_env(tmp_path, "# 楽天の設定\nRAKUTEN_APP_ID=abc\n", encoding="cp932")
        assert config.load_dotenv(path) == ["RAKUTEN_APP_ID"]


class TestCredentials:
    def test_missing_credentials_point_at_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DOTENV_PATH", tmp_path / ".env")
        with pytest.raises(config.SetupError) as excinfo:
            config.credentials()
        message = str(excinfo.value)
        assert ".env" in message
        assert "RAKUTEN_APP_ID" in message

    def test_reads_from_dotenv(self, tmp_path, monkeypatch):
        path = write_env(tmp_path, "RAKUTEN_APP_ID=abc\nRAKUTEN_ACCESS_KEY=key\n")
        monkeypatch.setattr(config, "DOTENV_PATH", path)
        creds = config.credentials()
        assert creds["application_id"] == "abc"
        assert creds["access_key"] == "key"
        assert creds["affiliate_id"] is None
