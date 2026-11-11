"""大会エンジン。操作ログ(actions)が唯一の真実。状態は常にログの再生で導出する。

- undo = 末尾の report または edit を取り除いて再生
- 過去試合の修正 = edit としてログに足す。再生のときに、勝者が同じなら元の report を
  差し替え、勝者が変わるなら下流(winner_to / loser_to の推移閉包)の report を外して
  新しい結果を末尾に置く。修正もログに残るので、undo で修正の前へ戻せる
- 並び順は set_order として記録する
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import standings
from .bracket_double import build_double_elim
from .bracket_single import build_single_elim
from .models import (Config, Format, Match, MatchResult, MatchState, Player,
                     SourceType, TournamentState, bracket_sort_key,
                     play_order_key, winners_rounds)
from .round_robin import build_round_robin

FORMAT_VERSION = 1


class EngineError(Exception):
    """操作エラー。メッセージはそのまま読み上げに使える日本語にする。"""


@dataclass
class Action:
    kind: str
    payload: dict


class TournamentEngine:
    def __init__(self) -> None:
        self.actions: list[Action] = []
        self.state = TournamentState(Config())

    # ---------- 操作(すべてログ追記 + 再構築) ----------

    def setup(self, config: Config) -> None:
        payload = {
            "title": config.title,
            "format": config.format.value,
            "best_of_default": config.best_of_default,
            "best_of_finals": config.best_of_finals,
            "gf_reset": config.gf_reset,
            "third_place": config.third_place,
            "best_of_wf": config.best_of_wf,
            "best_of_lf": config.best_of_lf,
            "best_of_gf": config.best_of_gf,
            "best_of_3p": config.best_of_3p,
        }
        self._commit(self.actions + [Action("setup", payload)])

    def register_players(self, names: list[str]) -> None:
        cleaned = [n.strip() for n in names]
        if any(not n for n in cleaned):
            raise EngineError("空の選手名があります")
        if len(cleaned) != len(set(cleaned)):
            raise EngineError("選手名が重複しています")
        if not (2 <= len(cleaned) <= 16):
            raise EngineError("選手は2人から16人までです")
        self._commit(self.actions + [Action("register_players",
                                            {"names": cleaned})])

    def set_order(self, ordered_ids: list[str]) -> None:
        current = {p.player_id for p in self.state.players}
        if len(ordered_ids) != len(current) or set(ordered_ids) != current:
            raise EngineError("並び順の指定が選手一覧と一致しません")
        self._commit(self.actions + [Action("set_order",
                                            {"order": list(ordered_ids)})])

    def generate(self) -> None:
        self._commit(self.actions + [Action("generate", {})])

    def report(self, match_id: str, winner_slot: int,
               score1: int = 0, score2: int = 0,
               forfeit: bool = False) -> None:
        payload = {"match_id": match_id, "winner_slot": winner_slot,
                   "score1": score1, "score2": score2, "forfeit": forfeit}
        self._commit(self.actions + [Action("report", payload)])

    def can_undo(self) -> bool:
        return bool(self.actions) and self.actions[-1].kind in ("report", "edit")

    def last_undo_kind(self) -> Optional[str]:
        """次の undo が取り消す操作の種類("report" か "edit")。無ければ None。"""
        return self.actions[-1].kind if self.can_undo() else None

    def undo_last(self) -> str:
        """直近の結果入力または修正を取り消し、対象の試合IDを返す。"""
        if not self.can_undo():
            raise EngineError("取り消せる結果入力がありません")
        mid = self.actions[-1].payload["match_id"]
        self._commit(self.actions[:-1])
        return mid

    def preview_edit_impact(self, match_id: str) -> list[str]:
        """match_id の結果を修正した場合にクリアされうる下流試合のID一覧。
        勝者が変わらない修正(スコア訂正)の場合、実際にはクリアされない。"""
        if match_id not in self.state.matches:
            raise EngineError("試合が見つかりません")
        affected = self._descendants(match_id)
        out: list[str] = []
        for mid in self.state.match_order:
            if mid in affected:
                dm = self.state.matches[mid]
                if dm.result is not None and not dm.result.auto_bye:
                    out.append(mid)
        return out

    def edit_result(self, match_id: str, winner_slot: int,
                    score1: int = 0, score2: int = 0,
                    forfeit: bool = False) -> list[str]:
        """過去試合の結果を修正する。クリアされた下流試合のID一覧を返す。
        勝者が変わらない場合はスコアの差し替えのみ(下流はそのまま)。"""
        m = self.state.matches.get(match_id)
        if m is None:
            raise EngineError("試合が見つかりません")
        if m.result is None or m.result.auto_bye:
            raise EngineError("この試合には修正できる結果がありません")

        new_payload = {"match_id": match_id, "winner_slot": winner_slot,
                       "score1": score1, "score2": score2, "forfeit": forfeit}

        # 勝者が同じならスコアの差し替えだけで、下流には影響しない
        cleared = ([] if m.result.winner_slot == int(winner_slot)
                   else self.preview_edit_impact(match_id))
        self._commit(self.actions + [Action("edit", new_payload)])
        return cleared

    # ---------- 参照 ----------

    def match(self, match_id: str) -> Match:
        m = self.state.matches.get(match_id)
        if m is None:
            raise EngineError("試合が見つかりません")
        return m

    def matches_bracket_order(self) -> list[Match]:
        return sorted(self.state.matches.values(), key=bracket_sort_key)

    def matches_play_order(self) -> list[Match]:
        k = winners_rounds(self.state)
        return sorted(self.state.matches.values(),
                      key=lambda m: play_order_key(m, k))

    def ready_matches(self) -> list[Match]:
        return [m for m in self.matches_play_order()
                if m.state() == MatchState.READY]

    def champion_id(self) -> Optional[str]:
        return standings.champion_id(self.state)

    def is_finished(self) -> bool:
        return standings.is_finished(self.state)

    # ---------- 保存・読み込み ----------

    def to_dict(self) -> dict:
        return {"format_version": FORMAT_VERSION,
                "actions": [{"kind": a.kind, "payload": dict(a.payload)}
                            for a in self.actions]}

    @classmethod
    def from_dict(cls, data: dict) -> "TournamentEngine":
        if not isinstance(data, dict):
            raise EngineError("大会ファイルではありません")
        if data.get("format_version") != FORMAT_VERSION:
            raise EngineError("保存データの形式バージョンが対応外です")
        eng = cls()
        try:
            eng._commit([Action(d["kind"], dict(d["payload"]))
                         for d in data.get("actions", [])])
        except EngineError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            raise EngineError("大会ファイルの内容が壊れています") from None
        return eng

    def save(self, path) -> None:
        """一時ファイルに書いてから置き換える(書いている途中で落ちても元が残る)。"""
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path) -> "TournamentEngine":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError:  # JSON でない、UTF-8 でない
            raise EngineError("大会ファイルとして読めません") from None
        return cls.from_dict(data)

    # ---------- 内部 ----------

    def _commit(self, new_actions: list[Action]) -> None:
        """アクション列を差し替えて再構築。失敗時は元に戻す。"""
        backup = self.actions
        try:
            self.actions = new_actions
            self._rebuild()
        except Exception:
            self.actions = backup
            self._rebuild()
            raise

    def _rebuild(self) -> None:
        st = TournamentState(Config())
        for act in self._effective(self.actions):
            self._apply(st, act)
        self.state = st

    def _effective(self, actions: list[Action]) -> list[Action]:
        """edit を畳み込んだ、再生用のアクション列を返す。"""
        if not any(a.kind == "edit" for a in actions):
            return actions
        # 配線(下流の判定)は結果に依らないので、結果以外の操作だけで組む
        base = TournamentState(Config())
        for act in actions:
            if act.kind not in ("report", "edit"):
                self._apply(base, act)
        out: list[Action] = []
        for act in actions:
            if act.kind != "edit":
                out.append(act)
                continue
            mid = act.payload["match_id"]
            idx = next((i for i, a in enumerate(out)
                        if a.kind == "report"
                        and a.payload["match_id"] == mid), None)
            if idx is None:
                raise EngineError("この試合には修正できる結果がありません")
            new = Action("report", dict(act.payload))
            if (int(out[idx].payload["winner_slot"])
                    == int(act.payload["winner_slot"])):
                out[idx] = new
            else:
                remove = self._descendants(mid, base) | {mid}
                out = [a for a in out
                       if not (a.kind == "report"
                               and a.payload["match_id"] in remove)]
                out.append(new)
        return out

    def _apply(self, st: TournamentState, act: Action) -> None:
        kind, p = act.kind, act.payload
        if kind == "setup":
            if st.generated:
                raise EngineError("生成後に設定は変更できません")

            def _bo(v) -> int:
                n = int(v)
                if n < 1 or n % 2 == 0:
                    raise EngineError("セット数の設定が不正です")
                return n

            def _opt(key: str):
                v = p.get(key)
                return _bo(v) if v is not None else None

            st.config = Config(
                title=p["title"], format=Format(p["format"]),
                best_of_default=_bo(p["best_of_default"]),
                best_of_finals=_bo(p["best_of_finals"]),
                gf_reset=bool(p["gf_reset"]),
                third_place=bool(p["third_place"]),
                best_of_wf=_opt("best_of_wf"),
                best_of_lf=_opt("best_of_lf"),
                best_of_gf=_opt("best_of_gf"),
                best_of_3p=_opt("best_of_3p"))
        elif kind == "register_players":
            if st.generated:
                raise EngineError("生成後に選手は変更できません")
            st.players = [Player(f"p{i + 1}", name, i + 1)
                          for i, name in enumerate(p["names"])]
        elif kind == "set_order":
            if st.generated:
                raise EngineError("生成後に並び順は変更できません")
            pmap = {pl.player_id: pl for pl in st.players}
            st.players = [pmap[pid] for pid in p["order"]]
            for i, pl in enumerate(st.players):
                pl.seed = i + 1
        elif kind == "generate":
            if st.generated:
                raise EngineError("ブラケットは生成済みです")
            fmt = st.config.format
            if not (2 <= len(st.players) <= 16):
                raise EngineError("選手は2人から16人までです")
            try:
                if fmt == Format.SINGLE_ELIM:
                    ms = build_single_elim(st.players, st.config)
                elif fmt == Format.DOUBLE_ELIM:
                    ms = build_double_elim(st.players, st.config)
                else:
                    ms = build_round_robin(st.players, st.config)
            except ValueError as exc:
                # 人数が形式に足りないとき。そのまま読み上げに使える文にする
                raise EngineError(str(exc)) from None
            st.matches = {m.match_id: m for m in ms}
            st.match_order = [m.match_id for m in ms]
            st.generated = True
            self._propagate(st)
        elif kind == "report":
            if not st.generated:
                raise EngineError("ブラケットが生成されていません")
            m = st.matches.get(p["match_id"])
            if m is None:
                raise EngineError("試合が見つかりません")
            if m.state() != MatchState.READY:
                raise EngineError("この試合はまだ対戦できる状態ではありません")
            ws = int(p["winner_slot"])
            if ws not in (1, 2):
                raise EngineError("勝者スロットの指定が不正です")
            if p.get("forfeit"):
                m.result = MatchResult(ws, 0, 0, forfeit=True)
            else:
                s1, s2 = int(p["score1"]), int(p["score2"])
                needed = m.best_of // 2 + 1
                win_score = s1 if ws == 1 else s2
                lose_score = s2 if ws == 1 else s1
                if win_score != needed or not (0 <= lose_score < needed):
                    raise EngineError(f"スコアがBO{m.best_of}として不正です")
                m.result = MatchResult(ws, s1, s2)
            self._propagate(st)
        else:
            raise EngineError(f"未知の操作です: {kind}")

    def _propagate(self, st: TournamentState) -> None:
        """スロット解決・BYE自動消化・リセット戦制御を固定点まで繰り返す。"""
        changed = True
        while changed:
            changed = False
            for mid in st.match_order:
                m = st.matches[mid]
                for slot in (m.slot1, m.slot2):
                    if (slot.resolved_id is None
                            and slot.source_type in (SourceType.WINNER_OF,
                                                     SourceType.LOSER_OF)):
                        src = st.matches.get(slot.source_id)
                        if src is not None and src.result is not None:
                            rid = (src.winner_id()
                                   if slot.source_type == SourceType.WINNER_OF
                                   else src.loser_id())
                            if rid is not None:
                                slot.resolved_id = rid
                                changed = True
                if (m.result is None and not m.skipped
                        and m.slot1.is_resolved() and m.slot2.is_resolved()):
                    b1, b2 = m.slot1.is_bye(), m.slot2.is_bye()
                    if b1 or b2:
                        # BYEの自動消化(両方BYEなら slot1 側が勝ち上がる)
                        winner_slot = 2 if (b1 and not b2) else 1
                        m.result = MatchResult(winner_slot, 0, 0,
                                               auto_bye=True)
                        changed = True
            # リセット戦の制御
            gf = st.matches.get("GF")
            gfr = st.matches.get("GFR")
            if gf is not None and gfr is not None and gf.result is not None:
                if gf.result.winner_slot == 2:
                    if gfr.skipped or gfr.slot1.resolved_id is None:
                        gfr.skipped = False
                        gfr.slot1.resolved_id = gf.slot1.resolved_id
                        gfr.slot2.resolved_id = gf.slot2.resolved_id
                        changed = True
                else:
                    if not gfr.skipped:
                        gfr.skipped = True
                        changed = True

    def _descendants(self, match_id: str,
                     st: Optional[TournamentState] = None) -> set[str]:
        """winner_to / loser_to の推移閉包(自身は含まない)。GF なら GFR も含む。"""
        st = st or self.state
        out: set[str] = set()
        stack = [match_id]
        while stack:
            cur = st.matches.get(stack.pop())
            if cur is None:
                continue
            for dest in (cur.winner_to, cur.loser_to):
                if dest is not None and dest[0] not in out:
                    out.add(dest[0])
                    stack.append(dest[0])
        if "GFR" in st.matches and (match_id == "GF" or "GF" in out):
            out.add("GFR")
        out.discard(match_id)
        return out
