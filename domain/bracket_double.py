"""ダブルエリミネーションのブラケット生成。

敗者の落下先は 4/8/16 人用テンプレートで固定する。
major ラウンド(ウィナーズ敗者が合流する回)ではウィナーズ敗者を逆順に配置し、
1回戦の対戦相手との即再戦を構造的に避ける(格ゲー大会の標準交差パターン)。
"""
from __future__ import annotations

import dataclasses

from .bracket_single import bracket_size, build_single_elim, round_count, wire_routes
from .models import Config, Match, Phase, Player, Slot, SourceType

# ("V", 試合ID) = その試合の勝者 / ("L", 試合ID) = その試合の敗者
_Pair = tuple[tuple[str, str], tuple[str, str]]

LB_TEMPLATES: dict[int, list[list[_Pair]]] = {
    4: [
        [(("L", "W1-1"), ("L", "W1-2"))],
        [(("V", "L1-1"), ("L", "W2-1"))],
    ],
    8: [
        [(("L", "W1-1"), ("L", "W1-2")), (("L", "W1-3"), ("L", "W1-4"))],
        [(("V", "L1-1"), ("L", "W2-2")), (("V", "L1-2"), ("L", "W2-1"))],
        [(("V", "L2-1"), ("V", "L2-2"))],
        [(("V", "L3-1"), ("L", "W3-1"))],
    ],
    16: [
        [(("L", "W1-1"), ("L", "W1-2")), (("L", "W1-3"), ("L", "W1-4")),
         (("L", "W1-5"), ("L", "W1-6")), (("L", "W1-7"), ("L", "W1-8"))],
        [(("V", "L1-1"), ("L", "W2-4")), (("V", "L1-2"), ("L", "W2-3")),
         (("V", "L1-3"), ("L", "W2-2")), (("V", "L1-4"), ("L", "W2-1"))],
        [(("V", "L2-1"), ("V", "L2-2")), (("V", "L2-3"), ("V", "L2-4"))],
        [(("V", "L3-1"), ("L", "W3-2")), (("V", "L3-2"), ("L", "W3-1"))],
        [(("V", "L4-1"), ("V", "L4-2"))],
        [(("V", "L5-1"), ("L", "W4-1"))],
    ],
}


def _slot_from(spec: tuple[str, str]) -> Slot:
    kind, mid = spec
    if kind == "V":
        return Slot(SourceType.WINNER_OF, mid)
    return Slot(SourceType.LOSER_OF, mid)


def build_double_elim(ordered_players: list[Player], config: Config) -> list[Match]:
    n = len(ordered_players)
    if n < 3:
        raise ValueError("ダブルエリミネーションは3人以上が必要です")
    size = bracket_size(n)
    k = round_count(size)

    # ウィナーズ側はシングルエリミと同構造(3位決定戦なし)
    cfg = dataclasses.replace(config, third_place=False)
    matches = build_single_elim(ordered_players, cfg)

    # ルーザーズ側をテンプレートから生成
    template = LB_TEMPLATES[size]
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
