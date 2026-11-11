"""ドメイン層テスト(直接実行式)。

実行すると tests/result_domain.txt に UTF-8 で結果を書き出す。
終了コード: 全件OKなら0、失敗があれば1。
"""
from __future__ import annotations

import json
import random
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.models import (BYE_ID, MAX_PLAYERS, Config, Format, MatchState,
                           Phase, SourceType)
from domain.bracket_double import lb_template
from domain.bracket_single import bracket_size, seed_slots
from domain.engine import EngineError, TournamentEngine
from domain import standings, textgen
import i18n

LINES: list[str] = []
TOTAL = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global TOTAL, FAIL
    TOTAL += 1
    if cond:
        LINES.append(f"OK: {label}")
    else:
        FAIL += 1
        LINES.append(f"NG: {label}")


def note(text: str) -> None:
    LINES.append(text)


def new_engine(fmt: Format, names: list[str], title: str = "テスト大会",
               **cfg) -> TournamentEngine:
    e = TournamentEngine()
    e.setup(Config(title=title, format=fmt, **cfg))
    e.register_players(names)
    e.generate()
    return e


def play_all(e: TournamentEngine, chooser=None) -> None:
    """READY の先頭から順に消化する。chooser(m) が勝者スロット(1/2)を返す。"""
    if chooser is None:
        chooser = lambda m: 1
    guard = 0
    while True:
        ready = e.ready_matches()
        if not ready:
            break
        m = ready[0]
        ws = chooser(m)
        needed = m.best_of // 2 + 1
        if ws == 1:
            e.report(m.match_id, 1, needed, 0)
        else:
            e.report(m.match_id, 2, 0, needed)
        guard += 1
        if guard > 5000:
            raise RuntimeError("play_all が収束しません")


def sig(e: TournamentEngine) -> str:
    """状態のシグネチャ(比較用)。"""
    data = {}
    for mid, m in sorted(e.state.matches.items()):
        r = None
        if m.result is not None:
            r = [m.result.winner_slot, m.result.score1, m.result.score2,
                 m.result.forfeit, m.result.auto_bye]
        data[mid] = [m.slot1.resolved_id, m.slot2.resolved_id, m.skipped, r]
    data["__champ__"] = e.champion_id()
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def slot_pool(state, slot) -> set:
    """スロットに登場しうる実選手の集合(構造のみ、結果非依存)。"""
    if slot.source_type == SourceType.PLAYER and slot.source_id:
        return {slot.source_id}
    if slot.source_type == SourceType.BYE:
        return set()
    if slot.source_type in (SourceType.WINNER_OF, SourceType.LOSER_OF):
        src = state.matches[slot.source_id]
        return slot_pool(state, src.slot1) | slot_pool(state, src.slot2)
    return set()


def names_n(n: int) -> list[str]:
    return [f"選手{i}" for i in range(1, n + 1)]


# ---------- A. シード配置 ----------
note("=== A. シード配置 ===")
check(seed_slots(2) == [1, 2], "seed_slots(2)")
check(seed_slots(4) == [1, 4, 2, 3], "seed_slots(4)")
check(seed_slots(8) == [1, 8, 4, 5, 2, 7, 3, 6], "seed_slots(8)")
check(bracket_size(3) == 4 and bracket_size(8) == 8 and bracket_size(9) == 16,
      "bracket_size")

# ---------- B. シングルエリミネーション ----------
note("=== B. シングルエリミネーション ===")
for n in range(2, MAX_PLAYERS + 1):
    e = new_engine(Format.SINGLE_ELIM, names_n(n))
    size = bracket_size(n)
    check(len(e.state.matches) == size - 1, f"単{n}人: 試合数 {size - 1}")
    play_all(e)
    check(e.is_finished(), f"単{n}人: 終了")
    check(e.champion_id() == "p1", f"単{n}人: 優勝はシード1")
    pl = standings.final_placements(e.state)
    places = [p for p, _ in pl]
    check(len(pl) == n, f"単{n}人: 順位が全員分")
    check(places.count(1) == 1, f"単{n}人: 1位が一意")

# 3位決定戦つき
e = new_engine(Format.SINGLE_ELIM, names_n(8), third_place=True)
check("3P" in e.state.matches, "単8人+3P: 3位決定戦が存在")
check(len(e.state.matches) == 8, "単8人+3P: 試合数8")
play_all(e)
pl_list = standings.final_placements(e.state)
top4 = sorted(p for p, _ in pl_list)[:4]
check(top4 == [1, 2, 3, 4], "単8人+3P: 1〜4位が一意に確定")

# ---------- C. ダブルエリミネーション ----------
note("=== C. ダブルエリミネーション ===")
for n in range(3, MAX_PLAYERS + 1):
    e = new_engine(Format.DOUBLE_ELIM, names_n(n))
    size = bracket_size(n)
    expected = 2 * size - 2 + 1  # +1 はリセット戦
    check(len(e.state.matches) == expected, f"双{n}人: 試合数 {expected}")
    w_ok = all(m.loser_to is not None
               for m in e.state.matches.values()
               if m.phase == Phase.WINNERS)
    check(w_ok, f"双{n}人: 全ウィナーズ試合に敗者の行き先")
    play_all(e)
    check(e.is_finished(), f"双{n}人: 終了")
    check(e.champion_id() == "p1", f"双{n}人: 優勝はシード1")
    gfr = e.state.matches["GFR"]
    check(gfr.skipped, f"双{n}人: リセット戦は実施なし")
    # 敗退者は全員ちょうど2敗
    losses: dict[str, int] = {}
    for m in e.state.matches.values():
        if m.result is None or m.skipped:
            continue
        lid = m.loser_id()
        if lid in e.state.real_ids():
            losses[lid] = losses.get(lid, 0) + 1
    elim = e.state.real_ids() - {"p1"}
    check(all(losses.get(pid, 0) == 2 for pid in elim),
          f"双{n}人: 敗退者は全員2敗")
    check(losses.get("p1", 0) == 0, f"双{n}人: 優勝者は無敗")
    pl_list = standings.final_placements(e.state)
    check(len(pl_list) == n, f"双{n}人: 順位が全員分")

