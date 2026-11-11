"""読み上げ・表示用テキスト生成。画面の行テキストと音声は必ずここから生成し、
文言のズレを防ぐ。文言そのものは i18n の ja.py / en.py にある(現在の言語で出す)。

用語方針:
- 日本語のフェーズ名はカタカナ(ウィナーズ/ルーザーズ/グランドファイナル/リセット戦)。
  英字表記はPC-Talkerの読みが不安定なため、日本語では使わない。
- スコアは「2対1」/「2 to 1」形式(ハイフンは読みが環境依存のため使わない)。
- 英語では数の単複を避けるため「Players: 5」のような書き方にする。
"""
from __future__ import annotations

from collections import defaultdict

from i18n import join_names, place, t

from . import standings
from .models import (BYE_ID, Format, Match, MatchState, Phase, Slot,
                     SourceType, TournamentState, losers_rounds,
                     play_order_key, winners_rounds)

_FORMAT_KEYS = {
    Format.SINGLE_ELIM: "format.single_elim",
    Format.DOUBLE_ELIM: "format.double_elim",
    Format.ROUND_ROBIN: "format.round_robin",
}


def format_name(fmt: Format) -> str:
    return t(_FORMAT_KEYS[fmt])


def _dist_name(dist: int, round_no: int) -> str:
    if dist == 0:
        return t("stage.final")
    if dist == 1:
        return t("stage.semifinal")
    if dist == 2:
        return t("stage.quarterfinal")
    return t("stage.round", n=round_no)


def stage_label(state: TournamentState, phase: Phase, round_no: int) -> str:
    """フェーズとラウンド番号からラベルを作る(Match が無くても使える)。"""
    if phase == Phase.LEAGUE:
        return t("stage.league_round", n=round_no)
    if phase == Phase.GRAND_FINAL:
        return t("stage.grand_final")
    if phase == Phase.GRAND_FINAL_RESET:
        return t("stage.reset")
    if phase == Phase.THIRD_PLACE:
        return t("stage.third_place")
    if phase == Phase.WINNERS:
        k = winners_rounds(state)
        name = _dist_name(k - round_no, round_no)
        if state.config.format == Format.SINGLE_ELIM:
            return name
        return t("stage.winners", name=name)
    # ルーザーズ
    last = losers_rounds(state)
    d = last - round_no
    if d == 0:
        name = t("stage.final")
    elif d == 1:
        name = t("stage.semifinal")
    else:
        name = t("stage.round", n=round_no)
    return t("stage.losers", name=name)


def phase_round_label(state: TournamentState, m: Match) -> str:
    return stage_label(state, m.phase, m.round_no)


def _round_size(state: TournamentState, m: Match) -> int:
    """同じフェーズ・ラウンドの試合数(engine が生成時に数えた値)。"""
    return state.round_sizes.get((m.phase, m.round_no), 0)


def match_label(state: TournamentState, m: Match) -> str:
    label = phase_round_label(state, m)
    if _round_size(state, m) > 1:
        return t("match.numbered", label=label, i=m.index_in_round)
    return label


def slot_label(state: TournamentState, m: Match, slot: Slot) -> str:
    if slot.is_resolved():
        return state.name_of(slot.resolved_id)
    if m.phase == Phase.GRAND_FINAL_RESET:
        return t("slot.reset_tbd")
    if slot.source_type == SourceType.WINNER_OF:
        src = state.matches.get(slot.source_id)
        return (t("slot.winner_of", label=match_label(state, src))
                if src else t("name.tbd"))
    if slot.source_type == SourceType.LOSER_OF:
        src = state.matches.get(slot.source_id)
        return (t("slot.loser_of", label=match_label(state, src))
                if src else t("name.tbd"))
    return t("name.tbd")


def _scores_for_winner(m: Match) -> tuple[int, int]:
    r = m.result
    if r.winner_slot == 1:
        return r.score1, r.score2
    return r.score2, r.score1


