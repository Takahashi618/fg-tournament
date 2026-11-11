"""fg_tournament メインアプリ(フェーズ2)。

キーボードのみで全操作が完結する、スクリーンリーダー前提の大会運営コンソール。
4ビュー(F6切替) + 結果入力/修正 + undo + 保存読込。
表示と読み上げの言語は言語メニューで切り替える(日本語 / English)。
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import wx

from app import settings, views
from app.dialogs import (HelpDialog, ImpactConfirmDialog, OrderDialog,
                         PlayersDialog, PublishDialog, ResultDialog,
                         SetupDialog)
from app.speech import Speaker
from domain import EngineError, MatchState, TournamentEngine
from domain import textgen
from publish import exporter
from i18n import LANGUAGES, get_language, set_language, t
from publish.uploader import Publisher

APP_VERSION = "1.1.0"


def app_title() -> str:
    return f"{t('app.name')} v{APP_VERSION}"


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
ID_LANGS = {code: wx.NewIdRef() for code in LANGUAGES}

# メニューバーの構成(左から順): (メニュー名のキー, 項目の一覧)。
# 項目は (ID, 文言キー)、None は区切り線。項目が None のメニューは言語の切り替え。
# メニューを作るとき(_build_menu)と言語を切り替えるとき(_apply_language)の両方がこの表を使う。
_MENUS = [
    ("menu.file", [
        (wx.ID_NEW, "menu.new"),
        (wx.ID_OPEN, "menu.open"),
        (wx.ID_SAVE, "menu.save"),
        (ID_SAVEAS, "menu.save_as"),
        None,
        (wx.ID_EXIT, "menu.exit"),
    ]),
    ("menu.tournament", [
        (ID_INPUT, "menu.input"),
        (ID_UNDO, "menu.undo"),
        None,
        (ID_SUMMARY, "menu.summary"),
        (ID_VIEW, "menu.view"),
    ]),
    ("menu.publish", [
        (ID_PUBLISH, "menu.publish_settings"),
        (ID_UPLOAD, "menu.upload"),
        (ID_COPYURL, "menu.copy_url"),
    ]),
    ("menu.language", None),
    ("menu.help", [
        (ID_HELP, "menu.keys"),
    ]),
]


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=app_title(), size=(960, 660))
        self.engine = TournamentEngine()
        self.speaker = Speaker()
        self.publisher = Publisher(DATA_DIR)
        self.file_path: Path | None = None
        self.view_index = 0
        # 公開時に閲覧ページへ送る「直前の出来事」の文。言語を切り替えても
        # その言語で作り直せるよう、文ではなく文を作る関数を持つ
        self._last_event: Callable[[], str] | None = None
        self._rows: list[tuple[str, tuple]] = []
        self._build_menu()
        self._build_ui()
        self._refresh(keep_selection=False)
        self.Centre()

    # ---------- 構築 ----------

    def _build_menu(self):
        menubar = wx.MenuBar()
        for title_key, items in _MENUS:
            menu = wx.Menu()
            if items is None:
                # 言語名はどちらの言語でも、その言語自身の表記で出す
                for code, name in LANGUAGES.items():
                    menu.AppendRadioItem(ID_LANGS[code], name)
                menu.Check(ID_LANGS[get_language()], True)
            else:
                for item in items:
                    if item is None:
                        menu.AppendSeparator()
                    else:
                        menu.Append(item[0], t(item[1]))
            menubar.Append(menu, t(title_key))
        self.SetMenuBar(menubar)
        for code, mid in ID_LANGS.items():
            self.Bind(wx.EVT_MENU,
                      lambda e, c=code: self._on_language(c), id=mid)
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
        panel.SetName(t("main.panel"))
        self.panel = panel
        s = wx.BoxSizer(wx.VERTICAL)
        self.view_label = wx.StaticText(panel, label=views.view_title(0))
        s.Add(self.view_label, 0, wx.ALL, 6)
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName(views.view_title(0))
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
            self.SetTitle(f"{self.engine.state.config.title} - {app_title()}")
        else:
            self.SetTitle(app_title())
        if speak_title:
            self._announce(t("msg.view", title=title, n=len(rows)))

    def _announce(self, text: str, delay_ms: int = 200) -> None:
        """一覧更新の直後はPC-Talkerが選択項目を自動で読み上げて割り込むため、
        更新後に少し遅らせて読む(この読み上げが後勝ちになる)。"""
        wx.CallLater(delay_ms, self.speaker.say, text)

    def _on_switch_view(self):
        self.view_index = (self.view_index + 1) % views.VIEW_COUNT
        self._refresh(keep_selection=False, speak_title=True)

    # ---------- 言語 ----------

    def _on_language(self, code: str):
        """表示と読み上げの言語を切り替える。メニューは作り直さずラベルを付け替える
        (メニューのイベント処理中にメニューバーを破棄しないため)。"""
        if code == get_language():
            return
        set_language(code)
        try:
            settings.save_language(DATA_DIR, code)
            saved = True
        except Exception:
            saved = False
        self._apply_language()
        msg = t("msg.language_changed")
        if not saved:
            # 切り替えはできたが、次の起動に引き継げないことを伝える
            msg = t("sentence_sep").join(
                [msg, t("speech.warn", text=t("msg.language_save_failed"))])
        self._announce(msg)

    def _apply_language(self):
        menubar = self.GetMenuBar()
        for pos, (title_key, items) in enumerate(_MENUS):
            menubar.SetMenuLabel(pos, t(title_key))
            for item in items or ():
                if item is not None:
                    menubar.SetLabel(item[0], t(item[1]))
        menubar.Check(ID_LANGS[get_language()], True)
        self.panel.SetName(t("main.panel"))
        self._refresh()

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
            self.speaker.info(t("msg.auto_bye_no_edit"))
        elif mstate == MatchState.SKIPPED:
            self.speaker.info(t("msg.skipped"))
        else:
            self.speaker.info(t("msg.pending"))

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
        mid = m.match_id

        def event() -> str:
            st = self.engine.state
            return textgen.report_announcement(st, st.matches[mid])

        self._last_event = event
        self._autosave()
        self._refresh()
        self._announce(event())
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
                    self.speaker.info(t("msg.edit_cancelled"))
                    return
        try:
            cleared = self.engine.edit_result(
                m.match_id, res["winner_slot"],
                res["score1"], res["score2"], res["forfeit"])
        except EngineError as exc:
            self.speaker.error(str(exc))
            return
        mid = m.match_id
        cleared_count = len(cleared)

        def event() -> str:
            st = self.engine.state
            parts = [t("msg.edited"),
                     textgen.report_announcement(st, st.matches[mid])]
            if cleared_count:
                parts.append(t("msg.cleared", n=cleared_count))
            return t("sentence_sep").join(parts)

        self._last_event = event
        self._autosave()
        self._refresh()
        self._announce(event())
        self._maybe_auto_upload()

    # ---------- 大会操作 ----------

    def _on_undo(self):
        was_edit = self.engine.last_undo_kind() == "edit"
        try:
            mid = self.engine.undo_last()
        except EngineError as exc:
            self.speaker.error(str(exc))
            return

        def line() -> str:
            st = self.engine.state
            return textgen.match_line(st, st.matches[mid])

        # 修正の取り消しは、修正する前の結果(消えた下流の結果も)へ戻す
        event_key = "msg.edit_undo_event" if was_edit else "msg.undo_event"
        spoken_key = "msg.edit_undone" if was_edit else "msg.undone"
        self._last_event = lambda: t(event_key, line=line())
        self._autosave()
        self._refresh()
        self._announce(t(spoken_key, line=line()))
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

        pdlg = PlayersDialog(self, self.speaker, config.format)
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
            self.speaker.info(t("msg.new_cancelled"))
            return
        order = odlg.get_order()
        odlg.Destroy()

        try:
            eng.set_order(order)
            eng.generate()
        except EngineError as exc:
            self.speaker.error(str(exc))
            return

        kept = self._keep_previous_autosave()
        self.engine = eng
        self._last_event = None  # 前の大会の出来事を新しい大会の閲覧ページに出さない
        self.file_path = None
        self.view_index = 0
        self._refresh(keep_selection=False)
        self._autosave()
        parts = [t("msg.generated"), textgen.overall_summary(eng.state)]
        if kept:
            parts.append(t("msg.autosave_kept"))
        self._announce(t("sentence_sep").join(parts), delay_ms=300)

    # ---------- 保存・読み込み ----------

    def _on_open(self):
        DATA_DIR.mkdir(exist_ok=True)
        dlg = wx.FileDialog(self, t("msg.open_title"),
                            defaultDir=str(DATA_DIR),
                            wildcard=t("msg.file_wildcard"),
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        path = Path(dlg.GetPath())
        dlg.Destroy()
        try:
            self.engine = TournamentEngine.load(path)
        except EngineError as exc:
            self.speaker.error(t("msg.load_failed", error=exc))
            return
        except OSError:
            self.speaker.error(t("msg.load_failed",
                                 error=t("msg.cannot_open_file")))
            return
        except Exception:
            self.speaker.error(t("msg.load_failed",
                                 error=t("err.broken_file")))
            return
        self._last_event = None
        self.file_path = path
        self.view_index = 0
        self._refresh(keep_selection=False)
        self._announce(t("sentence_sep").join(
            [t("msg.loaded"), textgen.overall_summary(self.engine.state)]),
            delay_ms=300)

    def _on_save(self, save_as: bool):
        if not self.engine.state.generated:
            self.speaker.info(t("msg.nothing_to_save"))
            return
        if save_as or self.file_path is None:
            DATA_DIR.mkdir(exist_ok=True)
            default = self._default_filename()
            dlg = wx.FileDialog(self, t("msg.save_as_title"),
                                defaultDir=str(DATA_DIR),
                                defaultFile=default,
                                wildcard=t("msg.file_wildcard"),
                                style=(wx.FD_SAVE
                                       | wx.FD_OVERWRITE_PROMPT))
            if dlg.ShowModal() != wx.ID_OK:
                dlg.Destroy()
                return
            target = Path(dlg.GetPath())
            dlg.Destroy()
        else:
            target = self.file_path
        try:
            self.engine.save(target)
        except Exception:
            self.speaker.error(t("msg.save_failed",
                                 error=t("msg.check_writable")))
            return
        # 保存できてから保存先を覚える(失敗した場所へ自動保存し続けない)
        self.file_path = target
        self.speaker.success(t("msg.saved", name=target.name))

    def _default_filename(self) -> str:
        title = self.engine.state.config.title
        safe = (re.sub(r'[\\/:*?"<>|]', "_", title).strip()
                or t("setup.default_title"))
        stamp = datetime.date.today().strftime("%Y%m%d")
        return f"{stamp}_{safe}.json"

    def _keep_previous_autosave(self) -> bool:
        """新しい大会が autosave.json を上書きする前に、前の内容を別の名前で残す。
        名前を付けて保存していない大会が、新規作成で消えないようにするため。"""
        auto = DATA_DIR / "autosave.json"
        if not auto.exists():
            return False
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            auto.replace(DATA_DIR / f"autosave_backup_{stamp}.json")
        except OSError:
            self.speaker.warn(t("msg.autosave_keep_failed"))
            return False
        return True

    def _autosave(self):
        """結果入力のたびに自動保存する。保存先が未指定なら autosave.json。"""
        try:
            DATA_DIR.mkdir(exist_ok=True)
            target = self.file_path or (DATA_DIR / "autosave.json")
            self.engine.save(target)
        except Exception:
            self.speaker.warn(t("msg.autosave_failed"))


    # ---------- 公開(オンライン閲覧) ----------

    def _build_snapshot(self) -> dict:
        return exporter.build_snapshot(self.engine,
                                       last_event=(self._last_event()
                                                   if self._last_event
                                                   else None))

    def _on_publish_settings(self):
        dlg = PublishDialog(self, self.publisher)
        saved = dlg.ShowModal() == wx.ID_OK
        retired = dlg.retired  # 作り直す前の (relay のURL, トークン)。無ければ None
        dlg.Destroy()
        if not saved:
            return
        if retired is not None:
            # 古い閲覧URLの内容をサーバーから消す(消せなければ知らせる)
            def deleted(ok: bool, msg: str):
                if not ok:
                    wx.CallAfter(self.speaker.warn,
                                 t("msg.old_viewer_delete_failed", msg=msg),
                                 False)

            self.publisher.delete_remote_async(retired[1], retired[0], deleted)
        if self.publisher.enabled:
            self.speaker.say(t("msg.publish_on"))
        else:
            self.speaker.say(t("msg.publish_off"))

    def _on_copy_url(self):
        if not (self.publisher.token and self.publisher.relay_url):
            self.speaker.info(t("msg.no_viewer_url"))
            return
        url = self.publisher.viewer_url()
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(url))
            wx.TheClipboard.Close()
            self.speaker.say(t("msg.url_copied", url=url))
        else:
            self.speaker.error(t("msg.clipboard_failed"))

    def _on_upload(self):
        if not self.engine.state.generated:
            self.speaker.info(t("msg.nothing_to_upload"))
            return
        if not self.publisher.enabled:
            self.speaker.info(t("msg.publish_disabled"))
            return
        snap = self._build_snapshot()

        def done(ok: bool, msg: str):
            if ok:
                wx.CallAfter(self.speaker.say, msg, False)
            else:
                wx.CallAfter(self.speaker.warn, msg, False)

        self.publisher.upload_async(snap, done)
        self.speaker.say(t("msg.uploading"))

    def _maybe_auto_upload(self):
        """公開が有効なら、結果反映のたびにバックグラウンドで自動アップロード。
        成功時は読み上げない(操作を妨げない)。失敗時のみ警告する。"""
        if not (self.publisher.enabled and self.engine.state.generated):
            return
        snap = self._build_snapshot()

        def done(ok: bool, msg: str):
            if not ok:
                wx.CallAfter(self.speaker.warn,
                             t("msg.auto_upload_failed", msg=msg), False)

        self.publisher.upload_async(snap, done)


def main():
    set_language(settings.load_language(DATA_DIR))
    app = wx.App()
    frame = MainFrame()
    frame.Show()
    frame.speaker.info(t("msg.started", name=t("app.name"),
                         ver=APP_VERSION))
    app.MainLoop()


if __name__ == "__main__":
    main()
