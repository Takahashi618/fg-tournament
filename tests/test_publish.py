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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain import Config, Format, TournamentEngine
from publish import exporter
from publish.uploader import Publisher, is_safe_relay_url, viewer_url_for

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
ARRIVED: list = []
SLOW = threading.Event()


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
        action = q.get("action", [""])[0]
        if key != "testkey":
            self._send(403, {"ok": False, "error": "auth failed"})
            return
        if action == "delete":
            STORE.pop(token, None)
            self._send(200, {"ok": True})
            return
        if SLOW.is_set():
            time.sleep(0.3)  # 送信中に次の依頼が重なる状況を作る
        ARRIVED.append(json.loads(body.decode("utf-8")).get("seq"))
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
check(not (tmp_dir / "publish_state.json.tmp").exists(),
      "公開状態の保存: 一時ファイルが残らない")

# 続けて頼んでも、古い内容が後から届いて上書きしない
pub.get_admin_key = lambda: "testkey"  # keyring を使わずに送る
ARRIVED.clear()
SLOW.set()
results: list = []
finished = threading.Event()


def on_done(ok: bool, _msg: str) -> None:
    results.append(ok)
    if len(results) == 5:
        finished.set()


for seq in range(1, 6):
    pub.upload_async(dict(snap, seq=seq), on_done)
finished.wait(10)
SLOW.clear()
check(len(results) == 5 and all(results), f"連続送信: 5件とも結果が返る({results})")
check(ARRIVED == sorted(ARRIVED), f"連続送信: 届いた順が頼んだ順({ARRIVED})")
check(json.loads(STORE[tok].decode("utf-8")).get("seq") == 5,
      "連続送信: サーバーに残るのは最後の内容")
check(len(ARRIVED) < 5, f"連続送信: 待ちの間の古い内容は送らない({len(ARRIVED)}回送信)")

# 閲覧URLを作り直したときの、古い内容の削除
okd, msgd = pub.delete_remote(tok, admin_key="testkey")
check(okd and tok not in STORE, f"古い閲覧データの削除: {msgd}")
okd2, msgd2 = pub.delete_remote(tok, admin_key="wrongkey")
check(not okd2 and "403" in msgd2, f"削除も管理キーが要る: {msgd2}")

server.shutdown()

# 管理キーを暗号化せずに送らない(https だけ。このPC自身への http は確認用に通す)
for url, expected in (("https://example.com/r.php", True),
                      ("http://example.com/r.php", False),
                      ("http://127.0.0.1:8000/r.php", True),
                      ("http://localhost/r.php", True),
                      ("ftp://example.com/r.php", False),
                      ("example.com/r.php", False),
                      ("", False)):
    check(is_safe_relay_url(url) is expected, f"relay のURLの検査: {url!r} → {expected}")
pub.relay_url = "http://example.com/tournament_relay.php"
ok4, msg4 = pub.upload_now(snap, admin_key="testkey")
check(not ok4 and "https" in msg4, f"http の relay へは送らない: {msg4}")
check(viewer_url_for("https://example.com/x/relay.php", "abc")
      == "https://example.com/x/viewer.html?t=abc", "閲覧URLの組み立て")

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
               "MAX_BYTES", "'delete'", "require_admin"):
    check(marker in relay, f"relay.php に {marker}")
check("diag" not in relay, "relay.php に認証なしの診断用の入口が無い")
check(relay.count("require_admin();") == 2,
      "relay.php の put と delete が管理キーを確かめる")

# ---------- 結果出力 ----------
out = Path(__file__).parent / "result_publish.txt"
LINES.append("")
LINES.append(f"合計 {TOTAL} 件中 失敗 {FAIL} 件")
out.write_text("\n".join(LINES) + "\n", encoding="utf-8")
print(f"TOTAL={TOTAL} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
