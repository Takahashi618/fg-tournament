"""公開状態の管理と relay へのアップロード。

- 公開状態(有効/トークン/relay URL)は data/publish_state.json に保存
- 管理キーは keyring(サービス名 fg_tournament_relay)に保存。
  keyring が使えない環境では publish_state.json に平文で保存(フォールバック)
- アップロードは urllib(標準ライブラリ)のHTTPS POST。
  X-Admin-Key ヘッダで認証(URLに載せずアクセスログへの漏えいを避ける)
- relay のURLは https:// だけを受け付ける(管理キーを暗号化せずに流さないため)。
  手元での確認用に、このPC自身(localhost / 127.0.0.1)だけは http:// も通す
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

DEFAULT_RELAY_URL = ""
KEYRING_SERVICE = "fg_tournament_relay"
KEYRING_USER = "admin_key"
URL_SCHEME_MESSAGE = "relayのURLは https:// で始まるものにしてください"

_DoneCallback = Callable[[bool, str], None]


def is_safe_relay_url(url: str) -> bool:
    """管理キーを送ってよいURLか。https か、このPC自身への http。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return (parts.scheme == "http"
            and parts.hostname in ("localhost", "127.0.0.1", "::1"))


def viewer_url_for(relay_url: str, token: str) -> str:
    if not relay_url or not token:
        return ""
    base = relay_url.rsplit("/", 1)[0]
    return f"{base}/viewer.html?t={token}"


def new_token() -> str:
    return secrets.token_hex(16)


class Publisher:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "publish_state.json"
        self.enabled = False
        self.token = ""
        self.relay_url = DEFAULT_RELAY_URL
        self._fallback_key: Optional[str] = None
        # 送信は1本のワーカーが順に行う。待ちは最新の1件だけを持つ
        self._lock = threading.Lock()
        self._pending: Optional[tuple[dict, list[_DoneCallback]]] = None
        self._worker_running = False
        self._load()

    # ---------- 状態 ----------

    def _load(self) -> None:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(d, dict):
            return
        self.enabled = bool(d.get("enabled"))
        self.token = str(d.get("token", ""))
        self.relay_url = str(d.get("relay_url") or DEFAULT_RELAY_URL)
        self._fallback_key = d.get("admin_key") or None

    def save(self) -> None:
        """一時ファイルに書いてから置き換える(書いている途中で落ちても元が残る)。"""
        self.data_dir.mkdir(exist_ok=True)
        d = {"enabled": self.enabled, "token": self.token,
             "relay_url": self.relay_url}
        if self._fallback_key:
            d["admin_key"] = self._fallback_key
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def ensure_token(self, rotate: bool = False) -> str:
        if rotate or not self.token:
            self.token = new_token()
            self.save()
        return self.token

    def viewer_url(self) -> str:
        return viewer_url_for(self.relay_url, self.token)

    # ---------- 管理キー ----------

    def set_admin_key(self, key: str) -> str:
        """キーを保存し、保存先("keyring" か "file")を返す。"""
        key = key.strip()
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            self._fallback_key = None
            self.save()
            return "keyring"
        except Exception:
            self._fallback_key = key
            self.save()
            return "file"

    def get_admin_key(self) -> Optional[str]:
        try:
            import keyring
            k = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if k:
                return k
        except Exception:
            pass
        return self._fallback_key

    # ---------- アップロード ----------

    def upload_async(self, snapshot: dict,
                     on_done: Optional[_DoneCallback] = None) -> None:
        """別スレッドでPOSTする。on_done はワーカースレッドから呼ばれるため、
        UI操作する場合は呼び出し側で wx.CallAfter を使うこと。

        送信は必ず1件ずつ、頼んだ順に行う。送信中に次が来たら、待ちを新しい内容で
        置き換える(古い内容が後から届いて、閲覧ページが前の状態へ戻るのを防ぐ)。
        置き換えられた分の on_done は、新しい内容の結果で呼ぶ。"""
        with self._lock:
            callbacks = list(self._pending[1]) if self._pending else []
            if on_done is not None:
                callbacks.append(on_done)
            self._pending = (snapshot, callbacks)
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            with self._lock:
                if self._pending is None:
                    self._worker_running = False
                    return
                snapshot, callbacks = self._pending
                self._pending = None
            try:
                ok, msg = self.upload_now(snapshot)
            except Exception as exc:
                ok, msg = False, f"送信エラー: {exc.__class__.__name__}"
            for cb in callbacks:
                try:
                    cb(ok, msg)
                except Exception:
                    pass

    def _post(self, relay_url: str, action: str, token: str, body: bytes,
              admin_key: Optional[str]) -> tuple[bool, str]:
        if not relay_url:
            return False, "relayのURLが未設定です"
        if not is_safe_relay_url(relay_url):
            return False, URL_SCHEME_MESSAGE
        if not token:
            return False, "閲覧URLが未発行です"
        key = admin_key if admin_key is not None else self.get_admin_key()
        if not key:
            return False, "管理キーが未設定です"
        url = f"{relay_url}?action={action}&token={token}"
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "X-Admin-Key": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("ok"):
                    return True, ""
                err = data.get("error", "") if isinstance(data, dict) else ""
                return False, f"サーバー応答エラー: {err}"
        except urllib.error.HTTPError as exc:
            return False, f"HTTPエラー {exc.code}"
        except Exception as exc:
            return False, f"通信エラー: {exc.__class__.__name__}"

    def upload_now(self, snapshot: dict,
                   admin_key: Optional[str] = None) -> tuple[bool, str]:
        body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        ok, msg = self._post(self.relay_url, "put", self.token, body, admin_key)
        return ok, ("アップロードしました" if ok else msg)

    def delete_remote(self, token: str, relay_url: Optional[str] = None,
                      admin_key: Optional[str] = None) -> tuple[bool, str]:
        """サーバーに残っている閲覧データを消す(閲覧URLを作り直したとき用)。"""
        ok, msg = self._post(relay_url or self.relay_url, "delete", token,
                             b"{}", admin_key)
        return ok, ("削除しました" if ok else msg)

    def delete_remote_async(self, token: str, relay_url: Optional[str] = None,
                            on_done: Optional[_DoneCallback] = None) -> None:
        def run() -> None:
            ok, msg = self.delete_remote(token, relay_url)
            if on_done is not None:
                on_done(ok, msg)

        threading.Thread(target=run, daemon=True).start()
