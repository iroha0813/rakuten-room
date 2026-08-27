"""楽天ROOM 投稿候補の自動生成パイプライン。"""

import sys

__all__ = [
    "config",
    "rakuten",
    "collect",
    "score",
    "write",
    "render",
    "store",
    "report",
    "pipeline",
]


def _use_utf8_stdio() -> None:
    """標準出力をUTF-8にする。

    Windowsのコンソールは既定がcp932で、生成文に含まれる絵文字やダッシュを
    print しようとすると UnicodeEncodeError を投げる。API呼び出しは成功して
    いるのに警告の出力だけで例外になり、生成結果が捨てられる事故が起きたため、
    パッケージ読み込み時に一度だけ切り替える。
    GitHub Actions (Linux) は元からUTF-8なので影響しない。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # リダイレクト先が差し替えられている場合など
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


_use_utf8_stdio()
