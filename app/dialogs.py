"""ダイアログ群。

アクセシビリティ規約:
- StaticText ラベルは対応コントロールより先に生成(PC-Talkerのラベル対応)
- すべてのウィジェットに SetName を付ける
- キーはパネルの EVT_CHAR_HOOK で処理(文字キーは使わない。F8/Enter/EscはIMEセーフ)
- OK/キャンセルはパネル親 + EndModal 直接バインド
"""
from __future__ import annotations

import random

import wx

from domain import Config, Format
from domain import textgen

_FORMATS = [
    ("ダブルエリミネーション", Format.DOUBLE_ELIM),
    ("シングルエリミネーション", Format.SINGLE_ELIM),
    ("総当たりリーグ", Format.ROUND_ROBIN),
]
_BO_CHOICES = [("1本先取(BO1)", 1), ("2本先取(BO3)", 3),
               ("3本先取(BO5)", 5), ("4本先取(BO7)", 7)]


def _is_enter(code: int) -> bool:
    return code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)


class SetupDialog(wx.Dialog):
    """大会の基本設定。OKなら get_config() で Config を返す。"""

    def __init__(self, parent):
        super().__init__(parent, title="新規大会の設定", size=(640, 660))
        panel = wx.Panel(self)
        panel.SetName("新規大会の設定")
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel, label="大会名")
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.title_ctrl = wx.TextCtrl(panel, value="大会")
        self.title_ctrl.SetName("大会名")
        s.Add(self.title_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        lbl = wx.StaticText(panel, label="形式")
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.format_choice = wx.Choice(panel,
                                       choices=[n for n, _ in _FORMATS])
        self.format_choice.SetName("形式")
        self.format_choice.SetSelection(0)
        s.Add(self.format_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        def _bo_choice(label: str, name: str, default_index: int) -> wx.Choice:
            lb = wx.StaticText(panel, label=label)
            s.Add(lb, 0, wx.LEFT | wx.TOP, 8)
            ch = wx.Choice(panel, choices=[n for n, _ in _BO_CHOICES])
            ch.SetName(name)
            ch.SetSelection(default_index)
            s.Add(ch, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
            return ch

        # 既定: 通常戦=BO3、決勝系=BO5
        self.bo_default = _bo_choice("通常戦のセット数", "通常戦のセット数", 1)
        self.bo_wf_c = _bo_choice("決勝(ウィナーズ決勝)のセット数",
                                  "決勝のセット数", 2)
        self.bo_lf_c = _bo_choice("ルーザーズ決勝のセット数(ダブルエリミ)",
                                  "ルーザーズ決勝のセット数", 2)
        self.bo_gf_c = _bo_choice("グランドファイナルのセット数(リセット戦も同じ)",
                                  "グランドファイナルのセット数", 2)
        self.bo_3p_c = _bo_choice("3位決定戦のセット数(シングルエリミ)",
                                  "3位決定戦のセット数", 2)

        self.reset_check = wx.CheckBox(
            panel, label="グランドファイナルのリセット戦あり(ダブルエリミ)")
        self.reset_check.SetName("リセット戦あり")
        self.reset_check.SetValue(True)
        s.Add(self.reset_check, 0, wx.ALL, 8)

        self.third_check = wx.CheckBox(
            panel, label="3位決定戦あり(シングルエリミ)")
        self.third_check.SetName("3位決定戦あり")
        s.Add(self.third_check, 0, wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, label="次へ")
        ok.SetName("次へ")
        cancel = wx.Button(panel, label="キャンセル")
        cancel.SetName("キャンセル")
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
        title = self.title_ctrl.GetValue().strip() or "大会"
        wf = _BO_CHOICES[self.bo_wf_c.GetSelection()][1]
        lf = _BO_CHOICES[self.bo_lf_c.GetSelection()][1]
        gf = _BO_CHOICES[self.bo_gf_c.GetSelection()][1]
        tp = _BO_CHOICES[self.bo_3p_c.GetSelection()][1]
        self._config = Config(
            title=title,
            format=_FORMATS[self.format_choice.GetSelection()][1],
            best_of_default=_BO_CHOICES[self.bo_default.GetSelection()][1],
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
    入力欄でEnterを押すたびに、確定した名前と現在の人数を読み上げる。"""

    def __init__(self, parent, speaker):
        super().__init__(parent, title="選手登録", size=(560, 520))
        self._speaker = speaker
        panel = wx.Panel(self)
        panel.SetName("選手登録")
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel,
                            label="選手名を1行に1人ずつ入力(2〜16人)")
        s.Add(lbl, 0, wx.ALL, 8)
        self.text = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        self.text.SetName("選手名入力。1行に1人")
        s.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, label="次へ")
        ok.SetName("次へ")
        cancel = wx.Button(panel, label="キャンセル")
        cancel.SetName("キャンセル")
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
        self._speaker.say(f"{name}、{count}人目")

    def _on_ok(self):
        names = [ln.strip() for ln in self.text.GetValue().splitlines()
                 if ln.strip()]
        if not (2 <= len(names) <= 16):
            wx.MessageBox("選手は2人から16人までです。", "エラー",
                          wx.OK | wx.ICON_ERROR, self)
            return
        if len(names) != len(set(names)):
            wx.MessageBox("選手名が重複しています。", "エラー",
                          wx.OK | wx.ICON_ERROR, self)
            return
        self._names = names
        self.EndModal(wx.ID_OK)

    def get_names(self) -> list[str]:
        return self._names


class OrderDialog(wx.Dialog):
    """並び順の確認とシャッフル。OKなら get_order() で選手IDリストを返す。"""

    def __init__(self, parent, state, speaker):
        super().__init__(parent, title="並び順(シード順)", size=(560, 520))
        self._state = state
        self._speaker = speaker
        self._ids = [p.player_id for p in state.players]
        panel = wx.Panel(self)
        panel.SetName("並び順")
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(
            panel, label="現在の並び順。F8またはシャッフルボタンで並び替え")
        s.Add(lbl, 0, wx.ALL, 8)
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName("並び順一覧")
        s.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        shuffle_btn = wx.Button(panel, label="シャッフル(F8)")
        shuffle_btn.SetName("シャッフル")
        ok = wx.Button(panel, label="この順で生成")
        ok.SetName("この順で生成")
        cancel = wx.Button(panel, label="キャンセル")
        cancel.SetName("キャンセル")
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
        names = [f"{i + 1}番 {self._state.name_of(pid)}"
                 for i, pid in enumerate(self._ids)]
        self.listbox.Set(names)
        if names:
            self.listbox.SetSelection(0)

    def _on_shuffle(self, event=None):
        random.shuffle(self._ids)
        self._refresh()
        order_text = "。".join(
            f"{i + 1}番 {self._state.name_of(pid)}"
            for i, pid in enumerate(self._ids))
        # 一覧更新直後の自動読み上げに割り込まれないよう少し遅らせる
        wx.CallLater(200, self._speaker.say,
                     "シャッフルしました。" + order_text)

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
        title = "結果の修正" if editing else "結果入力"
        super().__init__(parent,
                         title=f"{title}: {textgen.match_label(state, match)}",
                         size=(600, 480))
        n1 = state.name_of(match.slot1.resolved_id)
        n2 = state.name_of(match.slot2.resolved_id)
        needed = match.best_of // 2 + 1

        # 候補: n1側のスイープ→接戦、n2側の接戦→スイープ、最後に不戦勝
        self._cands: list[tuple[str, tuple[int, int, int, bool]]] = []
        for x in range(needed):
            self._cands.append((f"{n1} {needed}対{x}", (1, needed, x, False)))
        for x in range(needed - 1, -1, -1):
            self._cands.append((f"{n2} {needed}対{x}", (2, needed, x, False)))
        self._cands.append((f"{n1}の不戦勝({n2}の棄権)", (1, 0, 0, True)))
        self._cands.append((f"{n2}の不戦勝({n1}の棄権)", (2, 0, 0, True)))

        panel = wx.Panel(self)
        panel.SetName(title)
        s = wx.BoxSizer(wx.VERTICAL)

        head = f"{n1} 対 {n2}。結果を選んでEnter"
        if editing and match.result is not None:
            cur_w = state.name_of(match.winner_id())
            if match.result.forfeit:
                head = f"現在の結果: {cur_w}の不戦勝。" + head
            else:
                r = match.result
                ws, ls = ((r.score1, r.score2) if r.winner_slot == 1
                          else (r.score2, r.score1))
                head = f"現在の結果: {cur_w}の勝ち、{ws}対{ls}。" + head
        lbl = wx.StaticText(panel, label=head)
        s.Add(lbl, 0, wx.ALL, 8)

        self.listbox = wx.ListBox(panel, choices=[c[0] for c in self._cands])
        self.listbox.SetName("結果候補")
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
        ok = wx.Button(panel, label="決定")
        ok.SetName("決定")
        cancel = wx.Button(panel, label="キャンセル")
        cancel.SetName("キャンセル")
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
        super().__init__(parent, title="修正の影響確認", size=(600, 440))
        panel = wx.Panel(self)
        panel.SetName("修正の影響確認")
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(
            panel,
            label=f"この修正で以下の{len(match_ids)}件の試合結果がクリアされます。"
                  "よろしいですか?")
        s.Add(lbl, 0, wx.ALL, 8)

        lines = [textgen.match_line(state, state.matches[mid])
                 for mid in match_ids]
        self.listbox = wx.ListBox(panel, choices=lines)
        self.listbox.SetName("クリアされる試合の一覧")
        if lines:
            self.listbox.SetSelection(0)
        s.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, label="クリアして修正する")
        ok.SetName("クリアして修正する")
        cancel = wx.Button(panel, label="やめる")
        cancel.SetName("やめる")
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


HELP_LINES = [
    "Enter: 選択した試合の結果入力。入力済みの試合なら修正",
    "F5: 全体状況の読み上げ",
    "F6: ビュー切替(ラウンド別 → 選手別 → 次の試合 → 順位)",
    "Ctrl+Z: 直前の結果入力の取り消し",
    "Ctrl+N: 新規大会の作成",
    "Ctrl+O: 大会ファイルを開く",
    "Ctrl+S: 上書き保存 / Ctrl+Shift+S: 名前を付けて保存",
    "Ctrl+U: 今すぐアップロード(公開が有効なとき)",
    "公開メニュー: オンライン閲覧の設定、閲覧URLの発行とコピー",
    "結果入力: 上下で候補を選んでEnter。Escで中止",
    "並び順ダイアログ: F8でシャッフル",
    "順位ビュー: 大会終了で確定順位、進行中は敗退状況を表示",
    "結果入力のたびに data フォルダへ自動保存されます",
]


class HelpDialog(wx.Dialog):
    """キー操作一覧。"""

    def __init__(self, parent):
        super().__init__(parent, title="キー操作一覧", size=(640, 480))
        panel = wx.Panel(self)
        panel.SetName("キー操作一覧")
        s = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(panel, label="キー操作一覧。上下で読み上げ")
        s.Add(lbl, 0, wx.ALL, 8)
        self.listbox = wx.ListBox(panel, choices=HELP_LINES)
        self.listbox.SetName("キー操作一覧")
        self.listbox.SetSelection(0)
        s.Add(self.listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        close = wx.Button(panel, label="閉じる")
        close.SetName("閉じる")
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
    """オンライン公開の設定。OKで publisher に保存済み。"""

    def __init__(self, parent, publisher):
        super().__init__(parent, title="公開設定(オンライン閲覧)",
                         size=(680, 560))
        self._pub = publisher
        panel = wx.Panel(self)
        panel.SetName("公開設定")
        s = wx.BoxSizer(wx.VERTICAL)

        self.enable_check = wx.CheckBox(
            panel, label="オンライン公開を有効にする(結果入力のたびに自動アップロード)")
        self.enable_check.SetName("オンライン公開を有効にする")
        self.enable_check.SetValue(publisher.enabled)
        s.Add(self.enable_check, 0, wx.ALL, 8)

        lbl = wx.StaticText(panel, label="relay のURL")
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.url_ctrl = wx.TextCtrl(panel, value=publisher.relay_url)
        self.url_ctrl.SetName("relayのURL")
        s.Add(self.url_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        lbl = wx.StaticText(
            panel, label="管理キー(サーバー側と同じ値。設定済みなら空欄のままで変更なし)")
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.key_ctrl = wx.TextCtrl(panel)
        self.key_ctrl.SetName("管理キー")
        s.Add(self.key_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        lbl = wx.StaticText(panel, label="閲覧URL(友人に伝えるアドレス)")
        s.Add(lbl, 0, wx.LEFT | wx.TOP, 8)
        self.viewer_ctrl = wx.TextCtrl(panel, style=wx.TE_READONLY)
        self.viewer_ctrl.SetName("閲覧URL")
        self._update_viewer_url()
        s.Add(self.viewer_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.issue_btn = wx.Button(panel, label="閲覧URLを発行(再発行)")
        self.issue_btn.SetName("閲覧URLを発行")
        ok = wx.Button(panel, label="保存")
        ok.SetName("保存")
        cancel = wx.Button(panel, label="キャンセル")
        cancel.SetName("キャンセル")
        btns.Add(self.issue_btn, 0, wx.ALL, 8)
        btns.Add(ok, 0, wx.ALL, 8)
        btns.Add(cancel, 0, wx.ALL, 8)
        s.Add(btns, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)

        self.issue_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_issue())
        ok.Bind(wx.EVT_BUTTON, lambda e: self._on_ok())
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        panel.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.enable_check.SetFocus()

    def _update_viewer_url(self):
        if self._pub.token and self._pub.relay_url:
            self.viewer_ctrl.SetValue(self._pub.viewer_url())
        else:
            self.viewer_ctrl.SetValue("未発行(発行ボタンで作成)")

    def _require_relay_url(self) -> bool:
        """relay のURLが入っていなければ知らせて入力欄へ戻す。"""
        if self.url_ctrl.GetValue().strip() or self._pub.relay_url:
            return True
        wx.MessageBox("relayのURLを入力してください。", "公開設定",
                      wx.OK | wx.ICON_WARNING, self)
        self.url_ctrl.SetFocus()
        return False

    def _on_issue(self):
        if not self._require_relay_url():
            return
        if self._pub.token:
            ans = wx.MessageBox(
                "既存の閲覧URLは使えなくなります。再発行しますか?",
                "確認", wx.YES_NO | wx.ICON_QUESTION, self)
            if ans != wx.YES:
                return
        # URL生成にはこの時点の relay URL を反映する
        self._pub.relay_url = (self.url_ctrl.GetValue().strip()
                               or self._pub.relay_url)
        self._pub.ensure_token(rotate=True)
        self._update_viewer_url()
        self.viewer_ctrl.SetFocus()

    def _on_ok(self):
        if self.enable_check.GetValue() and not self._require_relay_url():
            return
        url = self.url_ctrl.GetValue().strip()
        if url:
            self._pub.relay_url = url
        key = self.key_ctrl.GetValue().strip()
        if key:
            self._pub.set_admin_key(key)
        self._pub.enabled = self.enable_check.GetValue()
        if self._pub.enabled and not self._pub.token:
            self._pub.ensure_token()
        self._pub.save()
        self.EndModal(wx.ID_OK)

    def _on_key(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        if _is_enter(code):
            if wx.Window.FindFocus() is self.issue_btn:
                self._on_issue()
            else:
                self._on_ok()
            return
        event.Skip()
