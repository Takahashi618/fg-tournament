"""ダブルエリミネーションのブラケット生成。

ルーザーズ側は lb_template() が人数(2の累乗の枠数)から組み立てる。
- 奇数回(minor): 前の回の勝者どうしが隣り合わせで対戦する
- 偶数回(major): 前の回の勝者に、ウィナーズの敗者が合流する
major 回ではウィナーズ敗者の並びを入れ替え、すでに対戦した相手との再戦を
構造的に減らす(格ゲー大会の標準交差パターン)。
- 最初の major 回は逆順(1回戦の相手との即再戦が起きない)
- 2回目以降の major 回は、その回の試合数で決める
  2試合: 逆順 / 4試合: 逆順にしてから前半と後半を入れ替え / 8試合: 前半と後半を入れ替え
  (32人・64人用。乱数で結果を決めたシミュレーションで再戦が最も少なかった組み合わせ)
4・8・16人の枠では、以前の固定テンプレートと同じ配置になる
(保存した大会を読み込んだときに同じブラケットが再現される)。
"""
from __future__ import annotations

import dataclasses

from .bracket_single import bracket_size, build_single_elim, round_count, wire_routes
from .models import MIN_PLAYERS_DOUBLE, Config, Match, Phase, Player, Slot, SourceType

# ("V", 試合ID) = その試合の勝者 / ("L", 試合ID) = その試合の敗者
_Pair = tuple[tuple[str, str], tuple[str, str]]


def _reverse(order: list[int]) -> list[int]:
    return order[::-1]


def _swap_halves(order: list[int]) -> list[int]:
    h = len(order) // 2
    return order[h:] + order[:h]


def _reverse_swap_halves(order: list[int]) -> list[int]:
    return _swap_halves(_reverse(order))


# 2回目以降の major 回の並べ替え(キーはその回の試合数)
_LATER_MAJOR_ORDER = {
    2: _reverse,
    4: _reverse_swap_halves,
    8: _swap_halves,
}


def _drop_order(count: int, major_index: int) -> list[int]:
    """major 回でウィナーズ敗者を落とす順(ウィナーズの試合番号)。
    major_index は0始まり。count はその回の試合数。"""
    order = list(range(1, count + 1))
    if major_index == 0:
        return _reverse(order)
    return _LATER_MAJOR_ORDER.get(count, _reverse)(order)


def lb_template(size: int) -> list[list[_Pair]]:
    """ルーザーズ側の各回の対戦(枠数 size は4以上の2の累乗)。
    major_index 回目(0始まり)の major 回には、ウィナーズ (major_index + 2) 回戦の敗者が合流する。"""
    if size < 4 or size & (size - 1):
        # 2人枠では1回戦が空になり、下のループが終わらない
        raise ValueError(f"losers bracket needs a power-of-two size of 4 or more: {size}")
    rounds: list[list[_Pair]] = [[
        (("L", f"W1-{2 * i - 1}"), ("L", f"W1-{2 * i}"))
        for i in range(1, size // 4 + 1)
    ]]
    major_index = 0
    while True:
        count = len(rounds[-1])
        prev = len(rounds)
        drops = _drop_order(count, major_index)
        rounds.append([
            (("V", f"L{prev}-{i}"), ("L", f"W{major_index + 2}-{d}"))
            for i, d in enumerate(drops, start=1)
        ])
        if count == 1:
            return rounds
        major_index += 1
        prev = len(rounds)
        rounds.append([
            (("V", f"L{prev}-{2 * i - 1}"), ("V", f"L{prev}-{2 * i}"))
            for i in range(1, count // 2 + 1)
        ])


def _slot_from(spec: tuple[str, str]) -> Slot:
    kind, mid = spec
    if kind == "V":
        return Slot(SourceType.WINNER_OF, mid)
    return Slot(SourceType.LOSER_OF, mid)


def build_double_elim(ordered_players: list[Player], config: Config) -> list[Match]:
    n = len(ordered_players)
    if n < MIN_PLAYERS_DOUBLE:
        # 利用者向けの人数チェックは engine.check_player_count が先に行う
        raise ValueError(
            f"double elimination needs at least {MIN_PLAYERS_DOUBLE} players")
    size = bracket_size(n)
    k = round_count(size)

    # ウィナーズ側はシングルエリミと同構造(3位決定戦なし)
    cfg = dataclasses.replace(config, third_place=False)
    matches = build_single_elim(ordered_players, cfg)

    # ルーザーズ側をテンプレートから生成
    template = lb_template(size)
    lb_last = len(template)
    for r, pairs in enumerate(template, start=1):
        best = config.bo_lf() if r == lb_last else config.best_of_default
        for i, (s1, s2) in enumerate(pairs, start=1):
            matches.append(Match(
                match_id=f"L{r}-{i}", phase=Phase.LOSERS, round_no=r,
                index_in_round=i,
                slot1=_slot_from(s1), slot2=_slot_from(s2),
                best_of=best,
            ))

    # グランドファイナル
    matches.append(Match(
        match_id="GF", phase=Phase.GRAND_FINAL, round_no=1, index_in_round=1,
        slot1=Slot(SourceType.WINNER_OF, f"W{k}-1"),
        slot2=Slot(SourceType.WINNER_OF, f"L{lb_last}-1"),
        best_of=config.bo_gf(),
    ))

    # リセット戦。スロットは engine 側が GF の結果に応じて割り当てる。
    # slot1 = ウィナーズ側から来た選手、slot2 = ルーザーズ側から来た選手。
    if config.gf_reset:
        matches.append(Match(
            match_id="GFR", phase=Phase.GRAND_FINAL_RESET, round_no=1,
            index_in_round=1,
            slot1=Slot(SourceType.PLAYER, ""),
            slot2=Slot(SourceType.PLAYER, ""),
            best_of=config.bo_gf(),
        ))

    wire_routes(matches)
    return matches
