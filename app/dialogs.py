"""ダイアログ群。

アクセシビリティ規約:
- StaticText ラベルは対応コントロールより先に生成(PC-Talkerのラベル対応)
- すべてのウィジェットに SetName を付ける
- キーはパネルの EVT_CHAR_HOOK で処理(文字キーは使わない。F8/Enter/EscはIMEセーフ)
- OK/キャンセルはパネル親 + EndModal 直接バインド
- 文言は i18n から取る(ダイアログは開くたびに作るので、そのときの言語で出る)
"""
from __future__ import annotations

import random

import wx

from domain import Config, EngineError, Format, check_player_names
from domain import textgen
from domain.models import MAX_PLAYERS, min_players
from i18n import t
from publish.uploader import is_safe_relay_url, new_token, viewer_url_for

_FORMATS = [Format.DOUBLE_ELIM, Format.SINGLE_ELIM, Format.ROUND_ROBIN]
_BO_CHOICES = [1, 3, 5, 7]


def _bo_label(best_of: int) -> str:
    return t(f"bo.{best_of}")


def _is_enter(code: int) -> bool:
    return code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)


class SetupDialog(wx.Dialog):
    """大会の基本設定。OKなら get_config() で Config を返す。"""

    def __init__(self, parent):
        super().__init__(parent, title=t("setup.title"), size=(640, 660))
        panel = wx.Panel(self)
        panel.SetName(t("setup.title"))
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel, label=t("setup.name"))
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.title_ctrl = wx.TextCtrl(panel, value=t("setup.default_title"))
        self.title_ctrl.SetName(t("setup.name"))
        s.Add(self.title_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        lbl = wx.StaticText(panel, label=t("setup.format"))
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.format_choice = wx.Choice(
            panel, choices=[textgen.format_name(f) for f in _FORMATS])
        self.format_choice.SetName(t("setup.format"))
        self.format_choice.SetSelection(0)
        s.Add(self.format_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        def _bo_choice(label: str, name: str | None,
                       default_index: int) -> wx.Choice:
            lb = wx.StaticText(panel, label=label)
            s.Add(lb, 0, wx.LEFT | wx.TOP, 8)
            ch = wx.Choice(panel, choices=[_bo_label(b) for b in _BO_CHOICES])
            ch.SetName(name or label)
            ch.SetSelection(default_index)
            s.Add(ch, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            return ch

        # 既定: 通常戦=BO3、決勝系=BO5
        self.bo_default = _bo_choice(t("setup.bo_default"), None, 1)
        self.bo_wf_c = _bo_choice(t("setup.bo_wf"), t("setup.bo_wf_name"), 2)
        self.bo_lf_c = _bo_choice(t("setup.bo_lf"), t("setup.bo_lf_name"), 2)
        self.bo_gf_c = _bo_choice(t("setup.bo_gf"), t("setup.bo_gf_name"), 2)
        self.bo_3p_c = _bo_choice(t("setup.bo_3p"), t("setup.bo_3p_name"), 2)

        self.reset_check = wx.CheckBox(panel, label=t("setup.reset"))
        self.reset_check.SetName(t("setup.reset_name"))
        self.reset_check.SetValue(True)
        s.Add(self.reset_check, 0, wx.ALL, 8)

        self.third_check = wx.CheckBox(panel, label=t("setup.third"))
        self.third_check.SetName(t("setup.third_name"))
        s.Add(self.third_check, 0, wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, label=t("btn.next"))
        ok.SetName(t("btn.next"))
        cancel = wx.Button(panel, label=t("btn.cancel"))
        cancel.SetName(t("btn.cancel"))
        btns.Add(ok, 0, wx.ALL, 8)
        btns.Add(cancel, 0, wx.ALL, 8)
        s.Add(btns, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)

        ok.Bind(wx.EVT_BUTTON, lambda e: self._on_ok())
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.title_ctrl.SetFocus()

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        if _is_enter(code):
            self._on_ok()
            return
        event.Skip()

    def _on_ok(self):
        title = self.title_ctrl.GetValue().strip() or t("setup.default_title")
        wf = _BO_CHOICES[self.bo_wf_c.GetSelection()]
        lf = _BO_CHOICES[self.bo_lf_c.GetSelection()]
        gf = _BO_CHOICES[self.bo_gf_c.GetSelection()]
        tp = _BO_CHOICES[self.bo_3p_c.GetSelection()]
        self._config = Config(
            title=title,
            format=_FORMATS[self.format_choice.GetSelection()],
            best_of_default=_BO_CHOICES[self.bo_default.GetSelection()],
            best_of_finals=gf,  # 互換用の既定値(GFの値を入れる)
            gf_reset=self.reset_check.GetValue(),
            third_place=self.third_check.GetValue(),
            best_of_wf=wf, best_of_lf=lf, best_of_gf=gf, best_of_3p=tp,
        )
        self.EndModal(wx.ID_OK)

    def get_config(self) -> Config:
        return self._config


class PlayersDialog(wx.Dialog):
    """選手登録。OKなら get_names() で名前リストを返す。
    入力欄でEnterを押すたびに、確定した名前と現在の人数を読み上げる。
    人数の下限は形式で変わる(ダブルエリミだけ3人)。"""

    def __init__(self, parent, speaker, fmt: Format = Format.SINGLE_ELIM):
        super().__init__(parent, title=t("players.title"), size=(560, 520))
        self._speaker = speaker
        self._fmt = fmt
        self._min = min_players(fmt)
        panel = wx.Panel(self)
        panel.SetName(t("players.title"))
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel, label=t("players.label", min=self._min,
                                           max=MAX_PLAYERS))
        s.Add(lbl, 0, wx.ALL, 8)
        self.text = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.text.SetName(t("players.input_name"))
        s.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, label=t("btn.next"))
        ok.SetName(t("btn.next"))
        cancel = wx.Button(panel, label=t("btn.cancel"))
        cancel.SetName(t("btn.cancel"))
        btns.Add(ok, 0, wx.ALL, 8)
        btns.Add(cancel, 0, wx.ALL, 8)
        s.Add(btns, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)

        ok.Bind(wx.EVT_BUTTON, lambda e: self._on_ok())
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.text.SetFocus()

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        if _is_enter(code):
            if wx.Window.FindFocus() is self.text:
                event.Skip()  # 入力欄のEnterは改行として通す
                # PC-Talker自身のキーエコー(「改行」)に割り込まれないよう
                # 少し遅らせてから読む(こちらが後勝ちになる)
                wx.CallLater(250, self._speak_entered)
                return
            self._on_ok()
            return
        event.Skip()

    def _speak_entered(self):
        """Enterで確定した行(直前の名前)と現在の人数を読み上げる。"""
        pos = self.text.GetInsertionPoint()
        before = self.text.GetRange(0, pos)
        lines = before.splitlines()
        name = lines[-1].strip() if lines else ""
        if not name:
            return
        count = len([ln for ln in self.text.GetValue().splitlines()
                     if ln.strip()])
        self._speaker.say(t("players.entered", name=name, n=count))

    def _on_ok(self):
        names = [ln for ln in self.text.GetValue().splitlines() if ln.strip()]
        try:
            # 登録と同じ規則で確かめる。だめならダイアログを閉じずに入力を残す
            names = check_player_names(names, self._fmt)
        except EngineError as exc:
            wx.MessageBox(str(exc), t("dlg.error"),
                          wx.OK | wx.ICON_ERROR, self)
            return
        self._names = names
        self.EndModal(wx.ID_OK)

    def get_names(self) -> list[str]:
        return self._names


class OrderDialog(wx.Dialog):
    """並び順の確認とシャッフル。OKなら get_order() で選手IDリストを返す。"""

    def __init__(self, parent, state, speaker):
        super().__init__(parent, title=t("order.title"), size=(560, 520))
        self._state = state
        self._speaker = speaker
        self._ids = [p.player_id for p in state.players]
        panel = wx.Panel(self)
        panel.SetName(t("order.panel"))
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel, label=t("order.label"))
        s.Add(lbl, 0, wx.ALL, 8)
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName(t("order.list"))
        s.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        shuffle_btn = wx.Button(panel, label=t("order.shuffle_button"))
        shuffle_btn.SetName(t("order.shuffle_name"))
        ok = wx.Button(panel, label=t("order.generate"))
        ok.SetName(t("order.generate"))
        cancel = wx.Button(panel, label=t("btn.cancel"))
        cancel.SetName(t("btn.cancel"))
        btns.Add(shuffle_btn, 0, wx.ALL, 8)
        btns.Add(ok, 0, wx.ALL, 8)
        btns.Add(cancel, 0, wx.ALL, 8)
        s.Add(btns, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)

        shuffle_btn.Bind(wx.EVT_BUTTON, self._on_shuffle)
        ok.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self._refresh()
        self.listbox.SetFocus()

    def _refresh(self):
        names = [t("order.item", i=i + 1, name=self._state.name_of(pid))
                 for i, pid in enumerate(self._ids)]
        self.listbox.Set(names)
        if names:
            self.listbox.SetSelection(0)

    def _on_shuffle(self, event=None):
        random.shuffle(self._ids)
        self._refresh()
        order_text = t("phrase_sep").join(
            t("order.item", i=i + 1, name=self._state.name_of(pid))
            for i, pid in enumerate(self._ids))
        # 一覧更新直後の自動読み上げに割り込まれないよう少し遅らせる
        wx.CallLater(200, self._speaker.say,
                     t("order.shuffled", order=order_text))

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        if code == wx.WXK_F8:
            self._on_shuffle()
            return
        if _is_enter(code):
            self.EndModal(wx.ID_OK)
            return
        event.Skip()

    def get_order(self) -> list[str]:
        return list(self._ids)


