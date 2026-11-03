"""公開状態の管理と relay へのアップロード。

- 公開状態(有効/トークン/relay URL)は data/publish_state.json に保存
- 管理キーは keyring(サービス名 fg_tournament_relay)に保存。
  keyring が使えない環境では publish_state.json に平文で保存(フォールバック)
- アップロードは urllib(標準ライブラリ)のHTTPS POST。
  X-Admin-Key ヘッダで認証(URLに載せずアクセスログへの漏えいを避ける)
"""
from __future__ import annotations

import json
import secrets
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

DEFAULT_RELAY_URL = ""
KEYRING_SERVICE = "fg_tournament_relay"
KEYRING_USER = "admin_key"


class Publisher:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "publish_state.json"
        self.enabled = False
        self.token = ""
        self.relay_url = DEFAULT_RELAY_URL
        self._fallback_key: Optional[str] = None
        self._load()

    # ---------- 状態 ----------

    def _load(self) -> None:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.enabled = bool(d.get("enabled"))
        self.token = str(d.get("token", ""))
        self.relay_url = str(d.get("relay_url") or DEFAULT_RELAY_URL)
        self._fallback_key = d.get("admin_key") or None

    def save(self) -> None:
        self.data_dir.mkdir(exist_ok=True)
        d = {"enabled": self.enabled, "token": self.token,
             "relay_url": self.relay_url}
        if self._fallback_key:
            d["admin_key"] = self._fallback_key
        self.state_path.write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    def ensure_token(self, rotate: bool = False) -> str:
        if rotate or not self.token:
            self.token = secrets.token_hex(16)
            self.save()
        return self.token

    def viewer_url(self) -> str:
        if not self.relay_url:
            return ""
        base = self.relay_url.rsplit("/", 1)[0]
        return f"{base}/viewer.html?t={self.token}"

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
        """別スレッドでPOSTする。on_done はワーカースレッドから呼ばれるため、
        UI操作する場合は呼び出し側で wx.CallAfter を使うこと。"""
        t = threading.Thread(target=self._worker,
                             args=(snapshot, on_done), daemon=True)
        t.start()

    def _worker(self, snapshot: dict, on_done) -> None:
        ok, msg = self.upload_now(snapshot)
        if on_done is not None:
            on_done(ok, msg)

    def upload_now(self, snapshot: dict,
                   admin_key: Optional[str] = None) -> tuple[bool, str]:
        if not self.relay_url:
            return False, "relayのURLが未設定です"
        if not self.token:
            return False, "閲覧URLが未発行です"
        key = admin_key if admin_key is not None else self.get_admin_key()
        if not key:
            return False, "管理キーが未設定です"
        url = f"{self.relay_url}?action=put&token={self.token}"
        body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "X-Admin-Key": key})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ok"):
                    return True, "アップロードしました"
                return False, f"サーバー応答エラー: {data.get('error', '')}"
        except urllib.error.HTTPError as exc:
            return False, f"HTTPエラー {exc.code}"
        except Exception as exc:
            return False, f"通信エラー: {exc.__class__.__name__}"