def match_line(state: TournamentState, m: Match) -> str:
    """一覧表示・読み上げ兼用の1行テキスト。"""
    lbl = match_label(state, m)
    n1 = slot_label(state, m, m.slot1)
    n2 = slot_label(state, m, m.slot2)
    st = m.state()
    if st == MatchState.SKIPPED:
        return t("line.skipped", lbl=lbl)
    if st == MatchState.DONE:
        r = m.result
        if r.auto_bye:
            wid = m.winner_id()
            if wid == BYE_ID:
                return t("line.bye_none", lbl=lbl)
            return t("line.bye_win", lbl=lbl, w=state.name_of(wid))
        w = state.name_of(m.winner_id())
        if r.forfeit:
            return t("line.forfeit", lbl=lbl, n1=n1, n2=n2, w=w)
        ws, ls = _scores_for_winner(m)
        return t("line.done", lbl=lbl, n1=n1, n2=n2, w=w, ws=ws, ls=ls)
    if st == MatchState.READY:
        return t("line.ready", lbl=lbl, n1=n1, n2=n2)
    if m.phase == Phase.GRAND_FINAL_RESET:
        return t("line.reset_pending", lbl=lbl)
    return t("line.waiting", lbl=lbl, n1=n1, n2=n2)


def _dest_label(state: TournamentState, dest: tuple[str, int]) -> str:
    return match_label(state, state.matches[dest[0]])


def report_announcement(state: TournamentState, m: Match) -> str:
    """結果反映後(伝播済み)の状態で呼ぶこと。結果の入力・修正の直後の読み上げ文。"""
    r = m.result
    w = state.name_of(m.winner_id())
    lid = m.loser_id()
    lo = state.name_of(lid)
    parts: list[str] = []
    if r.forfeit:
        parts.append(t("ann.forfeit", w=w, lo=lo))
    else:
        ws, ls = _scores_for_winner(m)
        parts.append(t("ann.win", w=w, ws=ws, ls=ls))

    champ = standings.champion_id(state)
    reset_now = (m.match_id == "GF" and state.config.gf_reset
                 and r.winner_slot == 2)
    # この試合で優勝が決まったか(決勝・グランドファイナル・リセット戦)。
    # 終了後に途中の試合を修正したとき、優勝者が勝った途中の試合を決勝と取り違えない
    decided_here = (champ is not None and champ == m.winner_id()
                    and m.winner_to is None and m.phase != Phase.LEAGUE)

    if m.phase == Phase.THIRD_PLACE:
        # 3位決定戦は勝ち上がりも敗退もない。順位をそのまま言う
        parts.append(t("ann.third_place", w=w, lo=lo))
        return t("sentence_sep").join(parts)

    if reset_now:
        parts.append(t("ann.reset", w=w, lo=lo))
    elif m.phase == Phase.LEAGUE:
        # 総当たりは、最後の試合の勝者が優勝者とは限らない。順位表から言う
        if standings.is_finished(state):
            parts.append(t("ann.league_champion", w=state.name_of(champ))
                         if champ is not None else t("st.tied"))
    elif decided_here:
        parts.append(t("ann.champion", w=w))
    elif m.winner_to is not None:
        parts.append(t("ann.winner_to", w=w,
                       dest=_dest_label(state, m.winner_to)))

    if lid is not None and lid != BYE_ID and not reset_now:
        if m.loser_to is not None:
            parts.append(t("ann.loser_to", lo=lo,
                           dest=_dest_label(state, m.loser_to)))
        elif m.phase == Phase.LEAGUE:
            pass
        elif decided_here:
            parts.append(t("ann.runner_up", lo=lo))
        else:
            parts.append(t("ann.eliminated", lo=lo,
                           stage=phase_round_label(state, m)))
    return t("sentence_sep").join(parts)


def _path_context(state: TournamentState) -> tuple:
    """選手の軌跡で全選手に共通の値: (優勝者, 敗退した選手の集合, 選手ごとの試合)。
    選手ごとに計算し直すと、64人の総当たりで選手別ビューの更新に数秒かかるため、
    player_paths() は1回だけ計算して使い回す。"""
    champ = standings.champion_id(state)
    eliminated: set[str] = set()
    for _ph, _rn, pids in standings.elimination_groups(state):
        eliminated.update(pids)
    k = winners_rounds(state)
    by_player: dict[str, list[Match]] = defaultdict(list)
    for m in sorted(state.matches.values(),
                    key=lambda m: play_order_key(m, k)):
        if m.skipped:
            continue
        for pid in m.players():
            if pid is not None:
                by_player[pid].append(m)
    return champ, eliminated, by_player


