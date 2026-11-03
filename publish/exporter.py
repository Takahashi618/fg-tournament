"""閲覧用スナップショットの生成。

viewer.html がそのまま描画できる、解決済み・表示用のJSONを作る。
文言は domain/textgen に一本化されているため、アプリの読み上げと
Web表示が食い違わない。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from domain import Format
from domain import standings, textgen

SNAPSHOT_VERSION = 1


def build_snapshot(engine, last_event: Optional[str] = None) -> dict:
    st = engine.state
    if not st.generated:
        raise ValueError("ブラケットが未生成のためスナップショットを作れません")

    matches = []
    for m in engine.matches_bracket_order():
        r = m.result
        s1 = s2 = None
        if r is not None and not r.auto_bye and not r.forfeit:
            s1, s2 = r.score1, r.score2
        matches.append({
            "id": m.match_id,
            "phase": m.phase.value,
            "round": m.round_no,
            "index": m.index_in_round,
            "group": textgen.phase_round_label(st, m),
            "label": textgen.match_label(st, m),
            "line": textgen.match_line(st, m),
            "p1": textgen.slot_label(st, m, m.slot1),
            "p2": textgen.slot_label(st, m, m.slot2),
            "state": m.state().value,
            "winner": st.name_of(m.winner_id()) if r is not None else None,
            "winner_slot": r.winner_slot if r is not None else None,
            "s1": s1,
            "s2": s2,
            "forfeit": bool(r.forfeit) if r is not None else False,
            "auto": bool(r.auto_bye) if r is not None else False,
        })

    finished = engine.is_finished()
    champ = engine.champion_id()

    league = None
    if st.config.format == Format.ROUND_ROBIN:
        league = [{"rank": row["rank"],
                   "name": st.name_of(row["player_id"]),
                   "wins": row["wins"], "losses": row["losses"],
                   "diff": row["diff"]}
                  for row in standings.league_table(st)]

    placements = [{"place": p, "name": st.name_of(pid)}
                  for p, pid in standings.final_placements(st)] or None

    elim_groups = None
    if st.config.format != Format.ROUND_ROBIN and not finished:
        elim_groups = [{"label": textgen.stage_label(st, ph, rn),
                        "names": [st.name_of(pid) for pid in pids]}
                       for ph, rn, pids in standings.elimination_groups(st)]

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "title": st.config.title,
        "format": st.config.format.value,
        "format_name": textgen.FORMAT_NAMES[st.config.format],
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
