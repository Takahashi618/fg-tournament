"""閲覧用スナップショットの生成。

viewer.html がそのまま描画できる、解決済み・表示用のJSONを作る。
文言は domain/textgen に一本化されているため、アプリの読み上げと
Web表示が食い違わない。文言はアップロード時のアプリの言語で作り、
その言語コードを "lang" に入れる(viewer.html は画面の固定の文言をこれに合わせる)。

版2: 閲覧ページが読まない試合の id / index / label / winner を省き、総当たりの試合は
一覧に要る group / line / state だけにした(64人の総当たりは2016試合あり、閲覧ページが
15秒ごとに取りに来るため)。順位の表の行には順位の表記 place_label を付けた。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from domain import Format, Phase
from domain import standings, textgen
from i18n import get_language, rank_label, t

SNAPSHOT_VERSION = 2


def _match_entry(st, m) -> dict:
    entry = {
        "group": textgen.phase_round_label(st, m),
        "line": textgen.match_line(st, m),
        "state": m.state().value,
    }
    if m.phase == Phase.LEAGUE:
        # 総当たりはブラケット表示が無いので、一覧に要る項目だけ
        return entry
    r = m.result
    s1 = s2 = None
    if r is not None and not r.auto_bye and not r.forfeit:
        s1, s2 = r.score1, r.score2
    entry.update({
        "phase": m.phase.value,
        "round": m.round_no,
        "p1": textgen.slot_label(st, m, m.slot1),
        "p2": textgen.slot_label(st, m, m.slot2),
        "winner_slot": r.winner_slot if r is not None else None,
        "s1": s1,
        "s2": s2,
        "forfeit": bool(r.forfeit) if r is not None else False,
        "auto": bool(r.auto_bye) if r is not None else False,
    })
    return entry


def build_snapshot(engine, last_event: Optional[str] = None) -> dict:
    st = engine.state
    if not st.generated:
        raise ValueError(t("err.no_snapshot"))

    matches = [_match_entry(st, m) for m in engine.matches_bracket_order()]

    finished = engine.is_finished()
    champ = engine.champion_id()

    league = None
    if st.config.format == Format.ROUND_ROBIN:
        league = [{"rank": row["rank"],
                   "place_label": rank_label(row["rank"]),
                   "name": st.name_of(row["player_id"]),
                   "wins": row["wins"], "losses": row["losses"],
                   "diff": row["diff"]}
                  for row in standings.league_table(st)]

    placements = [{"place": p, "place_label": rank_label(p),
                   "name": st.name_of(pid)}
                  for p, pid in standings.final_placements(st)] or None

    elim_groups = None
    if st.config.format != Format.ROUND_ROBIN and not finished:
        elim_groups = [{"label": textgen.stage_label(st, ph, rn),
                        "names": [st.name_of(pid) for pid in pids]}
                       for ph, rn, pids in standings.elimination_groups(st)]

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "lang": get_language(),
        "title": st.config.title,
        "format": st.config.format.value,
        "format_name": textgen.format_name(st.config.format),
        "player_count": len(st.players),
        "players": [p.name for p in st.players],
        "finished": finished,
        "champion": st.name_of(champ) if champ else None,
        "summary": textgen.overall_summary(st),
        "updated_at": datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "last_event": last_event,
        "matches": matches,
        "standings_lines": textgen.standings_lines(st),
        "league_table": league,
        "placements": placements,
        "elimination_groups": elim_groups,
    }