# GFで敗者側が勝つ→リセット戦
e = new_engine(Format.DOUBLE_ELIM, names_n(8))
play_all(e, chooser=lambda m: 2 if m.match_id == "GF" else 1)
gf = e.state.matches["GF"]
gfr = e.state.matches["GFR"]
check(gf.result is not None and gf.result.winner_slot == 2,
      "双8リセット: GFは敗者側の勝ち")
check(gfr.result is not None and not gfr.skipped, "双8リセット: リセット戦実施")
check(gfr.slot1.resolved_id == gf.slot1.resolved_id
      and gfr.slot2.resolved_id == gf.slot2.resolved_id,
      "双8リセット: リセット戦の対戦者はGFと同じ")
check(e.champion_id() == gfr.winner_id(), "双8リセット: 優勝はリセット戦勝者")
check(e.champion_id() == "p1", "双8リセット: 優勝はウィナーズ側(p1)")
check(e.is_finished(), "双8リセット: 終了")
pl_list = standings.final_placements(e.state)
pl_map = {pid: p for p, pid in pl_list}
check(pl_map.get(gf.slot2.resolved_id) == 2, "双8リセット: 2位はGF敗者側選手")

# ---------- D. ルーザーズ合流の即再戦回避(構造検証) ----------
note("=== D. L2の即再戦回避 ===")
for n in (8, 16, 32, 64):
    e = new_engine(Format.DOUBLE_ELIM, names_n(n))
    st = e.state
    ok = True
    for m in st.matches.values():
        if m.phase != Phase.LOSERS or m.round_no != 2:
            continue
        # 勝ち上がり側(L1由来)と落下側(W2敗者)の候補集合が交差しないこと
        v_slot = m.slot1 if m.slot1.source_id.startswith("L") else m.slot2
        l_slot = m.slot2 if v_slot is m.slot1 else m.slot1
        if slot_pool(st, v_slot) & slot_pool(st, l_slot):
            ok = False
    check(ok, f"双{n}人: L2で1回戦の再戦が構造上起きない")

# ---------- E. 総当たりリーグ ----------
note("=== E. 総当たりリーグ ===")
e = new_engine(Format.ROUND_ROBIN, names_n(4))
check(len(e.state.matches) == 6, "リーグ4人: 6試合")
rounds = {m.round_no for m in e.state.matches.values()}
check(rounds == {1, 2, 3}, "リーグ4人: 3節")
pairs = {frozenset(m.players()) for m in e.state.matches.values()}
check(len(pairs) == 6, "リーグ4人: 全組み合わせが1回ずつ")

e = new_engine(Format.ROUND_ROBIN, names_n(5))
check(len(e.state.matches) == 10, "リーグ5人: 10試合")
appear: dict[str, int] = {}
for m in e.state.matches.values():
    for pid in m.players():
        appear[pid] = appear.get(pid, 0) + 1
check(all(appear.get(f"p{i}", 0) == 4 for i in range(1, 6)),
      "リーグ5人: 各選手4試合")


def rr_report(e: TournamentEngine, winner: str, loser: str) -> None:
    """選手IDの組で試合を見つけて 2対0 で報告する。"""
    for m in e.state.matches.values():
        if frozenset(m.players()) == frozenset((winner, loser)):
            ws = 1 if m.slot1.resolved_id == winner else 2
            if ws == 1:
                e.report(m.match_id, 1, 2, 0)
            else:
                e.report(m.match_id, 2, 0, 2)
            return
    raise RuntimeError("組み合わせが見つかりません")


# 順位: 勝ち数→直接対決
e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C", "D"])
rr_report(e, "p1", "p2")  # A>B
rr_report(e, "p1", "p3")  # A>C
rr_report(e, "p4", "p1")  # D>A
rr_report(e, "p2", "p3")  # B>C
rr_report(e, "p2", "p4")  # B>D
rr_report(e, "p3", "p4")  # C>D
table = standings.league_table(e.state)
order = [(row["rank"], row["player_id"]) for row in table]
check(order == [(1, "p1"), (2, "p2"), (3, "p3"), (4, "p4")],
      f"リーグ順位(直接対決): {order}")
check(e.champion_id() == "p1", "リーグ: 優勝はA")

# 3すくみ → 同順位
e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C", "D"])
rr_report(e, "p1", "p2")  # A>B
rr_report(e, "p2", "p3")  # B>C
rr_report(e, "p3", "p1")  # C>A
rr_report(e, "p1", "p4")
rr_report(e, "p2", "p4")
rr_report(e, "p3", "p4")
table = standings.league_table(e.state)
ranks = [row["rank"] for row in table]
check(ranks == [1, 1, 1, 4], f"リーグ3すくみ: 同率1位 {ranks}")
check(e.champion_id() is None, "リーグ3すくみ: 優勝者は未確定(同率)")
check(e.is_finished(), "リーグ3すくみ: 全試合終了")

# ---------- F. undo と結果修正 ----------
note("=== F. undo と結果修正 ===")
e = new_engine(Format.SINGLE_ELIM, names_n(8))
e.report("W1-1", 1, 2, 0)  # p1勝ち
sig_after1 = sig(e)
e.report("W1-2", 1, 2, 0)  # p4勝ち
e.report("W2-1", 1, 2, 0)  # p1勝ち
check(e.preview_edit_impact("W1-1") == ["W2-1"],
      "修正影響: W1-1の下流はW2-1のみ")
cleared = e.edit_result("W1-1", 2, 1, 2)  # 勝者をp8に変更
check(cleared == ["W2-1"], "修正: W2-1がクリアされた")
w21 = e.state.matches["W2-1"]
check(w21.result is None, "修正: W2-1は未消化に戻った")
check(w21.slot1.resolved_id == "p8", "修正: W2-1のスロットが新勝者p8")
check(e.state.matches["W1-1"].winner_id() == "p8", "修正: W1-1の勝者はp8")

# 勝者が変わらないスコア訂正は下流に影響しない
e2 = new_engine(Format.SINGLE_ELIM, names_n(8))
e2.report("W1-1", 1, 2, 0)
e2.report("W1-2", 1, 2, 0)
e2.report("W2-1", 1, 2, 0)
cleared = e2.edit_result("W1-1", 1, 2, 1)
check(cleared == [], "スコア訂正: 下流クリアなし")
check(e2.state.matches["W2-1"].result is not None,
      "スコア訂正: W2-1は消化済みのまま")
