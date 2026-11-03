"""fg_tournament メインアプリ(フェーズ2)。

キーボードのみで全操作が完結する、スクリーンリーダー前提の大会運営コンソール。
4ビュー(F6切替) + 結果入力/修正 + undo + 保存読込。
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import wx

from app import views
from app.dialogs import (HelpDialog, ImpactConfirmDialog, OrderDialog,
                         PlayersDialog, PublishDialog, ResultDialog,
                         SetupDialog)
from app.speech import Speaker
from domain import EngineError, MatchState, TournamentEngine
from domain import textgen
from publish import exporter
from publish.uploader import Publisher

APP_NAME = "格ゲー大会マネージャー"
APP_VERSION = "1.0.3"
APP_TITLE = f"{APP_NAME} v{APP_VERSION}"


def _resolve_data_dir() -> Path:
    """データフォルダ。exe化(PyInstaller)時は exe と同じ場所の data。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parents[1] / "data"


DATA_DIR = _resolve_data_dir()

ID_INPUT = wx.NewIdRef()
ID_UNDO = wx.NewIdRef()
ID_SUMMARY = wx.NewIdRef()
ID_VIEW = wx.NewIdRef()
ID_HELP = wx.NewIdRef()
ID_SAVEAS = wx.NewIdRef()
ID_PUBLISH = wx.NewIdRef()
ID_UPLOAD = wx.NewIdRef()
ID_COPYURL = wx.NewIdRef()


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=APP_TITLE, size=(960, 660))
        self.engine = TournamentEngine()
        self.speaker = Speaker()
        self.publisher = Publisher(DATA_DIR)
        self.file_path: Path | None = None
        self.view_index = 0
        self._last_event: str | None = None
        self._rows: list[tuple[str, tuple]] = []
        self._build_menu()
        self._build_ui()
        self._refresh(keep_selection=False)
        self.Centre()

    # ---------- 構築 ----------

    def _build_menu(self):
        menubar = wx.MenuBar()

        m_file = wx.Menu()
        m_file.Append(wx.ID_NEW, "新規大会の作成(&N)\tCtrl+N")
        m_file.Append(wx.ID_OPEN, "開く(&O)\tCtrl+O")
        m_file.Append(wx.ID_SAVE, "上書き保存(&S)\tCtrl+S")
        m_file.Append(ID_SAVEAS, "名前を付けて保存(&A)\tCtrl+Shift+S")
        m_file.AppendSeparator()
        m_file.Append(wx.ID_EXIT, "終了(&X)")
        menubar.Append(m_file, "ファイル(&F)")

        m_game = wx.Menu()
        m_game.Append(ID_INPUT, "選択中の試合の結果入力・修正(&E)")
        m_game.Append(ID_UNDO, "直前の結果入力を取り消し(&U)\tCtrl+Z")
        m_game.AppendSeparator()
        m_game.Append(ID_SUMMARY, "全体状況を読み上げ(&R)\tF5")
        m_game.Append(ID_VIEW, "ビュー切替(&V)\tF6")
        menubar.Append(m_game, "大会(&T)")

        m_pub = wx.Menu()
        m_pub.Append(ID_PUBLISH, "公開設定(&S)...")
        m_pub.Append(ID_UPLOAD, "今すぐアップロード(&U)\tCtrl+U")
        m_pub.Append(ID_COPYURL, "閲覧URLをコピー(&L)")
        menubar.Append(m_pub, "公開(&P)")

        m_help = wx.Menu()
        m_help.Append(ID_HELP, "キー操作一覧(&K)\tF1")
        menubar.Append(m_help, "ヘルプ(&H)")

        self.SetMenuBar(menubar)
        self.Bind(wx.EVT_MENU, lambda e: self._on_new(), id=wx.ID_NEW)
        self.Bind(wx.EVT_MENU, lambda e: self._on_open(), id=wx.ID_OPEN)
        self.Bind(wx.EVT_MENU, lambda e: self._on_save(False), id=wx.ID_SAVE)
        self.Bind(wx.EVT_MENU, lambda e: self._on_save(True), id=ID_SAVEAS)
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, lambda e: self._activate_row(), id=ID_INPUT)
        self.Bind(wx.EVT_MENU, lambda e: self._on_undo(), id=ID_UNDO)
        self.Bind(wx.EVT_MENU, lambda e: self._on_summary(), id=ID_SUMMARY)
        self.Bind(wx.EVT_MENU, lambda e: self._on_switch_view(), id=ID_VIEW)
        self.Bind(wx.EVT_MENU, lambda e: self._on_help(), id=ID_HELP)
        self.Bind(wx.EVT_MENU, lambda e: self._on_publish_settings(),
                  id=ID_PUBLISH)
        self.Bind(wx.EVT_MENU, lambda e: self._on_upload(), id=ID_UPLOAD)
        self.Bind(wx.EVT_MENU, lambda e: self._on_copy_url(), id=ID_COPYURL)

    def _build_ui(self):
        panel = wx.Panel(self)
        panel.SetName("メイン")
        self.panel = panel
        s = wx.BoxSizer(wx.VERTICAL)
        self.view_label = wx.StaticText(panel, label=views.VIEW_TITLES[0])
        s.Add(self.view_label, 0, wx.ALL, 6)
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName(views.VIEW_TITLES[0])
        s.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 6)
        panel.SetSizer(s)
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK,
                          lambda e: self._activate_row())
        self.listbox.SetFocus()

    # ---------- 表示 ----------

    def _refresh(self, keep_selection: bool = True,
                 speak_title: bool = False):
        title, rows = views.build_view(self.engine, self.view_index)
        self._rows = rows
        sel = self.listbox.GetSelection() if keep_selection else 0
        self.listbox.Set([r[0] for r in rows])
        if rows:
            sel = min(max(sel, 0), len(rows) - 1)
            self.listbox.SetSelection(sel)
        self.view_label.SetLabel(title)
        self.listbox.SetName(title)
        if self.engine.state.generated:
            self.SetTitle(f"{self.engine.state.config.title} - {APP_TITLE}")
        else:
            self.SetTitle(APP_TITLE)
        if speak_title:
            self._announce(f"ビュー: {title}。{len(rows)}件")

    def _announce(self, text: str, delay_ms: int = 200) -> None:
        """一覧更新の直後はPC-Talkerが選択項目を自動で読み上げて割り込むため、
        更新後に少し遅らせて読む(この読み上げが後勝ちになる)。"""
        wx.CallLater(delay_ms, self.speaker.say, text)

    def _on_switch_view(self):
        self.view_index = (self.view_index + 1) % len(views.VIEW_TITLES)
        self._refresh(keep_selection=False, speak_title=True)

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._activate_row()
            return
        event.Skip()

    # ---------- 行のアクティベート ----------

    def _activate_row(self):
        sel = self.listbox.GetSelection()
        if sel < 0 or sel >= len(self._rows):
            return
        text, (kind, key) = self._rows[sel]
        if kind == "match":
            self._open_match(key)
        elif kind == "player":
            self.speaker.say(textgen.player_path(self.engine.state, key))
        else:
            self.speaker.say(text)

    def _open_match(self, match_id: str):
        st = self.engine.state
        m = st.matches[match_id]
        mstate = m.state()
        if mstate == MatchState.READY:
            self._do_report(m)
        elif (mstate == MatchState.DONE and m.result is not None
              and not m.result.auto_bye):
            self._do_edit(m)
        elif mstate == MatchState.DONE:
            self.speaker.info("この試合は不戦で自動消化されています。"
                              "修正はできません。")
        elif mstate == MatchState.SKIPPED:
            self.speaker.info("この試合は実施されません。")
        else:
            self.speaker.info("対戦者がまだ確定していません。")

    # ---------- 結果入力・修正 ----------

    def _ask_result(self, m, editing: bool):
        dlg = ResultDialog(self, self.engine.state, m, editing=editing)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return None
            return dlg.get_result()
        finally:
            dlg.Destroy()

    def _do_report(self, m):
        res = self._ask_result(m, editing=False)
        if res is None:
            return
        try:
            self.engine.report(m.match_id, res["winner_slot"],
                               res["score1"], res["score2"], res["forfeit"])
        except EngineError as exc:
            self.speaker.error(str(exc))
            return
        ann = textgen.report_announcement(
            self.engine.state, self.engine.state.matches[m.match_id])
        self._last_event = ann
        self._autosave()
        self._refresh()
        self._announce(ann)
        self._maybe_auto_upload()

    def _do_edit(self, m):
        res = self._ask_result(m, editing=True)
        if res is None:
            return
        # 勝者が変わる場合のみ下流クリアの確認を出す
        if res["winner_slot"] != m.result.winner_slot:
            impact = self.engine.preview_edit_impact(m.match_id)
            if impact:
                dlg = ImpactConfirmDialog(self, self.engine.state, impact)
                ok = dlg.ShowModal() == wx.ID_OK
                dlg.Destroy()
                if not ok:
                    self.speaker.info("修正を中止しました。")
                    return
        try:
            cleared = self.engine.edit_result(
                m.match_id, res["winner_slot"],
                res["score1"], res["score2"], res["forfeit"])
        except EngineError as exc:
            self.speaker.error(str(exc))
            return
        ann = textgen.report_announcement(
            self.engine.state, self.engine.state.matches[m.match_id])
        msg = "修正しました。" + ann
        if cleared:
            msg += f"クリアされた試合は{len(cleared)}件です。"
        self._last_event = msg
        self._autosave()
        self._refresh()
        self._announce(msg)
        self._maybe_auto_upload()

    # ---------- 大会操作 ----------

    def _on_undo(self):
        try:
            mid = self.engine.undo_last()
        except EngineError as exc:
            self.speaker.error(str(exc))
            return
        line = textgen.match_line(self.engine.state,
                                  self.engine.state.matches[mid])
        self._last_event = f"結果の取り消し: {line}"
        self._autosave()
        self._refresh()
        self._announce(f"取り消しました。{line}")
        self._maybe_auto_upload()

    def _on_summary(self):
        self.speaker.info(textgen.overall_summary(self.engine.state))

    def _on_help(self):
        dlg = HelpDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_new(self):
        sdlg = SetupDialog(self)
        if sdlg.ShowModal() != wx.ID_OK:
            sdlg.Destroy()
            return
        config = sdlg.get_config()
        sdlg.Destroy()

        pdlg = PlayersDialog(self, self.speaker)
        if pdlg.ShowModal() != wx.ID_OK:
            pdlg.Destroy()
            return
        names = pdlg.get_names()
        pdlg.Destroy()

        eng = TournamentEngine()
        try:
            eng.setup(config)
            eng.register_players(names)
        except EngineError as exc:
            self.speaker.error(str(exc))
            return

        odlg = OrderDialog(self, eng.state, self.speaker)
        if odlg.ShowModal() != wx.ID_OK:
            odlg.Destroy()
            self.speaker.info("新規作成を中止しました。")
            return
        order = odlg.get_order()
        odlg.Destroy()

        try:
            eng.set_order(order)
            eng.generate()
        except EngineError as exc:
            self.speaker.error(str(exc))
            return

        self.engine = eng
        self.file_path = None
        self.view_index = 0
        self._refresh(keep_selection=False)
        self._autosave()
        self._announce("ブラケットを生成しました。"
                       + textgen.overall_summary(eng.state), delay_ms=300)

    # ---------- 保存・読み込み ----------

    def _on_open(self):
        DATA_DIR.mkdir(exist_ok=True)
        dlg = wx.FileDialog(self, "大会ファイルを開く",
                            defaultDir=str(DATA_DIR),
                            wildcard="大会ファイル (*.json)|*.json",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        path = Path(dlg.GetPath())
        dlg.Destroy()
        try:
            self.engine = TournamentEngine.load(path)
        except Exception as exc:
            self.speaker.error(f"読み込みに失敗しました。{exc}")
            return
        self.file_path = path
        self.view_index = 0
        self._refresh(keep_selection=False)
        self._announce("読み込みました。"
                       + textgen.overall_summary(self.engine.state),
                       delay_ms=300)

    def _on_save(self, save_as: bool):
        if not self.engine.state.generated:
            self.speaker.info("保存する大会がありません。")
            return
        if save_as or self.file_path is None:
            DATA_DIR.mkdir(exist_ok=True)
            default = self._default_filename()
            dlg = wx.FileDialog(self, "名前を付けて保存",
                                defaultDir=str(DATA_DIR),
                                defaultFile=default,
                                wildcard="大会ファイル (*.json)|*.json",
                                style=(wx.FD_SAVE
                                       | wx.FD_OVERWRITE_PROMPT))
            if dlg.ShowModal() != wx.ID_OK:
                dlg.Destroy()
                return
            self.file_path = Path(dlg.GetPath())
            dlg.Destroy()
        try:
            self.engine.save(self.file_path)
        except Exception as exc:
            self.speaker.error(f"保存に失敗しました。{exc}")
            return
        self.speaker.success(f"保存しました。{self.file_path.name}")

    def _default_filename(self) -> str:
        title = self.engine.state.config.title
        safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or "大会"
        stamp = datetime.date.today().strftime("%Y%m%d")
        return f"{stamp}_{safe}.json"

    def _autosave(self):
        """結果入力のたびに自動保存する。保存先が未指定なら autosave.json。"""
        try:
            DATA_DIR.mkdir(exist_ok=True)
            target = self.file_path or (DATA_DIR / "autosave.json")
            self.engine.save(target)
        except Exception:
            self.speaker.warn("自動保存に失敗しました。")


    # ---------- 公開(オンライン閲覧) ----------

    def _build_snapshot(self) -> dict:
        return exporter.build_snapshot(self.engine,
                                       last_event=self._last_event)

    def _on_publish_settings(self):
        dlg = PublishDialog(self, self.publisher)
        saved = dlg.ShowModal() == wx.ID_OK
        dlg.Destroy()
        if not saved:
            return
        if self.publisher.enabled:
            self.speaker.say("公開設定を保存しました。公開は有効です。"
                             "Ctrl+Uでいつでもアップロードできます。")
        else:
            self.speaker.say("公開設定を保存しました。公開は無効です。")

    def _on_copy_url(self):
        if not (self.publisher.token and self.publisher.relay_url):
            self.speaker.info("閲覧URLが未発行です。"
                              "公開メニューの公開設定から発行してください。")
            return
        url = self.publisher.viewer_url()
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(url))
            wx.TheClipboard.Close()
            self.speaker.say("閲覧URLをコピーしました。" + url)
        else:
            self.speaker.error("クリップボードを開けませんでした。")

    def _on_upload(self):
        if not self.engine.state.generated:
            self.speaker.info("アップロードする大会がありません。")
            return
        if not self.publisher.enabled:
            self.speaker.info("公開が無効です。"
                              "公開メニューの公開設定から有効にしてください。")
            return
        snap = self._build_snapshot()

        def done(ok: bool, msg: str):
            if ok:
                wx.CallAfter(self.speaker.say, msg, False)
            else:
                wx.CallAfter(self.speaker.warn, msg, False)

        self.publisher.upload_async(snap, done)
        self.speaker.say("アップロードしています。")

    def _maybe_auto_upload(self):
        """公開が有効なら、結果反映のたびにバックグラウンドで自動アップロード。
        成功時は読み上げない(操作を妨げない)。失敗時のみ警告する。"""
        if not (self.publisher.enabled and self.engine.state.generated):
            return
        snap = self._build_snapshot()

        def done(ok: bool, msg: str):
            if not ok:
                wx.CallAfter(self.speaker.warn,
                             "自動アップロードに失敗しました。" + msg, False)

        self.publisher.upload_async(snap, done)


def main():
    app = wx.App()
    frame = MainFrame()
    frame.Show()
    frame.speaker.info(f"{APP_NAME} バージョン{APP_VERSION}を起動しました。"
                       "Ctrl+Nで新規作成、F1でキー操作一覧。")
    app.MainLoop()


if __name__ == "__main__":
    main()
