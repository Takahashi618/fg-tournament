"""ビュー内容の生成。build_view() は (タイトル, [(表示文字列, ペイロード), ...]) を返す。

ペイロード: ("match", match_id) / ("player", player_id) / (None, None)
表示・読み上げの文言は textgen と i18n に一本化する。
"""
from __future__ import annotations

from domain import textgen
from i18n import t

VIEW_KEYS = [
    "view.rounds",
    "view.players",
    "view.next",
    "view.standings",
]
VIEW_COUNT = len(VIEW_KEYS)


def view_title(index: int) -> str:
    return t(VIEW_KEYS[index])


def build_view(engine, index: int):
    st = engine.state
    title = view_title(index)
    if not st.generated:
        return title, [(t("view.empty_tournament"), (None, None))]
    if index == 0:
        rows = [(textgen.match_line(st, m), ("match", m.match_id))
                for m in engine.matches_bracket_order()]
    elif index == 1:
        rows = [(text, ("player", p.player_id))
                for p, text in zip(st.players, textgen.player_paths(st))]
    elif index == 2:
        ready = engine.ready_matches()
        if ready:
            rows = [(t("view.next_item", i=i + 1,
                       line=textgen.match_line(st, m)),
                     ("match", m.match_id))
                    for i, m in enumerate(ready)]
        else:
            rows = [(t("view.no_ready"), (None, None))]
    else:
        rows = [(line, (None, None))
                for line in textgen.standings_lines(st)]
    if not rows:
        rows = [(t("view.no_items"), (None, None))]
    return title, rows

