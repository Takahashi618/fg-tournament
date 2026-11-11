"""表示・読み上げの言語切り替え(日本語 / 英語)。UI・音声・ネットワークに依存しない。

言語ごとのモジュール(ja.py / en.py)が、その言語の名前 NAME、文言 MESSAGES
(どの言語も同じキー)、順位の書き方 rank_label() / place() を持つ。
対応言語は _MODULES だけに登録する(LANGUAGES はここから作る)。
現在の言語はプロセス全体で1つ(set_language で切り替え)。
"""
from __future__ import annotations

from . import en, ja

DEFAULT_LANGUAGE = "ja"
# OS の表示言語が対応言語のどれでもないときに使う言語
FALLBACK_LANGUAGE = "en"

_MODULES = {
    "ja": ja,
    "en": en,
}

# 言語コード -> その言語での言語名(切り替えメニューでは常にこの名前を出す)
LANGUAGES: dict[str, str] = {code: mod.NAME for code, mod in _MODULES.items()}

_current = DEFAULT_LANGUAGE


def set_language(code: str) -> None:
    global _current
    if code not in _MODULES:
        raise ValueError(f"unsupported language: {code}")
    _current = code


def get_language() -> str:
    return _current


def t(key: str, **values) -> str:
    """現在の言語の文言。キーが無ければ日本語、それも無ければキー自体を返す。"""
    text = _MODULES[_current].MESSAGES.get(key)
    if text is None:
        text = _MODULES[DEFAULT_LANGUAGE].MESSAGES.get(key, key)
    return text.format(**values) if values else text


def join_names(names) -> str:
    """名前の列挙(日本語は「、」、英語は「, 」区切り)。"""
    return t("list_sep").join(names)


def rank_label(n: int) -> str:
    """表の順位の欄の表記。日本語「3位」、英語「3rd」。"""
    return _MODULES[_current].rank_label(n)


def place(n: int) -> str:
    """文の中の順位の表記。日本語「3位」、英語「3rd place」。"""
    return _MODULES[_current].place(n)


def catalog_keys(code: str) -> set[str]:
    """テスト用: 指定言語のキー一覧。"""
    return set(_MODULES[code].MESSAGES)
