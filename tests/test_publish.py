"""公開層テスト(直接実行式)。

- exporter: スナップショットの構造と内容
- uploader: ローカルHTTPスタブに対する upload_now(認証成功/失敗)
- web ファイル: 重要マーカーの存在確認
結果は tests/result_publish.txt に出力。終了コード: 失敗ありなら1。
"""
from __future__ import annotations

import json
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import i18n
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
check(snap["lang"] == "ja", "言語コード ja")

# 閲覧ページが読む項目だけを送る(版2)
BRACKET_KEYS = {"group", "line", "state", "phase", "round", "p1", "p2",
                "winner_slot", "s1", "s2", "forfeit", "auto"}
check(snap["snapshot_version"] == 2, "スナップショットの版は2")
check(all(set(m) == BRACKET_KEYS for m in snap["matches"]),
      f"エリミ形式の試合の項目 {sorted(set(snap['matches'][0]))}")

done_m = snap["matches"][0]
check(done_m["p1"] == "田中" and done_m["winner_slot"] == 1
      and done_m["s1"] == 2 and done_m["s2"] == 0,
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
check(all(set(m) == {"group", "line", "state"} for m in snap2["matches"]),
      "総当たりの試合は一覧に要る項目だけ")
check([r["place_label"] for r in snap2["league_table"]] == ["1位", "2位", "3位"]
      and snap2["placements"][0]["place_label"] == "1位",
      "順位の表記(place_label)")

# 英語で作ったスナップショット
i18n.set_language("en")
try:
    snap_en = exporter.build_snapshot(eng)
finally:
    i18n.set_language("ja")
check(snap_en["lang"] == "en", "英語: 言語コード en")
check(snap_en["format_name"] == "Double Elimination", "英語: 形式名")
check(snap_en["matches"][0]["line"].startswith("Winners Semifinal, Match 1"),
      f"英語: 試合の行({snap_en['matches'][0]['line']})")
check(snap_en["summary"].startswith("公開テスト杯, Double Elimination"),
      "英語: 全体状況(大会名は入力のまま)")
i18n.set_language("en")
try:
    snap2_en = exporter.build_snapshot(eng2)
finally:
    i18n.set_language("ja")
check([r["place_label"] for r in snap2_en["league_table"]]
      == ["1st", "2nd", "3rd"], "英語: 順位の表記(place_label)")

# ---------- uploader (ローカルHTTPスタブ) ----------
note("=== uploader ===")

STORE: dict[str, bytes] = {}
POSTS: list[str] = []          # 受け取った本文の title(届いた順)
DELAY_FIRST = threading.Event()  # セットすると、次の1件の保存を遅らせる


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
        if key == "toolarge":
            self._send(413, {"ok": False, "error": "too large"})
            return
        if key != "testkey":
            self._send(403, {"ok": False, "error": "auth failed"})
            return
        if q.get("action", [""])[0] == "delete":
            STORE.pop(token, None)
            self._send(200, {"ok": True})
            return
        if DELAY_FIRST.is_set():
            DELAY_FIRST.clear()
            time.sleep(0.8)
        POSTS.append(json.loads(body.decode("utf-8")).get("title", ""))
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

check(STORE[tok] == json.dumps(snap, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"),
      "本文は空白を省いたJSON")

ok2, msg2 = pub.upload_now(snap, admin_key="wrongkey")
check(not ok2 and "403" in msg2, f"認証失敗を検出: {msg2}")

ok4, msg4 = pub.upload_now(snap, admin_key="toolarge")
check(not ok4 and "413" in msg4 and "tournament_relay.php" in msg4,
      f"大きすぎるときは relay の置き直しを案内: {msg4}")
i18n.set_language("en")
try:
    ok5, msg5 = pub.upload_now(snap, admin_key="wrongkey")
finally:
    i18n.set_language("ja")
check(not ok5 and msg5 == "HTTP error 403", f"英語: 通信の結果({msg5})")

pub2 = Publisher(tmp_dir)
check(pub2.token == tok, "publish_state.json から状態を復元")

# 続けて送ると1本ずつ送り、送信中に来た分は最新の1件だけを送る
# (古い内容が後から届いて新しい内容を上書きしない)
pub_async = Publisher(tmp_dir)
pub_async.relay_url = pub.relay_url
pub_async.token = tok
pub_async.get_admin_key = lambda: "testkey"
results: list[tuple[bool, str]] = []
all_done = threading.Event()


def on_done(ok, msg):
    results.append((ok, msg))
    if len(results) == 3:
        all_done.set()


POSTS.clear()
DELAY_FIRST.set()
for title in ("1件目", "2件目", "3件目"):
    s_async = dict(snap)
    s_async["title"] = title
    pub_async.upload_async(s_async, on_done)
    time.sleep(0.1)
all_done.wait(10)
check(len(results) == 3 and all(ok for ok, _ in results),
      f"続けた送信: 3件とも結果を知らせる {results}")
check(POSTS == ["1件目", "3件目"],
      f"続けた送信: 送信中に来た分は最新だけ送る {POSTS}")
check(json.loads(STORE[tok].decode("utf-8"))["title"] == "3件目",
      "続けた送信: サーバーに残るのは最新の内容")

check(not (tmp_dir / "publish_state.json.tmp").exists(),
      "公開状態の保存: 一時ファイルが残らない")

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
pub_http = Publisher(Path(tempfile.mkdtemp()))
pub_http.relay_url = "http://example.com/tournament_relay.php"
pub_http.token = "cd" * 16
ok6, msg6 = pub_http.upload_now(snap, admin_key="testkey")
check(not ok6 and "https" in msg6, f"http の relay へは送らない: {msg6}")
check(viewer_url_for("https://example.com/x/relay.php", "abc")
      == "https://example.com/x/viewer.html?t=abc", "閲覧URLの組み立て")

# 大きな本文を遅い回線で送っても、送れている限り打ち切らない
# (本文を小分けに送る。まとめて送ると timeout が送信全体の締め切りになる)


class SlowReader(BaseHTTPRequestHandler):
    def setup(self):
        self.request.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        super().setup()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        got = 0
        while got < n:
            chunk = self.rfile.read(min(8192, n - got))
            got += len(chunk)
            time.sleep(len(chunk) / (100 * 1024))  # 毎秒100KBで読む
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


import http.client
_orig_connect = http.client.HTTPConnection.connect


def _small_buffer_connect(self):
    # 送信側のバッファも小さくして、遅い回線と同じように送信が待たされる状態にする
    _orig_connect(self)
    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)


slow = ThreadingHTTPServer(("127.0.0.1", 0), SlowReader)
threading.Thread(target=slow.serve_forever, daemon=True).start()
http.client.HTTPConnection.connect = _small_buffer_connect
try:
    pub_slow = Publisher(Path(tempfile.mkdtemp()))
    pub_slow.relay_url = (f"http://127.0.0.1:{slow.server_address[1]}"
                          "/tournament_relay.php")
    pub_slow.token = "ab" * 16
    # 1秒だと、PC が混んでいるときに1回の送信が待たされて落ちることがあった。
    # 全体(約4秒)より短ければ確かめたいことは同じなので、3秒にしている
    pub_slow.timeout = 3
    big = {"pad": "x" * (400 * 1024)}
    t0 = time.time()
    ok_slow, msg_slow = pub_slow.upload_now(big, admin_key="k")
    took = time.time() - t0
finally:
    http.client.HTTPConnection.connect = _orig_connect
    slow.shutdown()
check(ok_slow and took > 3.3,
      f"遅い回線: timeout(3秒)より長くかかっても送れる({msg_slow}, {took:.1f}秒)")

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
               "setInterval", 'cache: "no-cache"', "lastText", "place_label",
               "data-i18n", "STRINGS", "Tournament Viewer",
               # 画面の言語はデータの lang に合わせる(無ければ以前の版の日本語)
               'const dataLang = data.lang || "ja";',
               "if (dataLang !== lang) applyLanguage(dataLang);"):
    check(marker in viewer, f"viewer.html に {marker}")
relay = (ROOT / "web" / "tournament_relay.php").read_text(encoding="utf-8")
for marker in ("hash_equals", "preg_match", "LOCK_EX", "X_ADMIN_KEY",
               "MAX_BYTES", "2097152", "ETag", "HTTP_IF_NONE_MATCH",
               "http_response_code(304)", "gzencode", "'delete'",
               "require_admin"):
    check(marker in relay, f"relay.php に {marker}")
for marker in ("$_GET['have']", "'unchanged' => true"):
    check(marker in relay, f"relay.php に {marker}")
check("diag" not in relay, "relay.php に認証なしの診断用の入口が無い")
check(relay.count("require_admin();") == 2,
      "relay.php の put と delete が管理キーを確かめる")
for marker in ("notFoundFirst", "notFoundLater", "res.status === 404",
               "&have=", "data.unchanged !== true"):
    check(marker in viewer, f"viewer.html に {marker}")

# ---------- 結果出力 ----------
out = Path(__file__).parent / "result_publish.txt"
LINES.append("")
LINES.append(f"合計 {TOTAL} 件中 失敗 {FAIL} 件")
out.write_text("\n".join(LINES) + "\n", encoding="utf-8")
print(f"TOTAL={TOTAL} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