r = e2.state.matches["W1-1"].result
check(r.score1 == 2 and r.score2 == 1, "スコア訂正: スコアが更新された")

# undo は末尾の報告だけを取り消す
e3 = new_engine(Format.SINGLE_ELIM, names_n(8))
e3.report("W1-1", 1, 2, 0)
s1 = sig(e3)
e3.report("W1-2", 1, 2, 0)
check(e3.can_undo(), "undo: 可能")
mid = e3.undo_last()
check(mid == "W1-2", "undo: 対象はW1-2")
check(sig(e3) == s1, "undo: 状態が1手前と一致")

# 不正スコアは拒否されロールバックされる
err = False
try:
    e3.report("W1-2", 1, 3, 0)  # BO3で3勝は不正
except EngineError:
    err = True
check(err, "検証: BO3で3対0は拒否")
check(sig(e3) == s1, "検証: 拒否後も状態が保たれる")

# ---------- G. 保存と読み込み ----------
note("=== G. 保存と読み込み ===")
e = new_engine(Format.DOUBLE_ELIM, names_n(8))
for _ in range(5):
    m = e.ready_matches()[0]
    e.report(m.match_id, 1, 2, 0)
save_path = Path(__file__).parent / "tmp_save.json"
e.save(save_path)
e_loaded = TournamentEngine.load(save_path)
check(sig(e) == sig(e_loaded), "保存→読込で状態一致")
check(len(e_loaded.actions) == len(e.actions), "保存→読込でログ件数一致")

# ---------- H. テキスト生成 ----------
note("=== H. テキスト生成(読み上げ文) ===")
e = new_engine(Format.DOUBLE_ELIM, ["田中", "鈴木", "佐藤", "伊藤"])
st = e.state
note("--- 生成直後の全試合 ---")
for m in e.matches_bracket_order():
    note("TXT: " + textgen.match_line(st, m))

e.report("W1-1", 1, 2, 0)  # 田中が伊藤に勝ち
ann = textgen.report_announcement(e.state, e.state.matches["W1-1"])
note("TXT: " + ann)
check("田中の勝ち" in ann, "アナウンス: 勝者名")
check("2対1" not in ann and "2対0" in ann, "アナウンス: スコアは「対」読み")
check("ルーザーズ" in ann and "伊藤" in ann, "アナウンス: 敗者の行き先")

path_text = textgen.player_path(e.state, "p1")
note("TXT: " + path_text)
check("現在" in path_text, "選手軌跡: 次の試合を含む")
check("勝ち" in path_text, "選手軌跡: 勝敗を含む")

summary = textgen.overall_summary(e.state)
note("TXT: " + summary)
check("ダブルエリミネーション" in summary, "サマリー: 形式名")
check("参加4名" in summary, "サマリー: 人数")

# GFリセット → 優勝までの一連
e.report("W1-2", 1, 2, 0)  # 鈴木が佐藤に勝ち
e.report("W2-1", 1, 3, 0)  # 田中が鈴木に勝ち(ウィナーズ決勝はBO5)
e.report("L1-1", 1, 2, 0)  # 伊藤が佐藤に勝ち
e.report("L2-1", 1, 3, 0)  # 伊藤が鈴木に勝ち(ルーザーズ決勝はBO5)
e.report("GF", 2, 1, 3)    # 伊藤(敗者側)がGFを取る → リセット
ann = textgen.report_announcement(e.state, e.state.matches["GF"])
note("TXT: " + ann)
check("リセット" in ann, "アナウンス: リセット発生")
gfr = e.state.matches["GFR"]
check(gfr.state() == MatchState.READY, "リセット戦がREADY")
note("TXT: " + textgen.match_line(e.state, gfr))

e.report("GFR", 1, 3, 1)   # 田中がリセット戦を制して優勝
ann = textgen.report_announcement(e.state, e.state.matches["GFR"])
note("TXT: " + ann)
check("優勝" in ann and "田中" in ann, "アナウンス: 優勝")
check(e.champion_id() == "p1", "リセット後の優勝者は田中")

note("--- 終了時の全試合 ---")
for m in e.matches_bracket_order():
    note("TXT: " + textgen.match_line(e.state, m))
pl_list = standings.final_placements(e.state)
note("TXT: 順位 " + ", ".join(
    f"{p}位 {e.state.name_of(pid)}" for p, pid in pl_list))
check([p for p, _ in pl_list] == [1, 2, 3, 4], "双4人: 順位1〜4")

# シングルエリミのラベル(接頭辞なし)
e = new_engine(Format.SINGLE_ELIM, names_n(4))
lbl = textgen.phase_round_label(e.state, e.state.matches["W2-1"])
check(lbl == "決勝", f"単: 決勝ラベル({lbl})")
lbl = textgen.phase_round_label(e.state, e.state.matches["W1-1"])
check(lbl == "準決勝", f"単: 準決勝ラベル({lbl})")

# ---------- I. 個別セット数と実試合数 ----------
note("=== I. 個別セット数と実試合数 ===")
e = new_engine(Format.DOUBLE_ELIM, names_n(8),
               best_of_wf=3, best_of_lf=5, best_of_gf=7)
check(e.state.matches["W3-1"].best_of == 3, "個別BO: ウィナーズ決勝=3")
check(e.state.matches["L4-1"].best_of == 5, "個別BO: ルーザーズ決勝=5")
check(e.state.matches["GF"].best_of == 7, "個別BO: グランドファイナル=7")
check(e.state.matches["GFR"].best_of == 7, "個別BO: リセット戦=7")
check(e.state.matches["W2-1"].best_of == 3, "個別BO: 通常戦は既定BO3")

e = new_engine(Format.SINGLE_ELIM, names_n(8), third_place=True,
               best_of_wf=7, best_of_3p=1)
check(e.state.matches["W3-1"].best_of == 7, "個別BO: 単の決勝=7")
check(e.state.matches["3P"].best_of == 1, "個別BO: 3位決定戦=1")

e = new_engine(Format.DOUBLE_ELIM, names_n(8))
check(e.state.matches["W3-1"].best_of == 5
      and e.state.matches["GF"].best_of == 5,
      "個別BO: 未指定なら従来のbest_of_finalsにフォールバック")

