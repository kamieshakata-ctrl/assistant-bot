/**
 * スプレッドシート Web API
 * Bot からの読み書きリクエストを処理する
 *
 * 対応アクション:
 *   GET  ?action=read&sheet=シート名         → シート全データをJSON返却
 *   POST {action:"append", sheet:"シート名", headers:[...], row:[...]}  → 行追加
 */

// スプレッドシートIDを直接指定（スタンドアロンプロジェクトのため必要）
var SPREADSHEET_ID = "104JfX8b4VuE6T2yGKI6hLL58z3gZSKQ339TLnQ_Y2iI";

// ── GET ハンドラ ──────────────────────────────────────────────────────────
function doGet(e) {
  try {
    var action = e.parameter.action || "read";
    var sheetName = e.parameter.sheet || "法人一覧シート";
    var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
    var ws = ss.getSheetByName(sheetName);

    if (!ws) {
      return jsonResponse({ ok: false, error: "Sheet not found: " + sheetName });
    }

    var data = ws.getDataRange().getValues();
    return jsonResponse({ ok: true, sheet: sheetName, data: data });

  } catch (err) {
    return jsonResponse({ ok: false, error: err.toString() });
  }
}

// ── POST ハンドラ ─────────────────────────────────────────────────────────
function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    var action = body.action || "append";
    var sheetName = body.sheet || "データ";
    var ss = SpreadsheetApp.openById(SPREADSHEET_ID);

    if (action === "append") {
      var ws = ss.getSheetByName(sheetName);

      // シートが存在しない場合は作成してヘッダーを書き込む
      if (!ws) {
        ws = ss.insertSheet(sheetName);
        if (body.headers && body.headers.length > 0) {
          ws.appendRow(body.headers);
        }
      }

      // 行データを追加
      if (body.row && body.row.length > 0) {
        ws.appendRow(body.row);
      }

      return jsonResponse({ ok: true, action: "append", sheet: sheetName });
    }

    return jsonResponse({ ok: false, error: "Unknown action: " + action });

  } catch (err) {
    return jsonResponse({ ok: false, error: err.toString() });
  }
}

// ── JSON レスポンスヘルパー ───────────────────────────────────────────────
function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
