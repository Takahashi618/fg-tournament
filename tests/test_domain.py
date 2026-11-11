"""ドメイン層テスト(直接実行式)。

実行すると tests/result_domain.txt に UTF-8 で結果を書き出す。
終了コード: 全件OKなら0、失敗があれば1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.models import (BYE_ID, Config, Format, MatchState, Phase,
                           SourceType)
from domain.bracket_single import bracket_size, seed_slots
from domain.engine import EngineError, TournamentEngine
from domain import standings, textgen

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


def new_engine(fmt: Format, names: list[str], **cfg) -> TournamentEngine:
    e = TournamentEngine()
    e.setup(Config(title="テスト大会", format=fmt, **cfg))
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
        if guard > 500:
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
for n in range(2, 17):
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
for n in range(3, 17):
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
for n in (8, 16):
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

# ---------- J. 修正の取り消し ----------
note("=== J. 修正の取り消し ===")
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

# ---------- K. 人数と壊れたデータ ----------
note("=== K. 人数と壊れたデータ ===")
e = TournamentEngine()
e.setup(Config(title="2人", format=Format.DOUBLE_ELIM))
e.register_players(["A", "B"])
msg = ""
try:
    e.generate()
except EngineError as exc:
    msg = str(exc)
except Exception as exc:  # EngineError 以外で止まってはいけない
    msg = "別の例外: " + type(exc).__name__
check("3人以上" in msg, f"双2人: 読み上げられる文で断る({msg})")
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
    ("17人", {"format_version": 1, "actions": [
        {"kind": "register_players",
         "payload": {"names": [str(i) for i in range(17)]}},
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

# ---------- L. 終了時の読み上げと並び ----------
note("=== L. 終了時の読み上げと並び ===")
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