class ResultDialog(wx.Dialog):
    """結果入力・修正。OKなら get_result() で
    dict(winner_slot, score1, score2, forfeit) を返す。"""

    def __init__(self, parent, state, match, editing: bool = False):
        title = t("result.title_edit") if editing else t("result.title")
        super().__init__(parent,
                         title=f"{title}: {textgen.match_label(state, match)}",
                         size=(600, 480))
        n1 = state.name_of(match.slot1.resolved_id)
        n2 = state.name_of(match.slot2.resolved_id)
        needed = match.best_of // 2 + 1

        # 候補: n1側のスイープ→接戦、n2側の接戦→スイープ、最後に不戦勝
        self._cands: list[tuple[str, tuple[int, int, int, bool]]] = []
        for x in range(needed):
            self._cands.append((t("result.score", name=n1, a=needed, b=x),
                                (1, needed, x, False)))
        for x in range(needed - 1, -1, -1):
            self._cands.append((t("result.score", name=n2, a=needed, b=x),
                                (2, needed, x, False)))
        self._cands.append((t("result.forfeit", w=n1, lo=n2),
                            (1, 0, 0, True)))
        self._cands.append((t("result.forfeit", w=n2, lo=n1),
                            (2, 0, 0, True)))

        panel = wx.Panel(self)
        panel.SetName(title)
        s = wx.BoxSizer(wx.VERTICAL)

        head = t("result.head", n1=n1, n2=n2)
        if editing and match.result is not None:
            cur_w = state.name_of(match.winner_id())
            if match.result.forfeit:
                current = t("result.current_forfeit", w=cur_w)
            else:
                r = match.result
                ws, ls = ((r.score1, r.score2) if r.winner_slot == 1
                          else (r.score2, r.score1))
                current = t("result.current", w=cur_w, ws=ws, ls=ls)
            head = t("sentence_sep").join([current, head])
        # 選手名の「&」をニーモニック(下線付きのアクセスキー)にしない
        lbl = wx.StaticText(panel, label=wx.Control.EscapeMnemonics(head))
        s.Add(lbl, 0, wx.ALL, 8)

        self.listbox = wx.ListBox(panel, choices=[c[0] for c in self._cands])
        self.listbox.SetName(t("result.list"))
        s.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # 修正時は現在の結果を選択済みにする
        sel = 0
        if editing and match.result is not None:
            r = match.result
            ws, ls = ((r.score1, r.score2) if r.winner_slot == 1
                      else (r.score2, r.score1))
            cur = (r.winner_slot, ws if not r.forfeit else 0,
                   ls if not r.forfeit else 0, r.forfeit)
            for i, (_t, cand) in enumerate(self._cands):
                if cand == cur:
                    sel = i
                    break
        self.listbox.SetSelection(sel)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, label=t("btn.ok"))
        ok.SetName(t("btn.ok"))
        cancel = wx.Button(panel, label=t("btn.cancel"))
        cancel.SetName(t("btn.cancel"))
        btns.Add(ok, 0, wx.ALL, 8)
        btns.Add(cancel, 0, wx.ALL, 8)
        s.Add(btns, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)

        ok.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK,
                          lambda e: self.EndModal(wx.ID_OK))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.listbox.SetFocus()

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        if _is_enter(code):
            self.EndModal(wx.ID_OK)
            return
        event.Skip()

    def get_result(self) -> dict:
        sel = max(self.listbox.GetSelection(), 0)
        winner_slot, sw, sl, forfeit = self._cands[sel][1]
        if winner_slot == 1:
            score1, score2 = sw, sl
        else:
            score1, score2 = sl, sw
        return {"winner_slot": winner_slot, "score1": score1,
                "score2": score2, "forfeit": forfeit}


