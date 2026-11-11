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

    # 以下は EndModal と MessageBox を差し替えて、画面を出さずに動きを確かめる
    import shutil
    import tempfile
    from app import dialogs
    from app.dialogs import PublishDialog
    from publish.uploader import Publisher

    boxes: list[str] = []
    real_box = dialogs.wx.MessageBox
    dialogs.wx.MessageBox = lambda msg, *a, **k: boxes.append(msg) or wx.YES
    try:
        # ダブルエリミネーションは3人から(選手登録の画面で断る)
        d = PlayersDialog(frame, frame.speaker, min_players=3)
        ended: list[int] = []
        d.EndModal = ended.append
        d.text.SetValue("田中\n鈴木")
        d._on_ok()
        check(not ended and boxes and "3人" in boxes[-1],
              "PlayersDialog 2人はダブルエリミで断る")
        d.text.SetValue("田中\n鈴木\n佐藤")
        d._on_ok()
        check(ended == [wx.ID_OK] and d.get_names() == ["田中", "鈴木", "佐藤"],
              "PlayersDialog 3人なら通る")
        d.Destroy()

        tmp = Path(tempfile.mkdtemp())
        pub = Publisher(tmp)
        pub.relay_url = "https://example.com/old/tournament_relay.php"
        pub.token = "a" * 32
        pub.save()

        # 作り直してもキャンセルすれば何も変わらない
        d = PublishDialog(frame, pub)
        d.EndModal = ended.append
        d._on_issue()
        check(("a" * 32) not in d.viewer_ctrl.GetValue(),
              "PublishDialog 発行で画面の閲覧URLが変わる")
        d.Destroy()
        check(Publisher(tmp).token == "a" * 32 and pub.token == "a" * 32,
              "PublishDialog 発行しても保存するまで確定しない")

        # http の URL は保存できない
        d = PublishDialog(frame, pub)
        ended.clear()
        d.EndModal = ended.append
        d.enable_check.SetValue(True)
        d.url_ctrl.SetValue("http://example.com/tournament_relay.php")
        d._on_ok()
        check(not ended and "https" in boxes[-1]
              and Publisher(tmp).relay_url.startswith("https://"),
              "PublishDialog http のURLは断る")

        # 作り直して保存すると、古いURLとトークンを呼び出し側へ渡す
        d.url_ctrl.SetValue("https://example.com/new/tournament_relay.php")
        d._on_issue()
        d._on_ok()
        saved = Publisher(tmp)
        check(ended == [wx.ID_OK] and saved.token != "a" * 32
              and len(saved.token) == 32 and saved.enabled
              and saved.relay_url.endswith("/new/tournament_relay.php"),
              "PublishDialog 作り直して保存")
        check(d.retired == ("https://example.com/old/tournament_relay.php",
                            "a" * 32),
              "PublishDialog 消すべき古いURLとトークンを返す")
        d.Destroy()
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        dialogs.wx.MessageBox = real_box

    # 新規作成の前に、前の自動保存を別の名前で残す
    from app import main as app_main
    tmp = Path(tempfile.mkdtemp())
    real_dir = app_main.DATA_DIR
    app_main.DATA_DIR = tmp
    try:
        check(frame._keep_previous_autosave() is False,
              "自動保存が無ければ何もしない")
        eng.save(tmp / "autosave.json")
        check(frame._keep_previous_autosave() is True
              and not (tmp / "autosave.json").exists()
              and len(list(tmp.glob("autosave_backup_*.json"))) == 1,
              "前の自動保存を別の名前で残す")
    finally:
        app_main.DATA_DIR = real_dir
        shutil.rmtree(tmp, ignore_errors=True)

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
