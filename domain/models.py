"""ドメインモデル定義。UI・音声・ネットワークに依存しない純粋なデータ構造。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Format(Enum):
    SINGLE_ELIM = "single_elim"
    DOUBLE_ELIM = "double_elim"
    ROUND_ROBIN = "round_robin"


class Phase(Enum):
    WINNERS = "winners"
    LOSERS = "losers"
    GRAND_FINAL = "grand_final"
    GRAND_FINAL_RESET = "grand_final_reset"
    THIRD_PLACE = "third_place"
    LEAGUE = "league"


class MatchState(Enum):
    PENDING = "pending"    # 対戦者が確定していない
    READY = "ready"        # 対戦可能
    DONE = "done"          # 終了
    SKIPPED = "skipped"    # 実施なし(リセット戦が発生しない場合など)


class SourceType(Enum):
    PLAYER = "player"      # 選手を直接指定
    WINNER_OF = "winner"   # 指定試合の勝者
    LOSER_OF = "loser"     # 指定試合の敗者
    BYE = "bye"            # 不戦(シード)


BYE_ID = "__BYE__"


@dataclass
class Player:
    player_id: str
    name: str
    seed: int = 0  # 1始まり。0は未設定


@dataclass
class Slot:
    source_type: SourceType
    source_id: str = ""                 # PLAYER: player_id / WINNER_OF, LOSER_OF: match_id
    resolved_id: Optional[str] = None   # 確定した player_id または BYE_ID

    def is_resolved(self) -> bool:
        return self.resolved_id is not None

    def is_bye(self) -> bool:
        return self.resolved_id == BYE_ID


@dataclass
class MatchResult:
    winner_slot: int          # 1 または 2
    score1: int = 0
    score2: int = 0
    forfeit: bool = False     # 不戦勝(棄権・欠席)
    auto_bye: bool = False    # BYE による自動消化


@dataclass
class Match:
    match_id: str
    phase: Phase
    round_no: int
    index_in_round: int
    slot1: Slot
    slot2: Slot
    best_of: int = 3
    result: Optional[MatchResult] = None
    winner_to: Optional[tuple[str, int]] = None  # (match_id, slot番号 1/2)
    loser_to: Optional[tuple[str, int]] = None
    skipped: bool = False

    def state(self) -> MatchState:
        if self.skipped:
            return MatchState.SKIPPED
        if self.result is not None:
            return MatchState.DONE
        if self.slot1.is_resolved() and self.slot2.is_resolved():
            return MatchState.READY
        return MatchState.PENDING

    def winner_id(self) -> Optional[str]:
        if self.result is None:
            return None
        return self.slot1.resolved_id if self.result.winner_slot == 1 else self.slot2.resolved_id

    def loser_id(self) -> Optional[str]:
        if self.result is None:
            return None
        return self.slot2.resolved_id if self.result.winner_slot == 1 else self.slot1.resolved_id

    def players(self) -> tuple[Optional[str], Optional[str]]:
        return (self.slot1.resolved_id, self.slot2.resolved_id)


@dataclass
class Config:
    title: str = "大会"
    format: Format = Format.DOUBLE_ELIM
    best_of_default: int = 3
    best_of_finals: int = 5     # 決勝系の既定(個別指定が無いときのフォールバック)
    gf_reset: bool = True       # グランドファイナルのリセット戦あり
    third_place: bool = False   # シングルエリミの3位決定戦
    # 決勝系の個別セット数(None なら best_of_finals を使う)
    best_of_wf: Optional[int] = None   # 決勝(ウィナーズ決勝)
    best_of_lf: Optional[int] = None   # ルーザーズ決勝
    best_of_gf: Optional[int] = None   # グランドファイナル(リセット戦も同じ)
    best_of_3p: Optional[int] = None   # 3位決定戦

    def bo_wf(self) -> int:
        return self.best_of_wf or self.best_of_finals

    def bo_lf(self) -> int:
        return self.best_of_lf or self.best_of_finals

    def bo_gf(self) -> int:
        return self.best_of_gf or self.best_of_finals

    def bo_3p(self) -> int:
        return self.best_of_3p or self.best_of_finals


@dataclass
class TournamentState:
    config: Config
    players: list[Player] = field(default_factory=list)
    matches: dict[str, Match] = field(default_factory=dict)
    match_order: list[str] = field(default_factory=list)
    generated: bool = False

    def player(self, player_id: str) -> Optional[Player]:
        for p in self.players:
            if p.player_id == player_id:
                return p
        return None

    def name_of(self, player_id: Optional[str]) -> str:
        if player_id is None:
            return "未定"
        if player_id == BYE_ID:
            return "不戦"
        p = self.player(player_id)
        return p.name if p else player_id

    def real_ids(self) -> set[str]:
        return {p.player_id for p in self.players}


def winners_rounds(state: TournamentState) -> int:
    """ウィナーズ(またはシングルエリミ本戦)の最終ラウンド番号。"""
    rounds = [m.round_no for m in state.matches.values() if m.phase == Phase.WINNERS]
    return max(rounds) if rounds else 0


def losers_rounds(state: TournamentState) -> int:
    rounds = [m.round_no for m in state.matches.values() if m.phase == Phase.LOSERS]
    return max(rounds) if rounds else 0


def play_order_key(m: Match, k: int) -> tuple:
    """消化順のソートキー。k はウィナーズのラウンド数。
    ダブルエリミでは W1, L1, W2, L2, L3, W3, L4, L5, ... GF, GFR の順になる。"""
    if m.phase == Phase.WINNERS:
        base = 10 if m.round_no == 1 else 30 * (m.round_no - 1)
    elif m.phase == Phase.LOSERS:
        r = m.round_no
        if r == 1:
            base = 20
        elif r % 2 == 0:
            base = 30 * (r // 2) + 10
        else:
            base = 30 * ((r - 1) // 2) + 20
    elif m.phase == Phase.THIRD_PLACE:
        base = 30 * (k - 1) - 5  # 決勝の直前
    elif m.phase == Phase.GRAND_FINAL:
        base = 100000
    elif m.phase == Phase.GRAND_FINAL_RESET:
        base = 100010
    else:  # LEAGUE
        base = 10 * m.round_no
    return (base, m.index_in_round)


def bracket_sort_key(m: Match) -> tuple:
    """ブラケット表示順(ウィナーズ → 3位決定戦 → ルーザーズ → GF → GFR)。"""
    rank = {
        Phase.WINNERS: 0,
        Phase.LEAGUE: 0,
        Phase.THIRD_PLACE: 1,
        Phase.LOSERS: 2,
        Phase.GRAND_FINAL: 3,
        Phase.GRAND_FINAL_RESET: 4,
    }[m.phase]
    return (rank, m.round_no, m.index_in_round)