class ImpactConfirmDialog(wx.Dialog):
    """結果修正の影響確認。クリアされる試合を一覧で示す。"""

    def __init__(self, parent, state, match_ids: list[str]):
        super().__init__(parent, title=t("impact.title"), size=(600, 440))
        panel = wx.Panel(self)
        panel.SetName(t("impact.title"))
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel,
                            label=t("impact.label", n=len(match_ids)))
        s.Add(lbl, 0, wx.ALL, 8)

        lines = [textgen.match_line(state, state.matches[mid])
                 for mid in match_ids]
        self.listbox = wx.ListBox(panel, choices=lines)
        self.listbox.SetName(t("impact.list"))
        if lines:
            self.listbox.SetSelection(0)
        s.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, label=t("impact.ok"))
        ok.SetName(t("impact.ok"))
        cancel = wx.Button(panel, label=t("impact.cancel"))
        cancel.SetName(t("impact.cancel"))
        btns.Add(ok, 0, wx.ALL, 8)
        btns.Add(cancel, 0, wx.ALL, 8)
        s.Add(btns, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)

        ok.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.listbox.SetFocus()

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()


def help_lines() -> list[str]:
    return t("help.lines").split("\n")


class HelpDialog(wx.Dialog):
    """キー操作一覧。"""

    def __init__(self, parent):
        super().__init__(parent, title=t("help.title"), size=(640, 480))
        panel = wx.Panel(self)
        panel.SetName(t("help.title"))
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel, label=t("help.label"))
        s.Add(lbl, 0, wx.ALL, 8)
        self.listbox = wx.ListBox(panel, choices=help_lines())
        self.listbox.SetName(t("help.title"))
        self.listbox.SetSelection(0)
        s.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        close = wx.Button(panel, label=t("btn.close"))
        close.SetName(t("btn.close"))
        s.Add(close, 0, wx.ALIGN_CENTER | wx.ALL, 8)
        panel.SetSizer(s)

        close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.listbox.SetFocus()

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code in (wx.WXK_ESCAPE, wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.EndModal(wx.ID_OK)
            return
        event.Skip()


class PublishDialog(wx.Dialog):
    """オンライン公開の設定。「保存」で publisher に書き込む。
    キャンセルしたときは何も変えない(閲覧URLの作り直しも保存するまで確定しない)。

    閲覧URLを作り直して保存したとき、retired に作り直す前の
    (relay のURL, トークン) が入る。呼び出し側がサーバーの古い内容を消す。"""

    def __init__(self, parent, publisher):
        super().__init__(parent, title=t("pub.title"), size=(680, 560))
        self._pub = publisher
        self._new_token = ""   # 発行ボタンで作った、まだ保存していないトークン
        self.retired = None
        panel = wx.Panel(self)
        panel.SetName(t("pub.panel"))
        s = wx.BoxSizer(wx.VERTICAL)

        self.enable_check = wx.CheckBox(panel, label=t("pub.enable"))
        self.enable_check.SetName(t("pub.enable_name"))
        self.enable_check.SetValue(publisher.enabled)
        s.Add(self.enable_check, 0, wx.ALL, 8)

        lbl = wx.StaticText(panel, label=t("pub.relay"))
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.url_ctrl = wx.TextCtrl(panel, value=publisher.relay_url)
        self.url_ctrl.SetName(t("pub.relay_name"))
        s.Add(self.url_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        lbl = wx.StaticText(panel, label=t("pub.key"))
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.key_ctrl = wx.TextCtrl(panel)
        self.key_ctrl.SetName(t("pub.key_name"))
        s.Add(self.key_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        lbl = wx.StaticText(panel, label=t("pub.viewer"))
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.viewer_ctrl = wx.TextCtrl(panel, style=wx.TE_READONLY)
        self.viewer_ctrl.SetName(t("pub.viewer_name"))
        self._update_viewer_url()
        s.Add(self.viewer_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        issue_btn = wx.Button(panel, label=t("pub.issue"))
        issue_btn.SetName(t("pub.issue_name"))
        ok = wx.Button(panel, label=t("btn.save"))
        ok.SetName(t("btn.save"))
        cancel = wx.Button(panel, label=t("btn.cancel"))
        cancel.SetName(t("btn.cancel"))
        btns.Add(issue_btn, 0, wx.ALL, 8)
        btns.Add(ok, 0, wx.ALL, 8)
        btns.Add(cancel, 0, wx.ALL, 8)
        s.Add(btns, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)

        issue_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_issue())
        ok.Bind(wx.EVT_BUTTON, lambda e: self._on_ok())
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.enable_check.SetFocus()

    def _url(self) -> str:
        """入力欄のURL。空なら保存済みのURL。"""
        return self.url_ctrl.GetValue().strip() or self._pub.relay_url

    def _token(self) -> str:
        return self._new_token or self._pub.token

    def _update_viewer_url(self):
        url = viewer_url_for(self._url(), self._token())
        self.viewer_ctrl.SetValue(url or t("pub.not_issued"))

    def _check_relay_url(self) -> bool:
        """relay のURLが使えるものでなければ知らせて入力欄へ戻す。"""
        url = self._url()
        if not url:
            msg = t("pub.need_relay")
        elif not is_safe_relay_url(url):
            msg = t("pub.need_https")
        else:
            return True
        wx.MessageBox(msg, t("pub.panel"), wx.OK | wx.ICON_WARNING, self)
        self.url_ctrl.SetFocus()
        return False

    def _on_issue(self):
        if not self._check_relay_url():
            return
        if self._pub.token and not self._new_token:
            ans = wx.MessageBox(t("pub.reissue_confirm"), t("pub.confirm"),
                                wx.YES_NO | wx.ICON_QUESTION, self)
            if ans != wx.YES:
                return
        self._new_token = new_token()
        self._update_viewer_url()
        self.viewer_ctrl.SetFocus()

    def _on_ok(self):
        enabled = self.enable_check.GetValue()
        # 無効にして保存するだけなら、URLが空でもよい
        if (enabled or self._url()) and not self._check_relay_url():
            return
        old = (self._pub.relay_url, self._pub.token)
        self._pub.relay_url = self._url()
        key = self.key_ctrl.GetValue().strip()
        if key:
            self._pub.set_admin_key(key)
        self._pub.enabled = enabled
        if self._new_token:
            self._pub.token = self._new_token
            if old[0] and old[1]:
                self.retired = old
        elif enabled and not self._pub.token:
            self._pub.token = new_token()
        self._pub.save()
        self.EndModal(wx.ID_OK)

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        # ボタンの上の Enter はボタン自身が受けるので、ここには来ない
        if _is_enter(code):
            self._on_ok()
            return
        event.Skip()
