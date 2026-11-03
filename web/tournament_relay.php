<?php
// fg_tournament relay - トーナメント状態の保存/取得
//
// 保存: POST tournament_relay.php?action=put&token=<hex16-64>
//       ヘッダ X-Admin-Key: <管理キー> / ボディ: JSON (最大512KB)
// 取得: GET  tournament_relay.php?action=get&token=<hex16-64>
//
// デプロイ時に ADMIN_KEY を実際の値へ差し替えること。
declare(strict_types=1);

const ADMIN_KEY = '__ADMIN_KEY__';
const MAX_BYTES = 524288; // 512KB

header('X-Content-Type-Options: nosniff');

function fail(int $code, string $msg): void {
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

$dataDir = __DIR__ . '/t';
$action = isset($_GET['action']) ? (string)$_GET['action'] : '';
$token = isset($_GET['token']) ? (string)$_GET['token'] : '';

if (!preg_match('/^[a-f0-9]{16,64}$/', $token)) {
    fail(400, 'bad token');
}
$path = $dataDir . '/' . $token . '.json';

if ($action === 'diag') {
    // 診断用: キーの値は一切返さない(到達状況と設定状態のみ)
    $names = [];
    foreach ($_SERVER as $k => $_v) {
        if (strpos($k, 'HTTP_X_') === 0) {
            $names[] = $k;
        }
    }
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode([
        'ok' => true,
        'placeholder' => ADMIN_KEY === ('__ADMIN_' . 'KEY__'),
        'has_key_header' => isset($_SERVER['HTTP_X_ADMIN_KEY'])
            && $_SERVER['HTTP_X_ADMIN_KEY'] !== '',
        'x_headers' => $names,
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($action === 'get') {
    if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
        fail(405, 'method not allowed');
    }
    if (!is_file($path)) {
        fail(404, 'not found');
    }
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    readfile($path);
    exit;
}

if ($action === 'put') {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        fail(405, 'method not allowed');
    }
    $key = isset($_SERVER['HTTP_X_ADMIN_KEY'])
        ? (string)$_SERVER['HTTP_X_ADMIN_KEY'] : '';
    // 注意: プレースホルダ literal は const 行にのみ置く(デプロイ時の置換対象を一意にするため連結で書く)
    $placeholder = '__ADMIN_' . 'KEY__';
    if (ADMIN_KEY === $placeholder || !hash_equals(ADMIN_KEY, $key)) {
        fail(403, 'auth failed');
    }
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

fail(400, 'bad action');
