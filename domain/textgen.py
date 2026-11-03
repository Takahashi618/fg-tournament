"""読み上げ・表示用テキスト生成。画面の行テキストと音声は必ずここから生成し、
文言のズレを防ぐ。

用語方針:
- フェーズ名はカタカナ(ウィナーズ/ルーザーズ/グランドファイナル/リセット戦)。
  英字表記はPC-Talkerの読みが不安定なため使わない。
- スコアは「2対1」形式(ハイフンは読みが環境依存のため使わない)。
"""
from __future__ import annotations

from . import standings
from .models import (BYE_ID, Format, Match, MatchState, Phase, Slot,
                     SourceType, TournamentState, losers_rounds,
                     play_order_key, winners_rounds)

FORMAT_NAMES = {
    Format.SINGLE_ELIM: "シングルエリミネーション",
    Format.DOUBLE_ELIM: "ダブルエリミネーション",
    Format.ROUND_ROBIN: "総当たりリーグ",
}


def _dist_name(dist: int, round_no: int) -> str:
    if dist == 0:
        return "決勝"
    if dist == 1:
        return "準決勝"
    if dist == 2:
        return "準々決勝"
    return f"{round_no}回戦"


def stage_label(state: TournamentState, phase: Phase, round_no: int) -> str:
    """フェーズとラウンド番号からラベルを作る(Match が無くても使える)。"""
    if phase == Phase.LEAGUE:
        return f"第{round_no}節"
    if phase == Phase.GRAND_FINAL:
        return "グランドファイナル"
    if phase == Phase.GRAND_FINAL_RESET:
        return "リセット戦"
    if phase == Phase.THIRD_PLACE:
        return "3位決定戦"
    if phase == Phase.WINNERS:
        k = winners_rounds(state)
        name = _dist_name(k - round_no, round_no)
        if state.config.format == Format.SINGLE_ELIM:
            return name
        return "ウィナーズ" + name
    # ルーザーズ
    last = losers_rounds(state)
    d = last - round_no
    if d == 0:
        name = "決勝"
    elif d == 1:
        name = "準決勝"
    else:
        name = f"{round_no}回戦"
    return "ルーザーズ" + name


def phase_round_label(state: TournamentState, m: Match) -> str:
    return stage_label(state, m.phase, m.round_no)


def match_label(state: TournamentState, m: Match) -> str:
    label = phase_round_label(state, m)
    count = sum(1 for x in state.matches.values()
                if x.phase == m.phase and x.round_no == m.round_no)
    if count > 1:
        return f"{label} 第{m.index_in_round}試合"
    return label


def slot_label(state: TournamentState, m: Match, slot: Slot) -> str:
    if slot.is_resolved():
        return state.name_of(slot.resolved_id)
    if m.phase == Phase.GRAND_FINAL_RESET:
        return "リセット発生時に決定"
    if slot.source_type == SourceType.WINNER_OF:
        src = state.matches.get(slot.source_id)
        return f"{match_label(state, src)}の勝者" if src else "未定"
    if slot.source_type == SourceType.LOSER_OF:
        src = state.matches.get(slot.source_id)
        return f"{match_label(state, src)}の敗者" if src else "未定"
    return "未定"


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
        return f"{lbl}: 実施なし"
    if st == MatchState.DONE:
        r = m.result
        if r.auto_bye:
            wid = m.winner_id()
            if wid == BYE_ID:
                return f"{lbl}: 不戦(対戦なし)"
            return f"{lbl}: {state.name_of(wid)} 不戦勝"
        w = state.name_of(m.winner_id())
        if r.forfeit:
            return f"{lbl}: {n1} 対 {n2}。{w}の不戦勝"
        ws, ls = _scores_for_winner(m)
        return f"{lbl}: {n1} 対 {n2}。{w}の勝ち、{ws}対{ls}"
    if st == MatchState.READY:
        return f"{lbl}: {n1} 対 {n2}。対戦可能"
    if m.phase == Phase.GRAND_FINAL_RESET:
        return f"{lbl}: グランドファイナルで敗者側が勝った場合のみ実施"
    return f"{lbl}: {n1} 対 {n2}。対戦者待ち"


def _dest_label(state: TournamentState, dest: tuple[str, int]) -> str:
    return match_label(state, state.matches[dest[0]])


def report_announcement(state: TournamentState, m: Match) -> str:
    """結果反映後(伝播済み)の状態で呼ぶこと。結果入力直後の読み上げ文。"""
    r = m.result
    w = state.name_of(m.winner_id())
    lid = m.loser_id()
    lo = state.name_of(lid)
    parts: list[str] = []
    if r.forfeit:
        parts.append(f"{lo}の棄権により{w}の不戦勝。")
    else:
        ws, ls = _scores_for_winner(m)
        parts.append(f"{w}の勝ち、{ws}対{ls}。")

    champ = standings.champion_id(state)
    reset_now = (m.match_id == "GF" and state.config.gf_reset
                 and r.winner_slot == 2)

    if reset_now:
        parts.append(f"ブラケットリセット。{w}と{lo}でリセット戦を行います。")
    elif champ is not None and champ == m.winner_id():
        parts.append(f"{w}の優勝です。")
    elif m.winner_to is not None:
        parts.append(f"{w}は{_dest_label(state, m.winner_to)}へ。")

    if lid is not None and lid != BYE_ID and not reset_now:
        if m.loser_to is not None:
            parts.append(f"{lo}は{_dest_label(state, m.loser_to)}へ。")
        elif m.phase == Phase.LEAGUE:
            pass
        elif champ is not None and champ == m.winner_id():
            parts.append(f"{lo}は準優勝。")
        else:
            parts.append(f"{lo}は{phase_round_label(state, m)}で敗退。")
    return "".join(parts)


