"""シングルエリミネーションのブラケット生成。

ダブルエリミネーションのウィナーズ側もこのモジュールで生成する。
winner_to / loser_to の配線は wire_routes() が各スロットの参照元から自動で張る。
"""
from __future__ import annotations

from .models import BYE_ID, MIN_PLAYERS, Config, Match, Phase, Player, Slot, SourceType


def bracket_size(n_players: int) -> int:
    """人数以上で最小の2の累乗(最小2)。"""
    size = 2
    while size < n_players:
        size *= 2
    return size


def round_count(size: int) -> int:
    k = 0
    while (1 << k) < size:
        k += 1
    return k


def seed_slots(size: int) -> list[int]:
    """標準シード配置。ブラケット位置順にシード番号を返す。
    例: size=8 -> [1, 8, 4, 5, 2, 7, 3, 6]"""
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        nxt: list[int] = []
        for s in order:
            nxt.append(s)
            nxt.append(n + 1 - s)
        order = nxt
    return order


def _slot_for_seed(seed: int, ordered_players: list[Player]) -> Slot:
    if seed <= len(ordered_players):
        p = ordered_players[seed - 1]
        return Slot(SourceType.PLAYER, p.player_id, p.player_id)
    return Slot(SourceType.BYE, "", BYE_ID)


def wire_routes(matches: list[Match]) -> None:
    """各試合のスロット参照(WINNER_OF/LOSER_OF)から winner_to / loser_to を張る。"""
    by_id = {m.match_id: m for m in matches}
    for m in matches:
        for slot_no, slot in ((1, m.slot1), (2, m.slot2)):
            if slot.source_type == SourceType.WINNER_OF:
                by_id[slot.source_id].winner_to = (m.match_id, slot_no)
            elif slot.source_type == SourceType.LOSER_OF:
                by_id[slot.source_id].loser_to = (m.match_id, slot_no)


def build_single_elim(ordered_players: list[Player], config: Config) -> list[Match]:
    """ordered_players はシード順(1位が先頭)。"""
    n = len(ordered_players)
    if n < MIN_PLAYERS:
        # 利用者向けの人数チェックは engine.check_player_count が先に行う
        raise ValueError(f"single elimination needs at least {MIN_PLAYERS} players")
    size = bracket_size(n)
    k = round_count(size)
    positions = seed_slots(size)
    matches: list[Match] = []

    # 1回戦
    for i in range(size // 2):
        matches.append(Match(
            match_id=f"W1-{i + 1}", phase=Phase.WINNERS, round_no=1,
            index_in_round=i + 1,
            slot1=_slot_for_seed(positions[2 * i], ordered_players),
            slot2=_slot_for_seed(positions[2 * i + 1], ordered_players),
            best_of=config.bo_wf() if k == 1 else config.best_of_default,
        ))

    # 2回戦以降
    for r in range(2, k + 1):
        best = config.bo_wf() if r == k else config.best_of_default
        for i in range(size // (2 ** r)):
            matches.append(Match(
                match_id=f"W{r}-{i + 1}", phase=Phase.WINNERS, round_no=r,
                index_in_round=i + 1,
                slot1=Slot(SourceType.WINNER_OF, f"W{r - 1}-{2 * i + 1}"),
                slot2=Slot(SourceType.WINNER_OF, f"W{r - 1}-{2 * i + 2}"),
                best_of=best,
            ))

    # 3位決定戦(準決勝の敗者同士)
    if config.third_place and k >= 2:
        matches.append(Match(
            match_id="3P", phase=Phase.THIRD_PLACE, round_no=1, index_in_round=1,
            slot1=Slot(SourceType.LOSER_OF, f"W{k - 1}-1"),
            slot2=Slot(SourceType.LOSER_OF, f"W{k - 1}-2"),
            best_of=config.bo_3p(),
        ))

    wire_routes(matches)
    return matches
