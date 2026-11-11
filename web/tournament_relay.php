<?php
// fg_tournament relay - トーナメント状態の保存/取得
//
// 保存: POST tournament_relay.php?action=put&token=<hex16-64>
//       ヘッダ X-Admin-Key: <管理キー> / ボディ: JSON (最大2MB)
// 取得: GET  tournament_relay.php?action=get&token=<hex16-64>
//       ETag 付き。If-None-Match が一致すれば 304。Accept-Encoding: gzip なら圧縮して返す
//       &have=<前に受け取った ETag の中身> を付けると、内容が同じとき {"ok":true,"unchanged":true} だけを返す
//       (手前の中継が If-None-Match を取り除くサーバーでも、送り直しを省けるようにするため)
// 削除: POST tournament_relay.php?action=delete&token=<hex16-64>
//       ヘッダ X-Admin-Key: <管理キー>(閲覧URLを作り直したとき、古いデータを消す)
//
// デプロイ時に ADMIN_KEY を実際の値へ差し替えること。
declare(strict_types=1);

const ADMIN_KEY = '__ADMIN_KEY__';
// 64人の総当たりリーグ(2016試合)で約0.8MBになるため、余裕を見て2MB
const MAX_BYTES = 2097152; // 2MB

header('X-Content-Type-Options: nosniff');

function fail(int $code, string $msg): void {
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

// 管理キーを確かめる。合わなければ 403 で終わる
function require_admin(): void {
    $key = isset($_SERVER['HTTP_X_ADMIN_KEY'])
        ? (string)$_SERVER['HTTP_X_ADMIN_KEY'] : '';
    // 注意: プレースホルダ literal は const 行にのみ置く(デプロイ時の置換対象を一意にするため連結で書く)
    $placeholder = '__ADMIN_' . 'KEY__';
    if (ADMIN_KEY === $placeholder || !hash_equals(ADMIN_KEY, $key)) {
        fail(403, 'auth failed');
    }
}

$dataDir = __DIR__ . '/t';
$action = isset($_GET['action']) ? (string)$_GET['action'] : '';
$token = isset($_GET['token']) ? (string)$_GET['token'] : '';

if (!preg_match('/^[a-f0-9]{16,64}$/', $token)) {
    fail(400, 'bad token');
}
$path = $dataDir . '/' . $token . '.json';

if ($action === 'get') {
    if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
        fail(405, 'method not allowed');
    }
    if (!is_file($path)) {
        fail(404, 'not found');
    }
    $body = @file_get_contents($path);
    if ($body === false) {
        fail(500, 'read error');
    }
    // 閲覧ページは15秒ごとに取りに来る。内容が変わっていなければ 304 で本文を送らない
    // (64人の総当たりは数百KBになるため)。no-cache は「毎回確かめてから使う」の意味
    $etag = '"' . md5($body) . '"';
    header('Cache-Control: no-cache');
    header('ETag: ' . $etag);
    header('Vary: Accept-Encoding');
    $inm = isset($_SERVER['HTTP_IF_NONE_MATCH'])
        ? (string)$_SERVER['HTTP_IF_NONE_MATCH'] : '';
    foreach (explode(',', $inm) as $tag) {
        $tag = trim($tag);
        if (strncmp($tag, 'W/', 2) === 0) {
            $tag = substr($tag, 2);
        }
        if ($tag === $etag) {
            http_response_code(304);
            exit;
        }
    }
    header('Content-Type: application/json; charset=utf-8');
    // 閲覧ページが前に受け取った内容の印を URL で知らせてきたとき。
    // If-None-Match は、手前の中継(キャッシュ)が取り除いて届かないサーバーがある
    $have = isset($_GET['have']) ? (string)$_GET['have'] : '';
    if ($have !== '' && hash_equals($etag, '"' . $have . '"')) {
        echo json_encode(['ok' => true, 'unchanged' => true]);
        exit;
    }
    $accept = isset($_SERVER['HTTP_ACCEPT_ENCODING'])
        ? (string)$_SERVER['HTTP_ACCEPT_ENCODING'] : '';
    // サーバー側で圧縮する設定(zlib.output_compression)なら二重に圧縮しない
    if (function_exists('gzencode') && stripos($accept, 'gzip') !== false
            && !ini_get('zlib.output_compression')) {
        $gz = gzencode($body, 6);
        if ($gz !== false) {
            header('Content-Encoding: gzip');
            header('Content-Length: ' . strlen($gz));
            echo $gz;
            exit;
        }
    }
    header('Content-Length: ' . strlen($body));
    echo $body;
    exit;
}

if ($action === 'put') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        fail(405, 'method not allowed');
    }
    require_admin();
    $body = file_get_contents('php://input', false, null, 0, MAX_BYTES + 1);
    if ($body === false || strlen($body) === 0) {
        fail(400, 'empty body');
    }
    if (strlen($body) > MAX_BYTES) {
        fail(413, 'too large');
    }
    json_decode($body);
    if (json_last_error() !== JSON_ERROR_NONE) {
        fail(400, 'invalid json');
    }
    if (!is_dir($dataDir) && !@mkdir($dataDir, 0755, true)) {
        fail(500, 'storage error');
    }
    $tmp = $path . '.tmp';
    if (@file_put_contents($tmp, $body, LOCK_EX) === false) {
        fail(500, 'write error');
    }
    if (!@rename($tmp, $path)) {
        @unlink($tmp);
        fail(500, 'rename error');
    }
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => true], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($action === 'delete') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        fail(405, 'method not allowed');
    }
    require_admin();
    // 無ければ消えているのと同じなので成功にする
    if (is_file($path) && !@unlink($path)) {
        fail(500, 'delete error');
    }
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => true], JSON_UNESCAPED_UNICODE);
    exit;
}

fail(400, 'bad action');