def player_path(state: TournamentState, player_id: str,
                context: tuple | None = None) -> str:
    """選手別ビュー用。その選手の軌跡を1文で返す。
    context は _path_context() の結果(省略時はここで計算する)。"""
    champ, eliminated, by_player = (context if context is not None
                                    else _path_context(state))
    name = state.name_of(player_id)
    segs: list[str] = []
    remaining = 0
    for m in by_player.get(player_id, []):
        lbl = match_label(state, m)
        if m.result is not None:
            if m.result.auto_bye:
                if m.winner_id() == player_id:
                    segs.append(t("path.bye", lbl=lbl))
                continue
            opp_id = (m.slot2.resolved_id
                      if m.slot1.resolved_id == player_id
                      else m.slot1.resolved_id)
            opp = state.name_of(opp_id)
            ws, ls = _scores_for_winner(m)
            if m.winner_id() == player_id:
                if m.result.forfeit:
                    segs.append(t("path.forfeit_win", lbl=lbl, opp=opp))
                else:
                    segs.append(t("path.win", lbl=lbl, opp=opp,
                                  ws=ws, ls=ls))
            else:
                if m.result.forfeit:
                    segs.append(t("path.forfeit_loss", lbl=lbl, opp=opp))
                else:
                    segs.append(t("path.loss", lbl=lbl, opp=opp,
                                  ws=ws, ls=ls))
        else:
            if state.config.format == Format.ROUND_ROBIN:
                remaining += 1
            else:
                opp_slot = (m.slot2 if m.slot1.resolved_id == player_id
                            else m.slot1)
                segs.append(t("path.current", lbl=lbl,
                              opp=slot_label(state, m, opp_slot)))
                break
    if state.config.format == Format.ROUND_ROBIN and remaining:
        segs.append(t("path.remaining", n=remaining))

    if champ == player_id:
        segs.append(t("path.champion"))
    elif player_id in eliminated:
        segs.append(t("path.eliminated"))
    if not segs:
        segs.append(t("path.no_matches"))
    return t("path.text", name=name, segs=t("phrase_sep").join(segs))


def player_paths(state: TournamentState) -> list[str]:
    """全選手の軌跡(登録順)。共通の値は1回だけ計算する。"""
    context = _path_context(state)
    return [player_path(state, p.player_id, context) for p in state.players]


def overall_summary(state: TournamentState) -> str:
    """F5 で読み上げる全体状況。"""
    if not state.generated:
        if not state.players:
            # 大会を作る前・開く前(メイン画面の一覧と同じ案内)
            return t("view.empty_tournament")
        return t("sum.not_generated",
                 title=state.config.title or t("setup.default_title"),
                 n=len(state.players))
    total, done = standings.real_match_counts(state)
    parts = [t("sum.head", title=state.config.title,
               fmt=format_name(state.config.format),
               n=len(state.players), total=total, done=done)]
    champ = standings.champion_id(state)
    if champ:
        parts.append(t("sum.champion", name=state.name_of(champ)))
    else:
        k = winners_rounds(state)
        ready = [m for m in sorted(state.matches.values(),
                                   key=lambda m: play_order_key(m, k))
                 if m.state() == MatchState.READY]
        if ready:
            parts.append(t("sum.next", line=match_line(state, ready[0])))
    return t("sentence_sep").join(parts)

def standings_lines(state: TournamentState) -> list[str]:
    """順位・状況ビューの行(アプリ表示とWeb公開で共用)。"""
    lines: list[str] = []
    champ = standings.champion_id(state)

    if state.config.format == Format.ROUND_ROBIN:
        if champ:
            lines.append(t("st.champion", name=state.name_of(champ)))
        elif standings.is_finished(state):
            lines.append(t("st.tied"))
        for row in standings.league_table(state):
            name = state.name_of(row["player_id"])
            lines.append(t("st.league_row", place=place(row["rank"]),
                           name=name, wins=row["wins"],
                           losses=row["losses"],
                           diff=f"{row['diff']:+d}"))
        return lines

    if standings.is_finished(state):
        lines.append(t("st.finished"))
        for rank, pid in standings.final_placements(state):
            lines.append(t("st.place", place=place(rank),
                           name=state.name_of(pid)))
        return lines

    groups = standings.elimination_groups(state)
    eliminated: set[str] = set()
    for _ph, _rn, pids in groups:
        eliminated.update(pids)
    alive = [p.name for p in state.players if p.player_id not in eliminated]
    if alive:
        lines.append(t("st.alive", names=join_names(alive)))
    for ph, rn, pids in groups:
        label = stage_label(state, ph, rn)
        names = join_names(state.name_of(pid) for pid in pids)
        lines.append(t("st.eliminated", label=label, names=names))
    if not groups:
        lines.append(t("st.no_eliminated"))
    return lines
