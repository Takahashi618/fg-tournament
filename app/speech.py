"""音声読み上げ。accessible_output2 経由でアクティブなスクリーンリーダーに送る。

- 環境変数 TOURNEY_SILENT=1 で全読み上げを抑止(テスト用)
- プレフィックスは [警告] / [エラー] のみ。通常の読み上げには付けない(ユーザー方針)
"""
from __future__ import annotations

import os

_SILENT = os.environ.get("TOURNEY_SILENT") == "1"


class Speaker:
    def __init__(self) -> None:
        self._ao = None
        if not _SILENT:
            try:
                from accessible_output2.outputs import auto
                self._ao = auto.Auto()
            except Exception:
                # スクリーンリーダー出力が使えなくてもアプリは動かす
                self._ao = None

    def say(self, text: str, interrupt: bool = True) -> None:
        if _SILENT or self._ao is None or not text:
            return
        try:
            self._ao.speak(text, interrupt=interrupt)
        except Exception:
            pass

    def info(self, text: str, interrupt: bool = True) -> None:
        self.say(text, interrupt)

    def success(self, text: str, interrupt: bool = True) -> None:
        self.say(text, interrupt)

    def warn(self, text: str, interrupt: bool = True) -> None:
        self.say(f"[警告] {text}", interrupt)

    def error(self, text: str, interrupt: bool = True) -> None:
        self.say(f"[エラー] {text}", interrupt)
