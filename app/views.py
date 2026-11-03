"""ビュー内容の生成。build_view() は (タイトル, [(表示文字列, ペイロード), ...]) を返す。

ペイロード: ("match", match_id) / ("player", player_id) / (None, None)
表示・読み上げの文言は textgen に一本化する。
"""
from __future__ import annotations

from domain import textgen

VIEW_TITLES = [
    "ラウンド別試合一覧",
    "選手別の軌跡",
    "次の試合",
    "順位・状況",
]

EMPTY_ROW = ("大会がありません。Ctrl+Nで新規作成、Ctrl+Oで読み込みできます。",
             (None, None))


def build_view(engine, index: int):
    st = engine.state
    title = VIEW_TITLES[index]
    if not st.generated:
        return title, [EMPTY_ROW]
    if index == 0:
        rows = [(textgen.match_line(st, m), ("match", m.match_id))
                for m in engine.matches_bracket_order()]
    elif index == 1:
        rows = [(textgen.player_path(st, p.player_id), ("player", p.player_id))
                for p in st.players]
    elif index == 2:
        ready = engine.ready_matches()
        if ready:
            rows = [(f"{i + 1}番目 {textgen.match_line(st, m)}",
                     ("match", m.match_id))
                    for i, m in enumerate(ready)]
        else:
            rows = [("対戦可能な試合はありません。", (None, None))]
    else:
        rows = [(line, (None, None)) for line in standings_lines(engine)]
    if not rows:
        rows = [("表示する項目がありません。", (None, None))]
    return title, rows


def standings_lines(engine) -> list[str]:
    return textgen.standings_lines(engine.state)