# 実試合数(不戦を除く)
e = new_engine(Format.DOUBLE_ELIM, names_n(9))
total, done = standings.real_match_counts(e.state)
check(total == 16, f"双9人: 実試合数16(構造{len(e.state.matches)}件中、実際{total})")
check(done == 0, "双9人: 消化0")
summary = textgen.overall_summary(e.state)
check("全16試合" in summary, "サマリーが実試合数を読む")
play_all(e)
total, done = standings.real_match_counts(e.state)
check(total == 16 and done == 16, "双9人: 全消化で16対16")

e = new_engine(Format.SINGLE_ELIM, names_n(9))
total, _d = standings.real_match_counts(e.state)
check(total == 8, f"単9人: 実試合数は人数マイナス1({total})")

e = new_engine(Format.DOUBLE_ELIM, names_n(4))
t0, _d = standings.real_match_counts(e.state)
check(t0 == 6, f"双4人: リセット戦発生前は6({t0})")
play_all(e, chooser=lambda m: 2 if m.match_id == "GF" else 1)
t1, d1 = standings.real_match_counts(e.state)
check(t1 == 7 and d1 == 7, f"双4人: リセット戦発生で7対7({t1}対{d1})")

# ---------- J. 参加人数の上限(64人) ----------
note("=== J. 参加人数の上限(64人) ===")
check(MAX_PLAYERS == 64, "上限は64人")
for fmt in Format:
    e = TournamentEngine()
    e.setup(Config(title="上限", format=fmt))
    e.register_players(names_n(64))
    check(len(e.state.players) == 64, f"{fmt.value}: 64人を登録できる")
    e = TournamentEngine()
    e.setup(Config(title="上限", format=fmt))
    err = False
    try:
        e.register_players(names_n(65))
    except EngineError as exc:
        err = "64" in str(exc)
    check(err, f"{fmt.value}: 65人は拒否(上限64を伝える)")

e = TournamentEngine()
e.setup(Config(title="下限", format=Format.DOUBLE_ELIM))
err = False
try:
    e.register_players(names_n(2))
except EngineError as exc:
    err = "3" in str(exc)
check(err, "双: 2人は登録の時点で拒否(下限3を伝える)")