def player_path(state: TournamentState, player_id: str) -> str:
    """選手別ビュー用。その選手の軌跡を1文で返す。"""
    name = state.name_of(player_id)
    k = winners_rounds(state)
    ms = sorted((m for m in state.matches.values()
                 if player_id in m.players() and not m.skipped),
                key=lambda m: play_order_key(m, k))
    segs: list[str] = []
    remaining = 0
    for m in ms:
        lbl = match_label(state, m)
        if m.result is not None:
            if m.result.auto_bye:
                if m.winner_id() == player_id:
                    segs.append(f"{lbl}は不戦勝")
                continue
            opp_id = (m.slot2.resolved_id
                      if m.slot1.resolved_id == player_id
                      else m.slot1.resolved_id)
            opp = state.name_of(opp_id)
            ws, ls = _scores_for_winner(m)
            if m.winner_id() == player_id:
                if m.result.forfeit:
                    segs.append(f"{lbl}で{opp}に不戦勝")
                else:
                    segs.append(f"{lbl}で{opp}に{ws}対{ls}で勝ち")
            else:
                if m.result.forfeit:
                    segs.append(f"{lbl}で{opp}に不戦敗")
                else:
                    segs.append(f"{lbl}で{opp}に{ls}対{ws}で負け")
        else:
            if state.config.format == Format.ROUND_ROBIN:
                remaining += 1
            else:
                opp_slot = (m.slot2 if m.slot1.resolved_id == player_id
                            else m.slot1)
                segs.append(f"現在{lbl}、対戦相手は"
                            f"{slot_label(state, m, opp_slot)}")
                break
    if state.config.format == Format.ROUND_ROBIN and remaining:
        segs.append(f"残り{remaining}試合")

    champ = standings.champion_id(state)
    if champ == player_id:
        segs.append("優勝")
    else:
        for _ph, _rn, pids in standings.elimination_groups(state):
            if player_id in pids:
                segs.append("敗退")
                break
    if not segs:
        segs.append("まだ試合がありません")
    return f"{name}。" + "。".join(segs) + "。"


def overall_summary(state: TournamentState) -> str:
    """F5 で読み上げる全体状況。"""
    if not state.generated:
        return (f"{state.config.title}。ブラケットは未生成です。"
                f"参加{len(state.players)}名。")
    total, done = standings.real_match_counts(state)
    fmt = FORMAT_NAMES[state.config.format]
    s = (f"{state.config.title}、{fmt}、参加{len(state.players)}名。"
         f"全{total}試合中{done}試合終了。")
    champ = standings.champion_id(state)
    if champ:
        s += f"優勝: {state.name_of(champ)}。"
    else:
        k = winners_rounds(state)
        ready = [m for m in sorted(state.matches.values(),
                                   key=lambda m: play_order_key(m, k))
                 if m.state() == MatchState.READY]
        if ready:
            s += f"次の試合、{match_line(state, ready[0])}。"
    return s

def standings_lines(state: TournamentState) -> list[str]:
    """順位・状況ビューの行(アプリ表示とWeb公開で共用)。"""
    lines: list[str] = []
    champ = standings.champion_id(state)

    if state.config.format == Format.ROUND_ROBIN:
        if champ:
            lines.append(f"優勝: {state.name_of(champ)}")
        elif standings.is_finished(state):
            lines.append("全試合終了。同率首位のため優勝者は未確定です。")
        for row in standings.league_table(state):
            name = state.name_of(row["player_id"])
            lines.append(f"{row['rank']}位 {name} "
                         f"{row['wins']}勝{row['losses']}敗 "
                         f"得失差{row['diff']:+d}")
        return lines

    if standings.is_finished(state):
        lines.append("大会終了。確定順位:")
        for place, pid in standings.final_placements(state):
            lines.append(f"{place}位 {state.name_of(pid)}")
        return lines

    groups = standings.elimination_groups(state)
    eliminated: set[str] = set()
    for _ph, _rn, pids in groups:
        eliminated.update(pids)
    alive = [p.name for p in state.players if p.player_id not in eliminated]
    if alive:
        lines.append("勝ち残り: " + "、".join(alive))
    for ph, rn, pids in groups:
        label = stage_label(state, ph, rn)
        names = "、".join(state.name_of(pid) for pid in pids)
        lines.append(f"{label}で敗退: {names}")
    if not groups:
        lines.append("敗退者はまだいません。")
    return lines
