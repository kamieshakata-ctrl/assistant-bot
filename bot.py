"""
契約代行アシスタント Telegram Bot
機能:
1. 名刺の自動作成（法人一覧から選択してPNG生成）
2. 支払い依頼フォーム（銀行一覧選択・検索、スプレッドシートに記録）
3. 稼働データ入力フォーム（稼働者名・店舗数・日付・iPhone機種・容量・台数）
"""

import io
import json
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta

import requests
import openpyxl
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

from banks_data import MAJOR_BANKS, search_banks
from meishi_generator import create_business_card

# ── Configuration ──────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8789721641:AAFKm0JIBMZKcIhqc6htSgnwl3fvTj2PY2c")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "104JfX8b4VuE6T2yGKI6hLL58z3gZSKQ339TLnQ_Y2iI")
RCLONE_CONFIG = os.environ.get("RCLONE_CONFIG", "/home/ubuntu/.gdrive-rclone.ini")
GDRIVE_ACCESS_TOKEN = os.environ.get("GDRIVE_ACCESS_TOKEN", "")
JST = timezone(timedelta(hours=9))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────
# 名刺作成
MEISHI_SELECT = 100
MEISHI_PAGE = 101

# 支払い依頼
TRANSFER_NAME = 200
TRANSFER_BANK = 201
TRANSFER_BANK_SEARCH = 202
TRANSFER_BRANCH = 203
TRANSFER_TYPE = 204
TRANSFER_ACCOUNT = 205
TRANSFER_AMOUNT = 206
TRANSFER_CONFIRM = 207

# 稼働報告
REPORT_NAME = 300
REPORT_SHOP = 301
REPORT_DATE = 302
REPORT_DATE_INPUT = 303
REPORT_MODEL = 304
REPORT_CAPACITY = 305
REPORT_QUANTITY = 306
REPORT_ADD_MORE = 307
REPORT_CONFIRM = 308


# ── Google Drive helpers ───────────────────────────────────────────────────
def get_access_token() -> str:
    if GDRIVE_ACCESS_TOKEN:
        return GDRIVE_ACCESS_TOKEN
    result = subprocess.run(
        ["rclone", "config", "dump", "--config", RCLONE_CONFIG],
        capture_output=True, text=True,
    )
    config = json.loads(result.stdout)
    token_data = json.loads(config["manus_google_drive"]["token"])
    return token_data["access_token"]