# 以前の固定テンプレート(4/8/16人)と同じ配置になる = 保存した大会が同じブラケットで再現される
OLD_LB_TEMPLATES = {
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
for size, tpl in OLD_LB_TEMPLATES.items():
    check(lb_template(size) == tpl, f"ルーザーズ配置: {size}人枠は以前と同じ")

# 32・64人枠の配置も固定する(変えると、保存した32・64人の大会が別の組み合わせで再生される)
PINNED_DROPS = {
    32: [[8, 7, 6, 5, 4, 3, 2, 1], [2, 1, 4, 3], [2, 1], [1]],
    64: [[16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
         [5, 6, 7, 8, 1, 2, 3, 4], [2, 1, 4, 3], [2, 1], [1]],
}
for size, pinned in PINNED_DROPS.items():
    tpl = lb_template(size)
    majors = [[int(b[1].split("-")[1]) for _a, b in pairs]
              for r, pairs in enumerate(tpl, start=1) if r % 2 == 0]
    minors_ok = all(
        pairs == [(("V", f"L{r - 1}-{2 * i - 1}"), ("V", f"L{r - 1}-{2 * i}"))
                  for i in range(1, len(pairs) + 1)]
        for r, pairs in enumerate(tpl, start=1) if r % 2 == 1 and r > 1)
    check(majors == pinned and minors_ok,
          f"ルーザーズ配置: {size}人枠の合流順が固定のとおり")

for size in (32, 64):
    tpl = lb_template(size)
    k = size.bit_length() - 1
    check(len(tpl) == 2 * (k - 1), f"ルーザーズ配置: {size}人枠は{2 * (k - 1)}回")
    drops: list[str] = []
    for pairs in tpl:
        for a, b in pairs:
            drops += [x[1] for x in (a, b) if x[0] == "L"]
    w_ids = {f"W{r}-{i}" for r in range(1, k + 1)
             for i in range(1, size // (2 ** r) + 1)}
    check(sorted(drops) == sorted(w_ids),
          f"ルーザーズ配置: {size}人枠でウィナーズ全試合の敗者が1回ずつ合流")

# 64人のダブルエリミ: リセット戦あり / 不戦が多い33人
for n, chooser, label in ((64, lambda m: 2 if m.match_id == "GF" else 1,
                           "リセット戦"),
                          (33, None, "不戦が多い")):
    e = new_engine(Format.DOUBLE_ELIM, names_n(n))
    play_all(e, chooser)
    check(e.is_finished() and e.champion_id() == "p1",
          f"双{n}人({label}): 終了して優勝はシード1")
    places = [p for p, _ in standings.final_placements(e.state)]
    check(len(places) == n and places.count(1) == 1,
          f"双{n}人({label}): 順位が全員分")

# 64人の総当たり: 2016試合・63節、全員63試合
e = new_engine(Format.ROUND_ROBIN, names_n(64))
check(len(e.state.matches) == 2016, "リーグ64人: 2016試合")
check({m.round_no for m in e.state.matches.values()} == set(range(1, 64)),
      "リーグ64人: 63節")
pairs = {frozenset(m.players()) for m in e.state.matches.values()}
check(len(pairs) == 2016, "リーグ64人: 全組み合わせが1回ずつ")
play_all(e)
table = standings.league_table(e.state)
check(e.is_finished() and table[0]["player_id"] == "p1"
      and table[0]["wins"] == 63, "リーグ64人: 全消化、首位はシード1で63勝")
check(textgen.match_label(e.state, e.state.matches["RR63-32"])
      == "第63節 第32試合", "リーグ64人: 試合ラベル")

# 結果の反映(行き先だけをたどる)が、全試合を走査する方法と同じ状態になる。
# 比べる相手は、同じ操作ログを「報告のたびに全試合を走査する」方法で再生したもの
# (反映済みの状態に全走査をかけ直すだけでは、まちがって書き込まれた値に気づけない)


class FullScanEngine(TournamentEngine):
    def _propagate_from(self, st, match_id):
        self._propagate(st)


rng = random.Random(20261103)
prop_ok = True
prop_checks = 0
for fmt, n in ((Format.DOUBLE_ELIM, 64), (Format.DOUBLE_ELIM, 37),
               (Format.SINGLE_ELIM, 45), (Format.DOUBLE_ELIM, 5),
               (Format.DOUBLE_ELIM, 3)):
    e = TournamentEngine()
    e.setup(Config(title="伝播", format=fmt, third_place=True))
    e.register_players(names_n(n))
    e.shuffle(rng.randrange(10 ** 6))
    e.generate()
    while True:
        ready = e.ready_matches()
        if not ready:
            break
        m = rng.choice(ready)
        ws = rng.choice((1, 2))
        if rng.random() < 0.2:
            e.report(m.match_id, ws, forfeit=True)
        else:
            need = m.best_of // 2 + 1
            x = rng.randrange(need)
            e.report(m.match_id, ws, *((need, x) if ws == 1 else (x, need)))
        if e.can_undo() and rng.random() < 0.05:
            e.undo_last()
        # 64人は再生に時間がかかるので、ときどき比べる
        if n < 40 or len(e.actions) % 7 == 0 or not e.ready_matches():
            prop_checks += 1
            if sig(FullScanEngine.from_dict(e.to_dict())) != sig(e):
                prop_ok = False
check(prop_ok and prop_checks > 100,
      f"結果の反映: 全試合を走査する再生と同じ状態({prop_checks}回比較)")

# 形式ごとの下限を下回る生成は EngineError(読み上げられる)になる
e = TournamentEngine()
e.setup(Config(title="x", format=Format.SINGLE_ELIM))
e.register_players(["A", "B"])
e.setup(Config(title="x", format=Format.DOUBLE_ELIM))
err = False
try:
    e.generate()
except EngineError:
    err = True
check(err, "生成の失敗は EngineError")

# 2人枠のルーザーズ配置は作れない(以前はループが終わらなかった)
err = False
try:
    lb_template(2)
except ValueError:
    err = True
check(err, "ルーザーズ配置: 2人枠は ValueError(止まらない)")

# ラウンドごとの試合数は生成時に数える(文の生成は状態を書き換えない)
e = new_engine(Format.DOUBLE_ELIM, names_n(9))
check(e.state.round_sizes.get((Phase.WINNERS, 1)) == 8
      and e.state.round_sizes.get((Phase.GRAND_FINAL, 1)) == 1,
      "ラウンドごとの試合数は生成時に数えてある")

# 選手名の確かめ方は、登録ダイアログと登録で同じ(engine.check_player_names)
from domain import check_player_names
check(check_player_names([" A ", "B"], Format.SINGLE_ELIM) == ["A", "B"],
      "選手名: 前後の空白を除く")
for names, fmt, label in ((["A", "A", "B"], Format.SINGLE_ELIM, "重複"),
                          (["A", ""], Format.SINGLE_ELIM, "空の名前"),
                          (["A", "B"], Format.DOUBLE_ELIM, "ダブルエリミ2人"),
                          (names_n(65), Format.ROUND_ROBIN, "65人")):
    err = False
    try:
        check_player_names(names, fmt)
    except EngineError:
        err = True
    check(err, f"選手名: {label}は EngineError")

# 総当たりの順位表(直接対決を1回の走査で数える)が、選手ごとに数える方法と同じ
def league_table_reference(state):
    """以前の数え方(選手ごとに全試合をたどる)。比べるためだけに使う。"""
    from collections import defaultdict
    stats = {p.player_id: {"wins": 0, "losses": 0, "gw": 0, "gl": 0}
             for p in state.players}
    done = [m for m in state.matches.values()
            if m.phase == Phase.LEAGUE and m.result is not None]
    for m in done:
        stats[m.winner_id()]["wins"] += 1
        stats[m.loser_id()]["losses"] += 1
        a, b = m.slot1.resolved_id, m.slot2.resolved_id
        stats[a]["gw"] += m.result.score1
        stats[a]["gl"] += m.result.score2
        stats[b]["gw"] += m.result.score2
        stats[b]["gl"] += m.result.score1
    groups = defaultdict(list)
    for pid, st in stats.items():
        groups[st["wins"]].append(pid)
    keymap = {}
    for wins, members in groups.items():
        mset = set(members)
        for pid in members:
            h2h = sum(1 for m in done
                      if m.winner_id() == pid and m.loser_id() in mset)
            keymap[pid] = (-wins, -h2h, -(stats[pid]["gw"] - stats[pid]["gl"]))
    seed_of = {p.player_id: p.seed for p in state.players}
    ordered = sorted(stats, key=lambda pid: (keymap[pid], seed_of[pid]))
    rows, prev, rank = [], None, 0
    for i, pid in enumerate(ordered):
        if keymap[pid] != prev:
            rank, prev = i + 1, keymap[pid]
        rows.append((rank, pid))
    return rows


rng = random.Random(7)
table_ok = True
for trial in range(120):
    n = rng.randrange(2, 13)
    e = new_engine(Format.ROUND_ROBIN, names_n(n))
    for m in list(e.matches_play_order()):
        if rng.random() < 0.8:
            ws = rng.choice((1, 2))
            x = rng.randrange(2)
            e.report(m.match_id, ws, *((2, x) if ws == 1 else (x, 2)))
    got = [(row["rank"], row["player_id"])
           for row in standings.league_table(e.state)]
    if got != league_table_reference(e.state):
        table_ok = False
check(table_ok, "リーグ順位表: 直接対決の数え方を変えても以前と同じ順位")

# 総当たりには敗退が無い(1回負けても「敗退」と言わない)
e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C", "D"])
rr_report(e, "p1", "p4")
path_text = textgen.player_path(e.state, "p4")
check(not path_text.endswith("敗退。") and "残り2試合" in path_text,
      f"総当たり: 負けても敗退と言わない({path_text})")
check(standings.elimination_groups(e.state) == [],
      "総当たり: 敗退グループは空")

# 選手別ビューの一括生成は、1人ずつの生成と同じ
for fmt, n in ((Format.DOUBLE_ELIM, 9), (Format.ROUND_ROBIN, 6),
               (Format.SINGLE_ELIM, 7)):
    e = new_engine(fmt, names_n(n), third_place=True)
    for _ in range(5):
        if e.ready_matches():
            m = e.ready_matches()[0]
            e.report(m.match_id, 2, *((0, m.best_of // 2 + 1)))
    check(textgen.player_paths(e.state)
          == [textgen.player_path(e.state, p.player_id)
              for p in e.state.players],
          f"{fmt.value}{n}人: 選手別の一括生成は1人ずつと同じ")

# 総当たり: 優勝者の出ない試合で全試合が終わっても、終了と優勝者を伝える
e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C", "D"])
for w, lo in (("p1", "p2"), ("p1", "p3"), ("p1", "p4"),
              ("p2", "p3"), ("p2", "p4")):
    rr_report(e, w, lo)
rr_report(e, "p3", "p4")  # 最後は C 対 D
last = [m for m in e.state.matches.values()
        if frozenset(m.players()) == frozenset(("p3", "p4"))][0]
ann = textgen.report_announcement(e.state, last)
check(ann == "Cの勝ち、2対0。全試合終了。Aの優勝です。",
      f"総当たり: 最後の試合で優勝を伝える({ann})")
e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C"])
rr_report(e, "p1", "p2")
rr_report(e, "p2", "p3")
rr_report(e, "p3", "p1")
last = [m for m in e.state.matches.values()
        if frozenset(m.players()) == frozenset(("p1", "p3"))][0]
ann = textgen.report_announcement(e.state, last)
check(ann.endswith("全試合終了。同率首位のため優勝者は未確定です。"),
      f"総当たり: 同率首位で終わったことを伝える({ann})")
e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C"])
rr_report(e, "p1", "p2")
ann = textgen.report_announcement(e.state, [
    m for m in e.state.matches.values()
    if frozenset(m.players()) == frozenset(("p1", "p2"))][0])
check(ann == "Aの勝ち、2対0。", f"総当たり: 途中の試合は勝敗だけ({ann})")

# 終了後に途中の試合のスコアを直しても、その試合の敗者を準優勝と言わない
e = new_engine(Format.SINGLE_ELIM, ["A", "B", "C", "D"])
e.report("W1-1", 1, 2, 0)
e.report("W1-2", 1, 2, 0)
e.report("W2-1", 1, 3, 0)
e.edit_result("W1-1", 1, 2, 1)
ann = textgen.report_announcement(e.state, e.state.matches["W1-1"])
check("準優勝" not in ann and "優勝です" not in ann and "準決勝で敗退" in ann,
      f"修正: 途中の試合の敗者は準優勝ではない({ann})")
ann = textgen.report_announcement(e.state, e.state.matches["W2-1"])
check(ann.endswith("Aの優勝です。Bは準優勝。"), f"決勝の読み上げは変わらない({ann})")

# 大会が無いときの全体状況(F5)
summary = textgen.overall_summary(TournamentEngine().state)
check(summary == "大会がありません。Ctrl+Nで新規作成、Ctrl+Oで読み込みできます。",
      f"大会が無いときの全体状況({summary})")

# ---------- K. 英語表記 ----------
note("=== K. 英語表記 ===")
ja_keys = i18n.catalog_keys("ja")
en_keys = i18n.catalog_keys("en")
check(ja_keys == en_keys,
      f"文言のキーが日英で一致(日本語のみ {sorted(ja_keys - en_keys)} / "
      f"英語のみ {sorted(en_keys - ja_keys)})")


def _fields(text: str) -> set:
    return {f for _l, f, _s, _c in string.Formatter().parse(text) if f}


from i18n import en as _en, ja as _ja
mismatch = [k for k in ja_keys & en_keys
            if _fields(_ja.MESSAGES[k]) != _fields(_en.MESSAGES[k])]
check(not mismatch, f"差し込む値の名前が日英で一致 {mismatch}")
check(len(_ja.MESSAGES["help.lines"].split("\n"))
      == len(_en.MESSAGES["help.lines"].split("\n")),
      "キー操作一覧の行数が日英で一致")

i18n.set_language("en")
try:
    check([i18n.place(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 64)]
          == ["1st place", "2nd place", "3rd place", "4th place",
              "11th place", "12th place", "13th place", "21st place",
              "22nd place", "64th place"], "英語の順位表記")

    e = new_engine(Format.DOUBLE_ELIM, ["Tanaka", "Suzuki", "Sato", "Ito"],
                   title="Test Cup")
    e.report("W1-1", 1, 2, 0)
    ann = textgen.report_announcement(e.state, e.state.matches["W1-1"])
    note("EN: " + ann)
    check(ann == "Tanaka wins, 2 to 0. Tanaka advances to Winners Final. "
                 "Ito goes to Losers Semifinal.", "英語: 結果の読み上げ")
    line = textgen.match_line(e.state, e.state.matches["W1-2"])
    check(line == "Winners Semifinal, Match 2: Suzuki vs Sato. Ready to play",
          f"英語: 試合の行({line})")
    for mid, ws, a, b in (("W1-2", 1, 2, 0), ("W2-1", 1, 3, 0),
                          ("L1-1", 1, 2, 0), ("L2-1", 1, 3, 0),
                          ("GF", 2, 1, 3)):
        e.report(mid, ws, a, b)
    ann = textgen.report_announcement(e.state, e.state.matches["GF"])
    check(ann.startswith("Ito wins, 3 to 1. Bracket reset."),
          f"英語: リセット({ann})")
    e.report("GFR", 1, 3, 0)
    ann = textgen.report_announcement(e.state, e.state.matches["GFR"])
    check(ann == "Tanaka wins, 3 to 0. Tanaka is the champion. "
                 "Ito finishes second.", f"英語: 優勝({ann})")
    lines = textgen.standings_lines(e.state)
    check(lines[:3] == ["Tournament finished. Final standings:",
                        "1st place: Tanaka", "2nd place: Ito"],
          f"英語: 確定順位 {lines[:3]}")
    summary = textgen.overall_summary(e.state)
    check(summary == "Test Cup, Double Elimination, players: 4. "
                     "Matches completed: 7 of 7. Champion: Tanaka.",
          f"英語: 全体状況({summary})")

    # すべての形式・人数で、英語の文に日本語が混ざらない
    import re
    # かな・漢字に加えて、和文の句読点・括弧・長音・全角の記号も日本語とみなす
    jp = re.compile("[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
    leak = []
    for fmt, n in ((Format.SINGLE_ELIM, 13), (Format.DOUBLE_ELIM, 13),
                   (Format.ROUND_ROBIN, 7), (Format.SINGLE_ELIM, 64)):
        e = new_engine(fmt, [f"P{i}" for i in range(n)], title="Cup",
                       third_place=True)
        for step in range(3):
            texts = [textgen.overall_summary(e.state)]
            texts += [textgen.match_line(e.state, m)
                      for m in e.matches_bracket_order()]
            texts += [textgen.player_path(e.state, p.player_id)
                      for p in e.state.players]
            texts += textgen.standings_lines(e.state)
            leak += [x for x in texts if jp.search(x)]
            if step < 2:
                play_all(e) if step == 1 else e.report(
                    e.ready_matches()[0].match_id, 2, forfeit=True)
    check(not leak, f"英語: 日本語の混入なし {leak[:3]}")

    summary = textgen.overall_summary(TournamentEngine().state)
    check(not jp.search(summary) and summary.startswith("No tournament"),
          f"英語: 大会が無いときの全体状況に日本語が混ざらない({summary})")
    check([i18n.rank_label(n) for n in (1, 2, 3, 11, 12, 13, 21, 111)]
          == ["1st", "2nd", "3rd", "11th", "12th", "13th", "21st", "111th"],
          "英語: 表の順位の表記")

    err_text = ""
    try:
        e.report("W1-1", 1, 2, 0)
    except EngineError as exc:
        err_text = str(exc)
    check(err_text == "This match is not ready to be played yet",
          f"英語: エラー文({err_text})")
finally:
    i18n.set_language("ja")
check(textgen.format_name(Format.ROUND_ROBIN) == "総当たりリーグ",
      "日本語に戻すと日本語の文")

# ---------- L. 日本語の文の固定 ----------
note("=== L. 日本語の文の固定 ===")
# 決まった手順で大会を進め、出てくる文をすべて golden_ja.txt と比べる。
# golden_ja.txt は v1.0.3 と同じ文(総当たりで「敗退」と言わない・最終戦で優勝を伝える、
# の2点だけが違う)。文言を変えたときは、意図した変更か確かめてから作り直すこと。


def transcript_ja() -> list[str]:
    out: list[str] = []

    def engine(fmt, names, title, **cfg):
        e = TournamentEngine()
        e.setup(Config(title=title, format=fmt, **cfg))
        e.register_players(names)
        e.generate()
        return e

    def dump(e, head):
        st = e.state
        out.append(f"## {head}")
        out.extend(textgen.match_line(st, m) for m in e.matches_bracket_order())
        out.extend(textgen.player_path(st, p.player_id) for p in st.players)
        out.extend(textgen.standings_lines(st))
        out.append(textgen.overall_summary(st))

    def play(e, head, pick):
        step = 0
        while e.ready_matches():
            m = e.ready_matches()[0]
            ws, forfeit = pick(step, m)
            need = m.best_of // 2 + 1
            x = step % need
            if forfeit:
                e.report(m.match_id, ws, forfeit=True)
            else:
                e.report(m.match_id, ws,
                         *((need, x) if ws == 1 else (x, need)))
            out.append(textgen.report_announcement(
                e.state, e.state.matches[m.match_id]))
            step += 1
            if step == 4:
                dump(e, head + " 途中")
        dump(e, head + " 終了")

    e = engine(Format.DOUBLE_ELIM, ["田中", "鈴木", "佐藤", "伊藤", "高橋"],
               "金曜杯")
    dump(e, "双5 生成直後")
    play(e, "双5", lambda step, m: (
        2 if m.match_id == "GF" or step % 3 == 1 else 1, step == 2))
    e = engine(Format.SINGLE_ELIM, ["A", "B", "C", "D", "E", "F"], "週末杯",
               third_place=True, best_of_wf=5, best_of_3p=1)
    play(e, "単6", lambda step, m: (1 if step % 2 == 0 else 2, step == 5))
    e = engine(Format.ROUND_ROBIN, ["赤", "青", "黄", "緑"], "リーグ")
    play(e, "総4", lambda step, m: (1 if step % 3 else 2, step == 4))
    e = engine(Format.ROUND_ROBIN, ["甲", "乙", "丙"], "三すくみ")
    play(e, "総3", lambda step, m: (2 if step == 1 else 1, False))
    return out


golden_lines = (Path(__file__).parent / "golden_ja.txt").read_text(
    encoding="utf-8").splitlines()
actual_lines = transcript_ja()
if actual_lines != golden_lines:
    (Path(__file__).parent / "result_golden_ja.txt").write_text(
        "\n".join(actual_lines) + "\n", encoding="utf-8")
    first = next((i for i, (a, b) in enumerate(zip(actual_lines, golden_lines))
                  if a != b), min(len(actual_lines), len(golden_lines)))
    note(f"日本語の文が golden_ja.txt と違う(最初の違いは {first + 1} 行目)。"
         "tests/result_golden_ja.txt と比べること")
check(actual_lines == golden_lines,
      f"日本語の文が golden_ja.txt と一致({len(golden_lines)}行)")

# ---------- V. 修正の取り消し(v1.0.4) ----------
note("=== V. 修正の取り消し ===")
e = new_engine(Format.SINGLE_ELIM, names_n(8))
e.report("W1-1", 1, 2, 0)
e.report("W1-2", 1, 2, 0)
before = sig(e)
e.edit_result("W1-1", 1, 2, 1)  # スコアだけの訂正
check(e.last_undo_kind() == "edit", "修正の後は、次の取り消しの対象が修正")
mid = e.undo_last()
check(mid == "W1-1", "スコア訂正の取り消し: 対象は訂正した試合")
check(sig(e) == before, "スコア訂正の取り消し: 訂正の前に戻る")
check(e.state.matches["W1-2"].result is not None,
      "スコア訂正の取り消し: ほかの試合の結果は消えない")

e.report("W2-1", 1, 2, 0)
before = sig(e)
cleared = e.edit_result("W1-1", 2, 0, 2)  # 勝者を変える
check(cleared == ["W2-1"], "勝者を変える修正: 下流がクリアされる")
mid = e.undo_last()
check(mid == "W1-1", "勝者を変えた修正の取り消し: 対象は修正した試合")
check(sig(e) == before, "勝者を変えた修正の取り消し: 下流の結果も戻る")
check(e.last_undo_kind() == "report", "修正を取り消すと、次の対象は結果入力")

# 修正を重ねても、保存と読み直しで同じ状態になる
e.edit_result("W1-1", 2, 1, 2)
e.report("W2-1", 2, 0, 2)
e.edit_result("W2-1", 2, 1, 2)
e.edit_result("W1-1", 1, 2, 0)
e_loaded = TournamentEngine.from_dict(json.loads(json.dumps(e.to_dict())))
check(sig(e) == sig(e_loaded), "修正を含むログ: 保存→読込で状態一致")
check(e.state.matches["W2-1"].result is None,
      "修正を含むログ: 最後の修正で下流がクリアされている")

err = False
try:
    e.edit_result("W3-1", 1, 2, 0)  # まだ結果の無い試合
except EngineError:
    err = True
check(err, "結果の無い試合は修正できない")

# ---------- W. 人数と壊れたデータ(v1.0.4) ----------
note("=== W. 人数と壊れたデータ ===")
e = TournamentEngine()
e.setup(Config(title="2人", format=Format.DOUBLE_ELIM))
msg = ""
try:
    e.register_players(["A", "B"])
    e.generate()
except EngineError as exc:
    msg = str(exc)
except Exception as exc:  # EngineError 以外で止まってはいけない
    msg = "別の例外: " + type(exc).__name__
check("3人" in msg, f"双2人: 読み上げられる文で断る({msg})")
check(not e.state.generated, "双2人: 断った後は未生成のまま")

bad_files = [
    ("リスト", []),
    ("payload の無い操作", {"format_version": 1, "actions": [{"kind": "setup"}]}),
    ("知らない選手の並び順", {"format_version": 1, "actions": [
        {"kind": "register_players", "payload": {"names": ["A", "B"]}},
        {"kind": "set_order", "payload": {"order": ["p9", "p1"]}}]}),
    ("偶数のセット数", {"format_version": 1, "actions": [
        {"kind": "setup", "payload": {
            "title": "T", "format": "single_elim", "best_of_default": 4,
            "best_of_finals": 5, "gf_reset": True, "third_place": False}}]}),
    ("65人", {"format_version": 1, "actions": [
        {"kind": "register_players",
         "payload": {"names": [str(i) for i in range(65)]}},
        {"kind": "generate", "payload": {}}]}),
]
for label, data in bad_files:
    kind = "読めてしまった"
    try:
        TournamentEngine.from_dict(data)
    except EngineError:
        kind = "EngineError"
    except Exception as exc:
        kind = type(exc).__name__
    check(kind == "EngineError", f"壊れたデータ({label}): {kind}")

bad_path = Path(__file__).parent / "tmp_bad.json"
bad_path.write_text("{ これは JSON ではない", encoding="utf-8")
kind = "読めてしまった"
try:
    TournamentEngine.load(bad_path)
except EngineError:
    kind = "EngineError"
except Exception as exc:
    kind = type(exc).__name__
bad_path.unlink()
check(kind == "EngineError", f"JSON でないファイル: {kind}")

# 保存は一時ファイル経由(途中の .tmp を残さない)
e = new_engine(Format.SINGLE_ELIM, names_n(4))
e.save(save_path)
check(not save_path.with_name(save_path.name + ".tmp").exists(),
      "保存: 一時ファイルが残らない")
check(sig(TournamentEngine.load(save_path)) == sig(e), "保存: 上書きして読み直せる")

# ---------- X. 終了時の読み上げと並び(v1.0.4) ----------
note("=== X. 終了時の読み上げと並び ===")
e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C"])
rr_report(e, "p1", "p2")
rr_report(e, "p1", "p3")
rr_report(e, "p2", "p3")  # 最後の試合の勝者(B)は優勝者(A)ではない
last = [m for m in e.state.matches.values()
        if frozenset(m.players()) == frozenset(("p2", "p3"))][0]
ann = textgen.report_announcement(e.state, last)
note("TXT: " + ann)
check("全試合終了" in ann and "Aの優勝" in ann,
      "リーグ: 最後の試合の勝者でなくても優勝者を読む")

e = new_engine(Format.ROUND_ROBIN, ["A", "B", "C"])
rr_report(e, "p1", "p2")
rr_report(e, "p2", "p3")
ann = textgen.report_announcement(e.state, e.state.matches[
    [m.match_id for m in e.state.matches.values()
     if frozenset(m.players()) == frozenset(("p2", "p3"))][0]])
check("全試合終了" not in ann, "リーグ: 途中では終了と言わない")
rr_report(e, "p3", "p1")  # 3すくみ
last = [m for m in e.state.matches.values()
        if frozenset(m.players()) == frozenset(("p1", "p3"))][0]
ann = textgen.report_announcement(e.state, last)
note("TXT: " + ann)
check("同率首位" in ann, "リーグ: 同率首位で終わったことを読む")

e = new_engine(Format.SINGLE_ELIM, ["A", "B", "C", "D"], third_place=True)
e.report("W1-1", 1, 2, 0)
e.report("W1-2", 1, 2, 0)
need = e.state.matches["3P"].best_of // 2 + 1
e.report("3P", 1, need, 0)
ann = textgen.report_announcement(e.state, e.state.matches["3P"])
note("TXT: " + ann)
check("3位" in ann and "4位" in ann and "敗退" not in ann,
      "3位決定戦: 3位と4位を読む")

e = new_engine(Format.SINGLE_ELIM, names_n(16))
for m in list(e.ready_matches()):
    e.report(m.match_id, 1, 2, 0)  # 上位シードが全部勝つ → 選手9〜16が敗退
groups = standings.elimination_groups(e.state)
check(groups[0][2] == [f"p{i}" for i in range(9, 17)],
      f"敗退者の並びはシード順(p9 が p10 より前): {groups[0][2]}")
play_all(e)
tail = [pid for p, pid in standings.final_placements(e.state) if p == 9]
check(tail == [f"p{i}" for i in range(9, 17)],
      f"同じ順位の並びはシード順: {tail}")

# ---------- 結果出力 ----------
out = Path(__file__).parent / "result_domain.txt"
LINES.append("")
LINES.append(f"合計 {TOTAL} 件中 失敗 {FAIL} 件")
out.write_text("\n".join(LINES) + "\n", encoding="utf-8")
print(f"TOTAL={TOTAL} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
