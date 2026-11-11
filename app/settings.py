"""アプリの設定(いまは表示と読み上げの言語だけ)。data/settings.ini に保存する。

大会ファイル(.json)と同じ data フォルダに置くので、「開く」の一覧(*.json)に
混ざらないよう INI 形式にする。
初回(設定ファイルが無いとき)は OS の表示言語が対応言語ならその言語、
そうでなければ英語(i18n.FALLBACK_LANGUAGE)で始める。読み取りに失敗したときは日本語。
"""
from __future__ import annotations

import configparser
import sys
from pathlib import Path

from i18n import DEFAULT_LANGUAGE, FALLBACK_LANGUAGE, LANGUAGES

SETTINGS_FILE = "settings.ini"
_SECTION = "app"


def _match_language(locale_name: str) -> str:
    """'ja_JP' のようなロケール名から、対応言語のコードを選ぶ。"""
    code = locale_name.split("_")[0].lower()
    return code if code in LANGUAGES else FALLBACK_LANGUAGE


def system_language() -> str:
    """OS の表示言語から既定の言語を決める。"""
    if sys.platform == "win32":
        try:
            import ctypes
            import locale
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            # 表に無い地域の変種は、その言語の既定の地域で引き直す
            name = (locale.windows_locale.get(langid)
                    or locale.windows_locale.get((langid & 0x3FF) | 0x400, ""))
        except Exception:
            return DEFAULT_LANGUAGE
        return _match_language(name) if name else FALLBACK_LANGUAGE
    try:
        import locale
        name = locale.getlocale()[0] or ""
    except Exception:
        name = ""
    return _match_language(name) if name else DEFAULT_LANGUAGE


def _read(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    try:
        cp.read(path, encoding="utf-8")  # ファイルが無ければ空のまま
    except (configparser.Error, OSError, UnicodeDecodeError):
        cp = configparser.ConfigParser()
    return cp


def load_language(data_dir: Path) -> str:
    cp = _read(Path(data_dir) / SETTINGS_FILE)
    lang = cp.get(_SECTION, "language", fallback="")
    return lang if lang in LANGUAGES else system_language()


def save_language(data_dir: Path, lang: str) -> None:
    """保存できなければ例外(OSError など)をそのまま投げる。"""
    path = Path(data_dir) / SETTINGS_FILE
    cp = _read(path)
    if not cp.has_section(_SECTION):
        cp.add_section(_SECTION)
    cp.set(_SECTION, "language", lang)
    Path(data_dir).mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f)