def download_spreadsheet() -> openpyxl.Workbook:
    token = get_access_token()
    url = (
        f"https://www.googleapis.com/drive/v3/files/{SPREADSHEET_ID}/export"
        f"?mimeType=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return openpyxl.load_workbook(io.BytesIO(resp.content))


def upload_spreadsheet(wb: openpyxl.Workbook) -> None:
    token = get_access_token()
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    url = (
        f"https://www.googleapis.com/upload/drive/v3/files/{SPREADSHEET_ID}"
        f"?uploadType=media&convert=true"
    )
    requests.patch(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
        data=buf.read(),
    )


def get_or_create_sheet(wb: openpyxl.Workbook, sheet_name: str, headers: list) -> openpyxl.worksheet.worksheet.Worksheet:
    if sheet_name not in wb.sheetnames:
        ws = wb.create_sheet(sheet_name)
        ws.append(headers)
    else:
        ws = wb[sheet_name]
    return ws


# ── 法人一覧取得 ───────────────────────────────────────────────────────────
def get_hojin_list() -> list[dict]:
    """スプレッドシートの法人一覧シートから法人データを取得する"""
    try:
        wb = download_spreadsheet()
        if "法人一覧シート" not in wb.sheetnames:
            return []
        ws = wb["法人一覧シート"]
        hojin_list = []
        for row in ws.iter_rows(min_row=3, max_row=ws.max_row, values_only=True):
            if row[1]:  # B列: 法人名
                hojin = {
                    "name": str(row[1]),
                    "zip": str(row[2]) if row[2] and not str(row[2]).startswith("=") else "",
                    "pref": str(row[3]) if row[3] else "",
                    "city": str(row[4]) if row[4] else "",
                    "addr": str(row[5]) if row[5] else "",
                    "tel": str(row[6]) if row[6] else "",
                    "email": str(row[7]) if row[7] else "",
                    "tantousha": str(row[9]) if row[9] else "",
                }
                hojin_list.append(hojin)
        return hojin_list
    except Exception as e:
        logger.error(f"法人一覧取得エラー: {e}")
        return []


# ── /start コマンド ────────────────────────────────────────────────────────
WELCOME_MESSAGE = """初めまして。

案件の説明に入ります。

〜お仕事の流れ〜
auショップでこちらが指定する会社名義でiPhoneを契約していただきます。

こちら近年横行しています叩きとは違い
人を傷つけたりは無くクリーンな内容となっております。
リスクなく誰にでもこなせますのでご検討よろしくお願いします。

問題無ければ、メニューの方の代行登録フォームからご登録にお進みください。"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("🪪 名刺の自動作成", callback_data="menu_meishi")],
        [InlineKeyboardButton("🏦 支払い依頼フォーム", callback_data="menu_transfer")],
        [InlineKeyboardButton("📝 稼働データ入力フォーム", callback_data="menu_report")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(WELCOME_MESSAGE)
    await update.message.reply_text(
        "メニューを選択してください：",
        reply_markup=reply_markup,
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_meishi":
        await start_meishi(update, context)
    elif data == "menu_transfer":
        await start_transfer(update, context)
    elif data == "menu_report":
        await start_report(update, context)


# ═══════════════════════════════════════════════════════════════════════════
# 機能1: 名刺の自動作成
# ═══════════════════════════════════════════════════════════════════════════

MEISHI_PAGE_SIZE = 8  # 1ページあたりの法人数


async def start_meishi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    msg = await query.message.reply_text("⏳ 法人一覧を読み込み中...")
    hojin_list = get_hojin_list()

    if not hojin_list:
        await msg.edit_text("❌ 法人一覧の取得に失敗しました。")
        return ConversationHandler.END

    context.user_data["hojin_list"] = hojin_list
    context.user_data["meishi_page"] = 0
    await msg.delete()
    return await show_meishi_page(update, context, query.message)


async def show_meishi_page(update: Update, context: ContextTypes.DEFAULT_TYPE, message=None) -> int:
    hojin_list = context.user_data.get("hojin_list", [])
    page = context.user_data.get("meishi_page", 0)
    total = len(hojin_list)
    total_pages = (total + MEISHI_PAGE_SIZE - 1) // MEISHI_PAGE_SIZE

    start_idx = page * MEISHI_PAGE_SIZE
    end_idx = min(start_idx + MEISHI_PAGE_SIZE, total)
    page_items = hojin_list[start_idx:end_idx]

    keyboard = []
    for i, h in enumerate(page_items):
        idx = start_idx + i
        # 法人名を短縮表示（長すぎる場合）
        display_name = h["name"]
        if len(display_name) > 20:
            display_name = display_name[:19] + "…"
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"meishi_select_{idx}")])

    # ページナビゲーション
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀ 前へ", callback_data=f"meishi_page_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("次へ ▶", callback_data=f"meishi_page_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("❌ キャンセル", callback_data="meishi_cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"🪪 名刺を作成する法人を選択してください\n（{page + 1}/{total_pages}ページ、全{total}件）"

    if message:
        await message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)

    return MEISHI_SELECT


async def meishi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "meishi_cancel":
        await query.message.edit_text("❌ キャンセルしました。")
        return ConversationHandler.END

    if data.startswith("meishi_page_"):
        page = int(data.split("_")[-1])
        context.user_data["meishi_page"] = page
        return await show_meishi_page(update, context)

    if data.startswith("meishi_select_"):
        idx = int(data.split("_")[-1])
        hojin_list = context.user_data.get("hojin_list", [])
        if idx >= len(hojin_list):
            await query.message.edit_text("❌ エラーが発生しました。")
            return ConversationHandler.END

        hojin = hojin_list[idx]
        await query.message.edit_text(f"⏳ 「{hojin['name']}」の名刺を生成中...")

        try:
            # 住所を組み立て
            address_parts = [hojin["pref"], hojin["city"], hojin["addr"]]
            address = "".join(p for p in address_parts if p)

            # 名刺PNG生成
            png_data = create_business_card(
                hojin_name=hojin["name"],
                tel=hojin["tel"],
                address=address,
                tantousha=hojin["tantousha"],
            )

            # 送信
            from meishi_generator import generate_email
            email = generate_email(hojin["name"])

            caption = (
                f"🪪 **{hojin['name']}**\n"
                f"担当者: {hojin['tantousha'] or '未設定'}\n"
                f"TEL: {hojin['tel'] or '未設定'}\n"
                f"MAIL: {email}\n"
                f"住所: {address or '未設定'}"
            )

            await query.message.reply_photo(
                photo=io.BytesIO(png_data),
                caption=caption,
                parse_mode="Markdown",
            )
            await query.message.edit_text("✅ 名刺を生成しました。")

        except Exception as e:
            logger.error(f"名刺生成エラー: {e}")
            await query.message.edit_text(f"❌ 名刺の生成に失敗しました: {e}")

        return ConversationHandler.END

    return MEISHI_SELECT


# ═══════════════════════════════════════════════════════════════════════════
# 機能2: 支払い依頼フォーム
# ═══════════════════════════════════════════════════════════════════════════

async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"] = {}
    if update.callback_query:
        await update.callback_query.message.reply_text(
            "🏦 **支払い依頼フォーム**\n\nお名前を入力してください：",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "🏦 **支払い依頼フォーム**\n\nお名前を入力してください：",
            parse_mode="Markdown",
        )
    return TRANSFER_NAME


async def transfer_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"]["name"] = update.message.text.strip()

    # 主要銀行ボタン表示
    keyboard = []
    row = []
    for i, bank in enumerate(MAJOR_BANKS):
        row.append(InlineKeyboardButton(bank, callback_data=f"bank_{bank}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔍 その他（検索）", callback_data="bank_search")])
    keyboard.append([InlineKeyboardButton("❌ キャンセル", callback_data="transfer_cancel")])

    await update.message.reply_text(
        "銀行名を選択してください：",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TRANSFER_BANK


async def transfer_bank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "transfer_cancel":
        await query.message.edit_text("❌ キャンセルしました。")
        return ConversationHandler.END

    if data == "bank_search":
        await query.message.edit_text("🔍 銀行名の一部を入力してください（例：「三菱」「信金」など）：")
        return TRANSFER_BANK_SEARCH

    if data.startswith("bank_"):
        bank_name = data[5:]
        context.user_data["transfer"]["bank"] = bank_name
        await query.message.edit_text(f"✅ 銀行名：{bank_name}\n\n支店名を入力してください：")
        return TRANSFER_BRANCH

    return TRANSFER_BANK


async def transfer_bank_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyword = update.message.text.strip()
    results = search_banks(keyword)

    if not results:
        await update.message.reply_text(
            f"「{keyword}」に一致する銀行が見つかりませんでした。\n別のキーワードを入力してください："
        )
        return TRANSFER_BANK_SEARCH

    keyboard = []
    for bank in results[:10]:
        keyboard.append([InlineKeyboardButton(bank, callback_data=f"bank_{bank}")])
    keyboard.append([InlineKeyboardButton("🔍 再検索", callback_data="bank_search")])
    keyboard.append([InlineKeyboardButton("❌ キャンセル", callback_data="transfer_cancel")])

    await update.message.reply_text(
        f"「{keyword}」の検索結果：",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TRANSFER_BANK


async def transfer_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"]["branch"] = update.message.text.strip()

    keyboard = [
        [
            InlineKeyboardButton("普通", callback_data="type_普通"),
            InlineKeyboardButton("当座", callback_data="type_当座"),
        ]
    ]
    await update.message.reply_text(
        "口座種別を選択してください：",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return TRANSFER_TYPE


async def transfer_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    account_type = query.data.split("_")[1]
    context.user_data["transfer"]["type"] = account_type
    await query.message.edit_text(f"口座種別：{account_type}\n\n口座番号を入力してください：")
    return TRANSFER_ACCOUNT


async def transfer_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"]["account"] = update.message.text.strip()
    await update.message.reply_text("振込金額を入力してください（例：50000）：")
    return TRANSFER_AMOUNT


async def transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount_text = update.message.text.strip().replace(",", "").replace("円", "")
    try:
        amount = int(amount_text)
        context.user_data["transfer"]["amount"] = amount
    except ValueError:
        await update.message.reply_text("❌ 金額は数字で入力してください：")
        return TRANSFER_AMOUNT

    t = context.user_data["transfer"]
    confirm_text = (
        f"📋 **振込依頼の確認**\n\n"
        f"お名前：{t['name']}\n"
        f"銀行名：{t['bank']}\n"
        f"支店名：{t['branch']}\n"
        f"口座種別：{t['type']}\n"
        f"口座番号：{t['account']}\n"
        f"振込金額：¥{amount:,}\n\n"
        f"この内容で送信しますか？"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ 送信", callback_data="transfer_submit"),
            InlineKeyboardButton("❌ キャンセル", callback_data="transfer_cancel"),
        ]
    ]
    await update.message.reply_text(
        confirm_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return TRANSFER_CONFIRM


async def transfer_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "transfer_cancel":
        await query.message.edit_text("❌ キャンセルしました。")
        return ConversationHandler.END

    t = context.user_data["transfer"]
    try:
        wb = download_spreadsheet()
        ws = get_or_create_sheet(
            wb, "支払い依頼",
            ["タイムスタンプ", "お名前", "銀行名", "支店名", "口座種別", "口座番号", "振込金額"]
        )
        now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
        ws.append([now, t["name"], t["bank"], t["branch"], t["type"], t["account"], t["amount"]])
        upload_spreadsheet(wb)
        await query.message.edit_text("✅ 振込依頼を送信しました。")
    except Exception as e:
        logger.error(f"振込依頼送信エラー: {e}")
        await query.message.edit_text(f"❌ 送信に失敗しました: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# 機能3: 稼働データ入力フォーム
# ═══════════════════════════════════════════════════════════════════════════

async def start_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["report"] = {"iphones": []}
    if update.callback_query:
        await update.callback_query.message.reply_text(
            "📝 **稼働データ入力フォーム**\n\n稼働者名を入力してください：",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "📝 **稼働データ入力フォーム**\n\n稼働者名を入力してください：",
            parse_mode="Markdown",
        )
    return REPORT_NAME


async def report_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["report"]["name"] = update.message.text.strip()

    keyboard = [
        [
            InlineKeyboardButton("1店舗", callback_data="shop_1"),
            InlineKeyboardButton("2店舗", callback_data="shop_2"),
        ],
        [
            InlineKeyboardButton("3店舗", callback_data="shop_3"),
            InlineKeyboardButton("4店舗", callback_data="shop_4"),
        ],
    ]
    await update.message.reply_text(
        "稼働店舗数を選択してください：",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return REPORT_SHOP


async def report_shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    shops = query.data.split("_")[1]
    context.user_data["report"]["shops"] = shops

    today = datetime.now(JST).strftime("%Y/%m/%d")
    keyboard = [
        [InlineKeyboardButton(f"今日（{today}）", callback_data="date_today")],
        [InlineKeyboardButton("日付を入力する", callback_data="date_input")],
    ]
    await query.message.edit_text(
        f"稼働店舗数：{shops}店舗\n\n稼働日を選択してください：",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return REPORT_DATE


async def report_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "date_today":
        today = datetime.now(JST).strftime("%Y/%m/%d")
        context.user_data["report"]["date"] = today
        return await show_model_selection(query.message, context)
    else:
        await query.message.edit_text("稼働日を入力してください（例：2025/03/15）：")
        return REPORT_DATE_INPUT


async def report_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_text = update.message.text.strip()
    context.user_data["report"]["date"] = date_text
    return await show_model_selection(update.message, context)


async def show_model_selection(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    iphones = context.user_data["report"].get("iphones", [])
    iphone_summary = ""
    if iphones:
        iphone_summary = "\n".join([f"  • {ip['model']} {ip['capacity']} × {ip['qty']}台" for ip in iphones])
        iphone_summary = f"\n\n**登録済み機種：**\n{iphone_summary}"

    keyboard = [
        [
            InlineKeyboardButton("iPhone 16", callback_data="model_iPhone16"),
            InlineKeyboardButton("iPhone 16e", callback_data="model_iPhone16e"),
        ],
    ]
    if iphones:
        keyboard.append([InlineKeyboardButton("✅ 入力完了", callback_data="model_done")])
    keyboard.append([InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel")])

    await message.reply_text(
        f"iPhone機種を選択してください：{iphone_summary}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return REPORT_MODEL


async def report_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "report_cancel":
        await query.message.edit_text("❌ キャンセルしました。")
        return ConversationHandler.END

    if data == "model_done":
        return await show_report_confirm(query.message, context)

    model = data.split("_")[1]  # "iPhone16" or "iPhone16e"
    context.user_data["report"]["current_model"] = model

    if model == "iPhone16":
        capacities = ["128GB", "256GB", "512GB", "1TB"]
    else:
        capacities = ["128GB", "256GB", "512GB"]

    keyboard = [[InlineKeyboardButton(c, callback_data=f"cap_{c}")] for c in capacities]
    keyboard.append([InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel")])

    display_model = "iPhone 16" if model == "iPhone16" else "iPhone 16e"
    await query.message.edit_text(
        f"{display_model}の容量を選択してください：",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return REPORT_CAPACITY


async def report_capacity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "report_cancel":
        await query.message.edit_text("❌ キャンセルしました。")
        return ConversationHandler.END

    capacity = data.split("_")[1]
    context.user_data["report"]["current_capacity"] = capacity
    model = context.user_data["report"]["current_model"]
    display_model = "iPhone 16" if model == "iPhone16" else "iPhone 16e"

    await query.message.edit_text(
        f"{display_model} {capacity}\n\n台数を入力してください（例：3）："
    )
    return REPORT_QUANTITY


async def report_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    qty_text = update.message.text.strip()
    try:
        qty = int(qty_text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ 台数は1以上の整数で入力してください：")
        return REPORT_QUANTITY

    model = context.user_data["report"]["current_model"]
    capacity = context.user_data["report"]["current_capacity"]
    display_model = "iPhone 16" if model == "iPhone16" else "iPhone 16e"

    context.user_data["report"]["iphones"].append({
        "model": display_model,
        "capacity": capacity,
        "qty": qty,
    })

    keyboard = [
        [
            InlineKeyboardButton("➕ 別の機種も追加", callback_data="model_add"),
            InlineKeyboardButton("✅ 入力完了", callback_data="model_done"),
        ],
        [InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel")],
    ]
    iphones = context.user_data["report"]["iphones"]
    summary = "\n".join([f"  • {ip['model']} {ip['capacity']} × {ip['qty']}台" for ip in iphones])

    await update.message.reply_text(
        f"✅ 追加しました：{display_model} {capacity} × {qty}台\n\n**現在の登録：**\n{summary}\n\n続けますか？",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return REPORT_ADD_MORE


async def report_add_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "report_cancel":
        await query.message.edit_text("❌ キャンセルしました。")
        return ConversationHandler.END

    if data == "model_done":
        return await show_report_confirm(query.message, context)

    if data == "model_add":
        return await show_model_selection(query.message, context)

    return REPORT_ADD_MORE


async def show_report_confirm(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    r = context.user_data["report"]
    iphones = r.get("iphones", [])
    iphone_text = "\n".join([f"  • {ip['model']} {ip['capacity']} × {ip['qty']}台" for ip in iphones])

    confirm_text = (
        f"📋 **稼働データの確認**\n\n"
        f"稼働者名：{r['name']}\n"
        f"稼働店舗数：{r['shops']}店舗\n"
        f"稼働日：{r['date']}\n"
        f"iPhone詳細：\n{iphone_text}\n\n"
        f"この内容で送信しますか？"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ 送信", callback_data="report_submit"),
            InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel"),
        ]
    ]
    await message.reply_text(
        confirm_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return REPORT_CONFIRM


async def report_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "report_cancel":
        await query.message.edit_text("❌ キャンセルしました。")
        return ConversationHandler.END

    r = context.user_data["report"]
    iphones = r.get("iphones", [])
    iphone_detail = " / ".join([f"{ip['model']} {ip['capacity']} × {ip['qty']}台" for ip in iphones])

    try:
        wb = download_spreadsheet()
        ws = get_or_create_sheet(
            wb, "稼働報告",
            ["タイムスタンプ", "稼働者名", "稼働店舗数", "稼働日", "iPhone詳細"]
        )
        now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
        ws.append([now, r["name"], r["shops"], r["date"], iphone_detail])
        upload_spreadsheet(wb)
        await query.message.edit_text("✅ 稼働データを送信しました。")
    except Exception as e:
        logger.error(f"稼働報告送信エラー: {e}")
        await query.message.edit_text(f"❌ 送信に失敗しました: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# メイン
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    # /start コマンド
    app.add_handler(CommandHandler("start", start))

    # メニューコールバック
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))

    # 名刺作成 ConversationHandler
    meishi_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_meishi, pattern="^menu_meishi$")],
        states={
            MEISHI_SELECT: [CallbackQueryHandler(meishi_callback, pattern="^meishi_")],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(meishi_conv)

    # 支払い依頼 ConversationHandler
    transfer_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern="^menu_transfer$")],
        states={
            TRANSFER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_name)],
            TRANSFER_BANK: [CallbackQueryHandler(transfer_bank_callback, pattern="^(bank_|transfer_cancel)")],
            TRANSFER_BANK_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_bank_search),
                CallbackQueryHandler(transfer_bank_callback, pattern="^(bank_|transfer_cancel)"),
            ],
            TRANSFER_BRANCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_branch)],
            TRANSFER_TYPE: [CallbackQueryHandler(transfer_type_callback, pattern="^type_")],
            TRANSFER_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_account)],
            TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_amount)],
            TRANSFER_CONFIRM: [CallbackQueryHandler(transfer_confirm_callback, pattern="^transfer_")],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(transfer_conv)

    # 稼働報告 ConversationHandler
    report_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_report, pattern="^menu_report$")],
        states={
            REPORT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_name)],
            REPORT_SHOP: [CallbackQueryHandler(report_shop_callback, pattern="^shop_")],
            REPORT_DATE: [CallbackQueryHandler(report_date_callback, pattern="^date_")],
            REPORT_DATE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_date_input)],
            REPORT_MODEL: [CallbackQueryHandler(report_model_callback, pattern="^(model_|report_cancel)")],
            REPORT_CAPACITY: [CallbackQueryHandler(report_capacity_callback, pattern="^(cap_|report_cancel)")],
            REPORT_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_quantity)],
            REPORT_ADD_MORE: [CallbackQueryHandler(report_add_more_callback, pattern="^(model_|report_cancel)")],
            REPORT_CONFIRM: [CallbackQueryHandler(report_confirm_callback, pattern="^report_")],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(report_conv)

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
