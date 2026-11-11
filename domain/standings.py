"""順位計算。models のみに依存する(engine から呼ばれるため循環を避ける)。"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .models import (Format, Phase, TournamentState, play_order_key,
                     winners_rounds)


def is_finished(state: TournamentState) -> bool:
    if not state.generated:
        return False
    return all(m.result is not None
               for m in state.matches.values() if not m.skipped)


def champion_id(state: TournamentState) -> Optional[str]:
    if not state.generated:
        return None
    fmt = state.config.format
    ms = state.matches
    if fmt == Format.ROUND_ROBIN:
        if not is_finished(state):
            return None
        table = league_table(state)
        tops = [row for row in table if row["rank"] == 1]
        return tops[0]["player_id"] if len(tops) == 1 else None
    if fmt == Format.SINGLE_ELIM:
        k = winners_rounds(state)
        final = ms.get(f"W{k}-1")
        return final.winner_id() if final and final.result else None
    # ダブルエリミネーション
    gf = ms.get("GF")
    if gf is None or gf.result is None:
        return None
    if gf.result.winner_slot == 1 or not state.config.gf_reset:
        return gf.winner_id()
    gfr = ms.get("GFR")
    if gfr is not None and gfr.result is not None:
        return gfr.winner_id()
    return None


def league_table(state: TournamentState) -> list[dict]:
    """リーグ順位表。勝ち数 → 同勝ち数グループ内の直接対決勝ち数 → ゲーム得失差。
    それでも並べば同順位(競技ランキング 1,1,3 形式)。表示順はシードで安定化。"""
    stats = {p.player_id: {"wins": 0, "losses": 0, "gw": 0, "gl": 0}
             for p in state.players}
    done = [m for m in state.matches.values()
            if m.phase == Phase.LEAGUE and m.result is not None]
    for m in done:
        w, lo = m.winner_id(), m.loser_id()
        if w in stats:
            stats[w]["wins"] += 1
        if lo in stats:
            stats[lo]["losses"] += 1
        a, b = m.slot1.resolved_id, m.slot2.resolved_id
        if a in stats:
            stats[a]["gw"] += m.result.score1
            stats[a]["gl"] += m.result.score2
        if b in stats:
            stats[b]["gw"] += m.result.score2
            stats[b]["gl"] += m.result.score1

    groups: dict[int, list[str]] = defaultdict(list)
    for pid, st in stats.items():
        groups[st["wins"]].append(pid)

    keymap: dict[str, tuple] = {}
    for wins, members in groups.items():
        mset = set(members)
        for pid in members:
            h2h = sum(1 for m in done
                      if m.winner_id() == pid and m.loser_id() in mset)
            diff = stats[pid]["gw"] - stats[pid]["gl"]
            keymap[pid] = (-wins, -h2h, -diff)

    seed_of = {p.player_id: p.seed for p in state.players}
    ordered = sorted(stats.keys(),
                     key=lambda pid: (keymap[pid], seed_of.get(pid, 999)))

    rows: list[dict] = []
    prev_key: Optional[tuple] = None
    rank = 0
    for i, pid in enumerate(ordered):
        if keymap[pid] != prev_key:
            rank = i + 1
            prev_key = keymap[pid]
        st = stats[pid]
        rows.append({"rank": rank, "player_id": pid,
                     "wins": st["wins"], "losses": st["losses"],
                     "gw": st["gw"], "gl": st["gl"],
                     "diff": st["gw"] - st["gl"]})
    return rows


def _loss_records(state: TournamentState) -> dict[str, tuple]:
    """各選手の最終敗退記録 pid -> (play_key, phase, round_no)。
    敗退 = loser_to の無い試合での敗戦。GF敗戦→GFR敗戦のように複数あれば後のもの。
    リセット戦がまだ残っている場合のGF敗戦は敗退として扱わない。"""
    k = winners_rounds(state)
    real = state.real_ids()
    best: dict[str, tuple] = {}
    for m in state.matches.values():
        if m.result is None or m.skipped:
            continue
        loser = m.loser_id()
        if loser not in real or m.loser_to is not None:
            continue
        if m.match_id == "GF" and state.config.gf_reset:
            gfr = state.matches.get("GFR")
            if gfr is not None and not gfr.skipped and gfr.result is None:
                continue  # リセット戦待ち。まだ敗退ではない
        key = play_order_key(m, k)[0]  # ラウンド単位のステージ値(試合番号は含めない)
        cur = best.get(loser)
        if cur is None or key > cur[0]:
            best[loser] = (key, m.phase, m.round_no)
    return best


def elimination_groups(state: TournamentState) -> list[tuple[Phase, int, list[str]]]:
    """進行中でも使える敗退状況。(phase, round_no, 選手IDリスト) を敗退が遅い順に返す。
    優勝者は含まない。"""
    champ = champion_id(state)
    grouped: dict[tuple, list[str]] = defaultdict(list)
    meta: dict[tuple, tuple] = {}
    for pid, (key, ph, rn) in _loss_records(state).items():
        if pid == champ:
            continue
        grouped[key].append(pid)
        meta[key] = (ph, rn)
    seed_of = _seed_map(state)
    out: list[tuple[Phase, int, list[str]]] = []
    for key in sorted(grouped.keys(), reverse=True):
        ph, rn = meta[key]
        out.append((ph, rn, sorted(grouped[key], key=seed_of)))
    return out


def _seed_map(state: TournamentState):
    """同じ順位・同じ敗退ラウンドの中を並べるキー(並び順=シード順)。"""
    seeds = {p.player_id: p.seed for p in state.players}
    return lambda pid: seeds.get(pid, 999)


def final_placements(state: TournamentState) -> list[tuple[int, str]]:
    """確定順位 (順位, player_id) のリストを順位昇順で返す。
    エリミネーション系は大会終了時のみ全員分を返す(進行中は空リスト)。
    順位は「自分より長く残った人数 + 1」= 敗退ステージの下から累積で決まる。"""
    fmt = state.config.format
    if fmt == Format.ROUND_ROBIN:
        if not is_finished(state):
            return []
        return [(row["rank"], row["player_id"]) for row in league_table(state)]
    if not is_finished(state):
        return []

    champ = champion_id(state)
    k = winners_rounds(state)
    stage_of: dict[str, tuple] = {}
    for pid, (key, _ph, _rn) in _loss_records(state).items():
        stage_of[pid] = (key, 0)

    # 3位決定戦の勝者は敗退ではないが順位確定(敗者の1つ上のステージ扱い)
    tp = state.matches.get("3P")
    if tp is not None and tp.result is not None:
        w = tp.winner_id()
        if w in state.real_ids():
            stage_of[w] = (play_order_key(tp, k)[0], 1)

    if champ:
        stage_of.pop(champ, None)

    by_stage: dict[tuple, list[str]] = defaultdict(list)
    for pid, st in stage_of.items():
        by_stage[st].append(pid)

    total = len(state.players)
    seed_of = _seed_map(state)
    placements: list[tuple[int, str]] = []
    cum = 0
    for st in sorted(by_stage.keys()):
        members = by_stage[st]
        cum += len(members)
        place = total - cum + 1
        for pid in members:
            placements.append((place, pid))
    if champ:
        placements.append((1, champ))
    placements.sort(key=lambda t: (t[0], seed_of(t[1])))
    return placements


def real_match_counts(state: TournamentState) -> tuple[int, int]:
    """実際に対戦が行われる試合数と消化数を返す(不戦は含めない)。

    - skipped(実施なし)と、BYE絡みで自動消化される/される予定の試合は除外
    - 未発生のリセット戦は数えず、発生が確定した時点で加算される
    BYEの伝播は生成時に確定するため、この判定は将来の自動消化も正しく除外できる。
    """
    total = 0
    done = 0
    for m in state.matches.values():
        if m.skipped:
            continue
        if m.result is not None and m.result.auto_bye:
            continue
        if m.slot1.is_bye() or m.slot2.is_bye():
            continue
        if (m.phase == Phase.GRAND_FINAL_RESET
                and m.result is None
                and m.slot1.resolved_id is None):
            continue  # リセット戦は発生が確定するまで数えない
        total += 1
        if m.result is not None:
            done += 1
    return total, done
