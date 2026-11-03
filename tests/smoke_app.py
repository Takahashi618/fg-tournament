"""アプリ層スモークテスト(ウィンドウ表示なし)。

TOURNEY_SILENT=1 を設定した上で、全モジュールのコンパイル、
ビュー生成、フレームと全ダイアログの構築・破棄を確認する。
結果は tests/result_app_smoke.txt に出力。終了コード: 失敗ありなら1。
"""
from __future__ import annotations

import os
import py_compile
import sys
import traceback
from pathlib import Path

os.environ["TOURNEY_SILENT"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def run() -> None:
    # 1) コンパイル確認
    targets = []
    for sub in ("domain", "app"):
        targets += sorted((ROOT / sub).glob("*.py"))
    for t in targets:
        try:
            py_compile.compile(str(t), doraise=True)
            check(True, f"compile {t.name}")
        except Exception as exc:
            check(False, f"compile {t.name}: {exc}")

    # 2) エンジン準備(ダブルエリミ4人、2試合消化)
    from domain import Config, Format, TournamentEngine
    eng = TournamentEngine()
    eng.setup(Config(title="スモーク", format=Format.DOUBLE_ELIM))
    eng.register_players(["田中", "鈴木", "佐藤", "伊藤"])
    eng.generate()
    eng.report("W1-1", 1, 2, 0)
    eng.report("W1-2", 1, 2, 0)

    # 3) ビュー生成(wx非依存)
    from app import views
    for i in range(4):
        title, rows = views.build_view(eng, i)
        check(bool(rows), f"ビュー{i} {title}: {len(rows)}行")

    # 4) wx 構築(表示はしない)
    import wx
    app = wx.App()
    from app.dialogs import (HelpDialog, ImpactConfirmDialog, OrderDialog,
                             PlayersDialog, ResultDialog, SetupDialog)
    from app.main import MainFrame

    frame = MainFrame()
    check(True, "MainFrame 構築")
    frame.engine = eng
    frame._refresh(keep_selection=False)
    check(frame.listbox.GetCount() > 0, "メイン一覧に行がある")
    for _ in range(4):
        frame._on_switch_view()
    check(frame.view_index == 0, "F6でビューが一周する")
    frame._on_summary()
    check(True, "全体状況の読み上げ呼び出し")

    d = SetupDialog(frame)
    d.Destroy()
    check(True, "SetupDialog 構築")

    d = PlayersDialog(frame, frame.speaker)
    d.Destroy()
    check(True, "PlayersDialog 構築")

    d = OrderDialog(frame, eng.state, frame.speaker)
    check(d.listbox.GetCount() == 4, "OrderDialog 4名表示")
    d.Destroy()

    ready = eng.ready_matches()[0]
    d = ResultDialog(frame, eng.state, ready)
    needed = ready.best_of // 2 + 1
    check(d.listbox.GetCount() == 2 * needed + 2, "ResultDialog 候補数")
    res = d.get_result()
    check(res["winner_slot"] == 1 and not res["forfeit"],
          "ResultDialog 既定候補の取得")
    d.Destroy()

    done = eng.state.matches["W1-1"]
    d = ResultDialog(frame, eng.state, done, editing=True)
    check(d.listbox.GetSelection() == 0,
          "ResultDialog 修正時に現在の結果(2対0)を選択済み")
    d.Destroy()

    impact = eng.preview_edit_impact("W1-1")
    d = ImpactConfirmDialog(frame, eng.state, impact if impact else ["W2-1"])
    d.Destroy()
    check(True, "ImpactConfirmDialog 構築")

    d = HelpDialog(frame)
    d.Destroy()
    check(True, "HelpDialog 構築")

    from app.dialogs import PublishDialog
    from publish.uploader import Publisher
    pub = Publisher(ROOT / "tests" / "tmp_pub")
    d = PublishDialog(frame, pub)
    d.Destroy()
    check(True, "PublishDialog 構築")

    frame.Destroy()
    check(True, "破棄完了")


try:
    run()
except Exception:
    FAIL += 1
    LINES.append("NG: 例外発生")
    LINES.append(traceback.format_exc())

out = Path(__file__).parent / "result_app_smoke.txt"
LINES.append("")
LINES.append(f"合計 {TOTAL} 件中 失敗 {FAIL} 件")
out.write_text("\n".join(LINES) + "\n", encoding="utf-8")
print(f"TOTAL={TOTAL} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
