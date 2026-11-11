"""総当たりリーグ戦の対戦カード生成(サークル法)。

奇数人数の場合は空文字のダミーを追加して「休み」を作り、
休みが絡む組み合わせは試合として生成しない。
"""
from __future__ import annotations

from .models import MIN_PLAYERS, Config, Match, Phase, Player, Slot, SourceType


def build_round_robin(ordered_players: list[Player], config: Config) -> list[Match]:
    n = len(ordered_players)
    if n < MIN_PLAYERS:
        # 利用者向けの人数チェックは engine.check_player_count が先に行う
        raise ValueError(f"a round robin needs at least {MIN_PLAYERS} players")

    work: list[str] = [p.player_id for p in ordered_players]
    if len(work) % 2 == 1:
        work.append("")  # 空文字 = 休み

    half = len(work) // 2
    total_rounds = len(work) - 1
    matches: list[Match] = []

    for r in range(1, total_rounds + 1):
        idx = 1
        for i in range(half):
            a = work[i]
            b = work[len(work) - 1 - i]
            if a and b:
                matches.append(Match(
                    match_id=f"RR{r}-{idx}", phase=Phase.LEAGUE, round_no=r,
                    index_in_round=idx,
                    slot1=Slot(SourceType.PLAYER, a, a),
                    slot2=Slot(SourceType.PLAYER, b, b),
                    best_of=config.best_of_default,
                ))
                idx += 1
        # 先頭を固定して残りを回転
        work = [work[0]] + [work[-1]] + work[1:-1]

    return matches
