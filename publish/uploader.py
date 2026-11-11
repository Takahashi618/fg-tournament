"""公開状態の管理と relay へのアップロード。

- 公開状態(有効/トークン/relay URL)は data/publish_state.json に保存
- 管理キーは keyring(サービス名 fg_tournament_relay)に保存。
  keyring が使えない環境では publish_state.json に平文で保存(フォールバック)
- アップロードは urllib(標準ライブラリ)のHTTPS POST。
  X-Admin-Key ヘッダで認証(URLに載せずアクセスログへの漏えいを避ける)
- 本文は空白を省いたJSON(64人の総当たりリーグでも relay の上限 2MB に収める)
- 送信は1本ずつ。送信中に次の結果が入ったら、最新の1件だけを続けて送る
  (並行して送ると、古い内容が後から届いて新しい内容を上書きすることがある)
- relay のURLは https:// だけを受け付ける(管理キーを暗号化せずに流さないため)。
  手元での確認用に、このPC自身(localhost / 127.0.0.1)だけは http:// も通す
"""
from __future__ import annotations

import io
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from i18n import t

DEFAULT_RELAY_URL = ""
KEYRING_SERVICE = "fg_tournament_relay"
KEYRING_USER = "admin_key"


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
    # 送受信が止まったままの時間の上限(秒)。本文は小分けに送るので、
    # 大きな大会を遅い回線で送っても、進んでいる限りは打ち切らない
    timeout = 10

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "publish_state.json"
        self.enabled = False
        self.token = ""
        self.relay_url = DEFAULT_RELAY_URL
        self._fallback_key: Optional[str] = None
        self._lock = threading.Lock()
        self._pending: Optional[dict] = None
        self._pending_callbacks: list[Callable[[bool, str], None]] = []
        self._uploading = False
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
                     on_done: Optional[Callable[[bool, str], None]] = None
                     ) -> None:
        """別スレッドでPOSTする。送信中なら、送り終わってから最新の1件だけを送る
        (間の古い内容は送らない)。on_done は、その内容を含む送信の結果で呼ぶ。
        on_done はワーカースレッドから呼ばれるため、UI操作する場合は
        呼び出し側で wx.CallAfter を使うこと。"""
        with self._lock:
            self._pending = snapshot
            if on_done is not None:
                self._pending_callbacks.append(on_done)
            if self._uploading:
                return
            self._uploading = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            with self._lock:
                snapshot = self._pending
                callbacks = self._pending_callbacks
                self._pending = None
                self._pending_callbacks = []
                if snapshot is None:
                    self._uploading = False
                    return
            try:
                ok, msg = self.upload_now(snapshot)
            except Exception as exc:  # 送信の不具合でワーカーを止めない
                ok, msg = False, t("up.network_error",
                                   name=exc.__class__.__name__)
            for cb in callbacks:
                try:
                    cb(ok, msg)
                except Exception:
                    pass

    def _post(self, relay_url: str, action: str, token: str, body: bytes,
              admin_key: Optional[str]) -> tuple[bool, str]:
        """relay へ POST する。(成功したか, 失敗のときの文)。"""
        if not relay_url:
            return False, t("up.no_relay")
        if not is_safe_relay_url(relay_url):
            return False, t("up.need_https")
        if not token:
            return False, t("up.no_token")
        key = admin_key if admin_key is not None else self.get_admin_key()
        if not key:
            return False, t("up.no_key")
        url = f"{relay_url}?action={action}&token={token}"
        # 本文をファイルのように渡すと、http.client が小分けにして送る。
        # bytes のまま渡すと1回の送信になり、timeout が送信全体の締め切りになる
        # (0.7MB を10秒で送れない回線では、毎回失敗する)
        req = urllib.request.Request(
            url, data=io.BytesIO(body), method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "Content-Length": str(len(body)),
                     "X-Admin-Key": key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("ok"):
                    return True, ""
                err = data.get("error", "") if isinstance(data, dict) else ""
                return False, t("up.server_error", error=err)
        except urllib.error.HTTPError as exc:
            if exc.code == 413:
                # 以前の relay(上限512KB)か、Webサーバー自体の上限
                return False, t("up.too_large")
            return False, t("up.http_error", code=exc.code)
        except Exception as exc:
            return False, t("up.network_error",
                            name=exc.__class__.__name__)

    def upload_now(self, snapshot: dict,
                   admin_key: Optional[str] = None) -> tuple[bool, str]:
        body = json.dumps(snapshot, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        ok, msg = self._post(self.relay_url, "put", self.token, body, admin_key)
        return ok, (t("up.ok") if ok else msg)

    def delete_remote(self, token: str, relay_url: Optional[str] = None,
                      admin_key: Optional[str] = None) -> tuple[bool, str]:
        """サーバーに残っている閲覧データを消す(閲覧URLを作り直したとき用)。"""
        ok, msg = self._post(relay_url or self.relay_url, "delete", token,
                             b"{}", admin_key)
        return ok, (t("up.deleted") if ok else msg)

    def delete_remote_async(self, token: str, relay_url: Optional[str] = None,
                            on_done: Optional[Callable[[bool, str], None]] = None
                            ) -> None:
        def run() -> None:
            ok, msg = self.delete_remote(token, relay_url)
            if on_done is not None:
                on_done(ok, msg)

        threading.Thread(target=run, daemon=True).start()
