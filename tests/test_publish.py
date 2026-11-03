"""公開層テスト(直接実行式)。

- exporter: スナップショットの構造と内容
- uploader: ローカルHTTPスタブに対する upload_now(認証成功/失敗)
- web ファイル: 重要マーカーの存在確認
結果は tests/result_publish.txt に出力。終了コード: 失敗ありなら1。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain import Config, Format, TournamentEngine
from publish import exporter
from publish.uploader import Publisher

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


# ---------- exporter ----------
note("=== exporter ===")
eng = TournamentEngine()
eng.setup(Config(title="公開テスト杯", format=Format.DOUBLE_ELIM))
eng.register_players(["田中", "鈴木", "佐藤", "伊藤"])
eng.generate()
eng.report("W1-1", 1, 2, 0)

snap = exporter.build_snapshot(eng, last_event="テストイベント")
check(snap["title"] == "公開テスト杯", "タイトル")
check(snap["format_name"] == "ダブルエリミネーション", "形式名")
check(snap["player_count"] == 4, "人数")
check(len(snap["matches"]) == 7, "試合数7(GFR含む)")
check(snap["last_event"] == "テストイベント", "last_event")
check(snap["finished"] is False and snap["champion"] is None, "進行中")
check(isinstance(snap["standings_lines"], list)
      and len(snap["standings_lines"]) >= 1, "順位行あり")
check(snap["league_table"] is None, "エリミ形式ではリーグ表なし")
check(snap["elimination_groups"] is not None, "進行中の敗退グループあり")

m0 = snap["matches"][0]
for key in ("id", "phase", "round", "index", "group", "label", "line",
            "p1", "p2", "state", "winner", "winner_slot", "s1", "s2",
            "forfeit", "auto"):
    if key not in m0:
        check(False, f"matchキー欠落: {key}")
        break
else:
    check(True, "matchの全キーが存在")

done_m = [m for m in snap["matches"] if m["id"] == "W1-1"][0]
check(done_m["winner"] == "田中" and done_m["s1"] == 2 and done_m["s2"] == 0,
      "W1-1の勝者とスコア")
try:
    json.dumps(snap, ensure_ascii=False)
    check(True, "JSONシリアライズ可能")
except Exception as exc:
    check(False, f"JSONシリアライズ不可: {exc}")

# 終了したリーグ戦のスナップショット
eng2 = TournamentEngine()
eng2.setup(Config(title="リーグ", format=Format.ROUND_ROBIN))
eng2.register_players(["A", "B", "C"])
eng2.generate()
while True:
    ready = eng2.ready_matches()
    if not ready:
        break
    mm = ready[0]
    eng2.report(mm.match_id, 1, 2, 0)
snap2 = exporter.build_snapshot(eng2)
check(snap2["finished"] is True, "リーグ終了")
check(snap2["league_table"] is not None
      and len(snap2["league_table"]) == 3, "リーグ表3行")
check(snap2["placements"] is not None, "確定順位あり")

# ---------- uploader (ローカルHTTPスタブ) ----------
note("=== uploader ===")

STORE: dict[str, bytes] = {}


class StubHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        token = q.get("token", [""])[0]
        key = self.headers.get("X-Admin-Key", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if key != "testkey":
            self._send(403, {"ok": False, "error": "auth failed"})
            return
        STORE[token] = body
        self._send(200, {"ok": True})

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
port = server.server_address[1]
th = threading.Thread(target=server.serve_forever, daemon=True)
th.start()

tmp_dir = Path(__file__).parent / "tmp_pub"
tmp_dir.mkdir(exist_ok=True)
pub = Publisher(tmp_dir)
pub.relay_url = f"http://127.0.0.1:{port}/tournament_relay.php"
tok = pub.ensure_token()
check(len(tok) == 32 and all(c in "0123456789abcdef" for c in tok),
      "トークンは32桁hex")
check(tok in pub.viewer_url() and "viewer.html?t=" in pub.viewer_url(),
      "閲覧URLにトークン")

ok, msg = pub.upload_now(snap, admin_key="testkey")
check(ok, f"アップロード成功: {msg}")
stored = json.loads(STORE[tok].decode("utf-8"))
check(stored["title"] == "公開テスト杯", "サーバー側に本文が保存された")

ok2, msg2 = pub.upload_now(snap, admin_key="wrongkey")
check(not ok2 and "403" in msg2, f"認証失敗を検出: {msg2}")

pub2 = Publisher(tmp_dir)
check(pub2.token == tok, "publish_state.json から状態を復元")

server.shutdown()

# relay のURLは既定で空(特定のサーバーへ送らない)
empty_dir = Path(tempfile.mkdtemp())
pub3 = Publisher(empty_dir)
check(pub3.relay_url == "", "relay のURLの既定は空")
check(pub3.viewer_url() == "", "relay のURLが無ければ閲覧URLも空")
pub3.ensure_token()
ok3, msg3 = pub3.upload_now(snap, admin_key="testkey")
check(not ok3 and "relay" in msg3, f"relay のURLが無いと送らない: {msg3}")
shutil.rmtree(empty_dir, ignore_errors=True)

# ---------- web ファイルのマーカー確認 ----------
note("=== web ファイル ===")
viewer = (ROOT / "web" / "viewer.html").read_text(encoding="utf-8")
for marker in ('lang="ja"', 'aria-live="polite"', 'scope', "action=get",
               "setInterval", "no-store"):
    check(marker in viewer, f"viewer.html に {marker}")
relay = (ROOT / "web" / "tournament_relay.php").read_text(encoding="utf-8")
for marker in ("hash_equals", "preg_match", "LOCK_EX", "X_ADMIN_KEY",
               "MAX_BYTES"):
    check(marker in relay, f"relay.php に {marker}")

# ---------- 結果出力 ----------
out = Path(__file__).parent / "result_publish.txt"
LINES.append("")
LINES.append(f"合計 {TOTAL} 件中 失敗 {FAIL} 件")
out.write_text("\n".join(LINES) + "\n", encoding="utf-8")
print(f"TOTAL={TOTAL} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
