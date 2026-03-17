"""
契約代行アシスタント Telegram Bot
- グループチャット・個人チャット両対応
- Google Drive API経由でスプレッドシート読み書き
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

# 銀行データ
from banks_data import MAJOR_BANKS, search_banks

# ── Configuration ──────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8318667481:AAFhDLfJxELvcwF53ZOsjSBNcje0QIuCPxc")
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
REWARD_NAME = 100
TRANSFER_NAME = 200
TRANSFER_BANK = 201
TRANSFER_BANK_SEARCH = 202
TRANSFER_BRANCH = 203
TRANSFER_TYPE = 204
TRANSFER_ACCOUNT = 205
TRANSFER_AMOUNT = 206
TRANSFER_CONFIRM = 207
REPORT_NAME = 300
REPORT_SHOP = 301
REPORT_DATE = 302
REPORT_DATE_INPUT = 303
REPORT_MODEL = 304
REPORT_CAPACITY = 305
REPORT_QUANTITY = 306
REPORT_ADD_MORE = 307
REPORT_CONFIRM = 308
REGISTRATION_NAME = 400
REGISTRATION_ADDRESS = 401
REGISTRATION_PHOTO = 402
REGISTRATION_CONFIRM = 403


# ── Google Drive helpers ───────────────────────────────────────────────────
def get_access_token() -> str:
    # 環境変数からトークンを取得（Railway等のクラウド環境用）
    if GDRIVE_ACCESS_TOKEN:
        return GDRIVE_ACCESS_TOKEN
    # rclone設定からトークンを取得（ローカル環境用）
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
        f"https://www.googleapis.com/drive/v3/files/{SPREADSHEET_ID}"
        f"/export?mimeType=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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
        f"?uploadType=media"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    resp = requests.patch(url, headers=headers, data=buf.read())
    resp.raise_for_status()


def read_meiginin_list() -> list[dict]:
    wb = download_spreadsheet()
    ws = wb["稼働名義人リスト"]
    results = []
    for row in ws.iter_rows(min_row=3, values_only=True):
        name = row[1] if len(row) > 1 else None
        if not name or not str(name).strip():
            continue
        results.append({
            "名前": str(row[1]).strip() if row[1] else "",
            "ID": str(row[2]).strip() if len(row) > 2 and row[2] else "",
            "居住地": str(row[3]).strip() if len(row) > 3 and row[3] else "",
            "紹介者": str(row[4]).strip() if len(row) > 4 and row[4] else "",
            "台数": str(row[5]).strip() if len(row) > 5 and row[5] else "",
            "ステータス": str(row[6]).strip() if len(row) > 6 and row[6] else "",
        })
    return results


def append_to_sheet(sheet_name: str, headers: list[str], row_data: list[str]) -> None:
    wb = download_spreadsheet()
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb.create_sheet(sheet_name)
        ws.append(headers)
    ws.append(row_data)
    upload_spreadsheet(wb)


# ── /start ─────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ウェルカムメッセージ
    welcome_text = (
        "初めまして。\n\n"
        "案件の説明に入ります。\n\n"
        "〜お仕事の流れ〜\n"
        "auショップでこちらが指定する会社名義でiPhoneを契約していただきます。\n\n"
        "こちら近年横行しています叩きとは違い\n"
        "人を傷つけたりは無くクリーンな内容となっております。\n"
        "リスクなく誰にでもこなせますのでご検討よろしくお願いします。\n\n"
        "問題無ければ、メニューの方の代行登録フォームからご登録にお進みください。"
    )
    
    keyboard = [
        [InlineKeyboardButton("🪪 名刺印刷の手順", callback_data="meishi")],
        [InlineKeyboardButton("📄 登記簿謄本取得の手順", callback_data="touki")],
        [InlineKeyboardButton("💰 報酬確認", callback_data="reward")],
        [InlineKeyboardButton("🏦 振込依頼", callback_data="transfer")],
        [InlineKeyboardButton("📝 稼働報告", callback_data="report")],
        [InlineKeyboardButton("📋 代行登録フォーム", callback_data="registration")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    menu_text = "📋 *契約代行アシスタント*\n\nご利用になりたい機能を選択してください。"
    
    if update.message:
        # 初回実行時
        await update.message.reply_text(welcome_text)
        await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        # メニューに戻る時など
        await update.callback_query.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode="Markdown")


# ── 1. 名刺印刷の手順 ────────────────────────────────────────────────────
async def meishi_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    # コンビニ選択ボタン
    keyboard = [
        [InlineKeyboardButton("セブンイレブン", callback_data="meishi_seven")],
        [InlineKeyboardButton("ローソン", callback_data="meishi_lawson")],
        [InlineKeyboardButton("ファミリーマート", callback_data="meishi_famima")],
        [InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")],
    ]
    
    text = (
        "🪪 *名刺印刷の手順*\n\n"
        "まずアプリをダウンロードしてください：\n"
        "📱 iPhone: [App Store](https://apps.apple.com/app/id6479501537)\n"
        "📱 Android: [Google Play](https://play.google.com/store/apps/details?id=com.mymeishi)\n\n"
        "印刷するコンビニを選択してください。"
    )
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# ── 2. 登記簿謄本取得の手順 ──────────────────────────────────
async def touki_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    images_dir = "/home/ubuntu/telegram_bot/images"
    try:
        # 画像送信
        with open(f"{images_dir}/houmu_building.jpg", "rb") as f:
            await query.message.reply_photo(f, caption="🏢 法務局の外観例")
        with open(f"{images_dir}/hakkou_seikyu_ki_steps.png", "rb") as f:
            await query.message.reply_photo(f, caption="🖥️ 証明書発行請求機の使い方")
        with open(f"{images_dir}/application_form.jpg", "rb") as f:
            await query.message.reply_photo(f, caption="📝 交付請求書の記入例")
    except:
        pass

    text = (
        "📄 *登記簿謄本取得の手順*\n\n"
        "🏛️ *法務局窓口で取得する方法*\n\n"
        "1. **証明書発行請求機を使う（おすすめ）**\n"
        "・タッチパネルで会社名を入力して検索\n"
        "・整理番号票を受け取り、印紙（600円）を購入\n\n"
        "2. **手書き申請書で申請**\n"
        "・窓口の用紙に記入して提出\n\n"
        "💰 手数料: 1通600円"
    )
    keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# ── 3. 報酬確認 ───────────────────────────────────────────────────────────
async def reward_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💰 *報酬確認*\n\nお名前を入力してください。", parse_mode="Markdown")
    return REWARD_NAME

async def reward_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    members = read_meiginin_list()
    match = next((m for m in members if name in m["名前"]), None)
    if match:
        text = (
            f"✅ *名義人情報が見つかりました*\n\n"
            f"👤 *名前:* {match['名前']}\n"
            f"🆔 *ID:* {match['ID']}\n"
            f"📍 *居住地:* {match['居住地']}\n"
            f"📱 *台数:* {match['台数']}\n"
            f"📊 *ステータス:* {match['ステータス']}"
        )
    else:
        text = "❌ 該当する名前が見つかりませんでした。"
    
    keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ConversationHandler.END


# ── 4. 振込依頼 ───────────────────────────────────────────────────────────
async def transfer_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["transfer"] = {}
    await query.message.reply_text("🏦 *振込依頼*\n\n① お名前を入力してください。", parse_mode="Markdown")
    return TRANSFER_NAME

async def transfer_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"]["name"] = update.message.text.strip()
    
    # 主要銀行ボタン
    keyboard = []
    row = []
    for i, bank in enumerate(MAJOR_BANKS):
        row.append(InlineKeyboardButton(bank, callback_data=f"bank_sel_{bank}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔍 その他（検索）", callback_data="bank_search")])
    
    await update.message.reply_text(
        "② *銀行名* を選択してください。",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return TRANSFER_BANK

async def transfer_bank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "bank_search":
        await query.message.reply_text("銀行名の一部を入力して検索してください（例: 楽天）")
        return TRANSFER_BANK_SEARCH
    
    bank_name = query.data.replace("bank_sel_", "")
    context.user_data["transfer"]["bank"] = bank_name
    await query.message.reply_text(f"銀行名: *{bank_name}*\n\n③ *支店名* を入力してください。", parse_mode="Markdown")
    return TRANSFER_BRANCH

async def transfer_bank_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyword = update.message.text.strip()
    results = search_banks(keyword)
    
    if not results:
        await update.message.reply_text("❌ 該当する銀行が見つかりませんでした。再度入力してください。")
        return TRANSFER_BANK_SEARCH
    
    keyboard = []
    for bank in results[:10]: # 最大10件
        keyboard.append([InlineKeyboardButton(bank, callback_data=f"bank_sel_{bank}")])
    
    await update.message.reply_text(
        f"「{keyword}」の検索結果:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TRANSFER_BANK

async def transfer_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"]["branch"] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("普通", callback_data="transfer_type_普通"),
         InlineKeyboardButton("当座", callback_data="transfer_type_当座")]
    ]
    await update.message.reply_text("④ *口座種別* を選択してください。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return TRANSFER_TYPE

async def transfer_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    acc_type = query.data.replace("transfer_type_", "")
    context.user_data["transfer"]["type"] = acc_type
    await query.message.reply_text(f"口座種別: *{acc_type}*\n\n⑤ *口座番号* を入力してください。", parse_mode="Markdown")
    return TRANSFER_ACCOUNT

async def transfer_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"]["account"] = update.message.text.strip()
    await update.message.reply_text("⑥ *振込金額* を入力してください。", parse_mode="Markdown")
    return TRANSFER_AMOUNT

async def transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["transfer"]["amount"] = update.message.text.strip()
    d = context.user_data["transfer"]
    text = (
        "🏦 *振込依頼 — 確認*\n\n"
        f"👤 名前: {d['name']}\n"
        f"🏦 銀行: {d['bank']}\n"
        f"🏢 支店: {d['branch']}\n"
        f"📝 種別: {d['type']}\n"
        f"🔢 番号: {d['account']}\n"
        f"💰 金額: {d['amount']}円\n\n"
        "送信しますか？"
    )
    keyboard = [[InlineKeyboardButton("✅ 送信", callback_data="transfer_submit"),
                 InlineKeyboardButton("❌ キャンセル", callback_data="transfer_cancel")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return TRANSFER_CONFIRM

async def transfer_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data["transfer"]
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
    headers = ["タイムスタンプ", "名前", "銀行名", "支店名", "種別", "口座番号", "金額"]
    row = [now, d["name"], d["bank"], d["branch"], d["type"], d["account"], d["amount"]]
    append_to_sheet("振込依頼", headers, row)
    await query.message.reply_text("✅ 振込依頼を送信しました。")
    return ConversationHandler.END

async def transfer_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("❌ キャンセルしました。")
    return ConversationHandler.END


# ── 5. 稼働報告 ───────────────────────────────────────────────────────────
async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["report"] = {"iphones": []}
    await query.message.reply_text("📝 *稼働報告*\n\n① 稼働者名を入力してください。", parse_mode="Markdown")
    return REPORT_NAME

async def report_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["report"]["name"] = update.message.text.strip()
    keyboard = [[InlineKeyboardButton(f"{i}店舗", callback_data=f"shop_count_{i}")] for i in range(1, 5)]
    await update.message.reply_text("② *稼働店舗数* を選択してください。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REPORT_SHOP

async def report_shop_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    count = query.data.replace("shop_count_", "")
    context.user_data["report"]["shop_count"] = f"{count}店舗"
    today = datetime.now(JST).strftime("%Y/%m/%d")
    keyboard = [[InlineKeyboardButton(f"📅 今日（{today}）", callback_data="date_today")],
                [InlineKeyboardButton("✏️ 日付を入力", callback_data="date_manual")]]
    await query.message.reply_text("③ *稼働日* を選択してください。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REPORT_DATE

async def report_date_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "date_today":
        context.user_data["report"]["date"] = datetime.now(JST).strftime("%Y/%m/%d")
        keyboard = [[InlineKeyboardButton("iPhone 16", callback_data="model_iphone16")],
                    [InlineKeyboardButton("iPhone 16e", callback_data="model_iphone16e")]]
        await query.message.reply_text("④ *iPhone機種* を選択してください。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return REPORT_MODEL
    else:
        await query.message.reply_text("日付を入力してください（例: 2026/02/18）")
        return REPORT_DATE_INPUT

async def report_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["report"]["date"] = update.message.text.strip()
    keyboard = [[InlineKeyboardButton("iPhone 16", callback_data="model_iphone16")],
                [InlineKeyboardButton("iPhone 16e", callback_data="model_iphone16e")]]
    await update.message.reply_text("④ *iPhone機種* を選択してください。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REPORT_MODEL

async def report_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    model = query.data.replace("model_", "")
    context.user_data["report"]["current_model"] = "iPhone 16" if model == "iphone16" else "iPhone 16e"
    
    if model == "iphone16":
        caps = ["128GB", "256GB", "512GB", "1TB"]
    else:
        caps = ["128GB", "256GB", "512GB"]
    
    keyboard = [[InlineKeyboardButton(c, callback_data=f"cap_{c}")] for c in caps]
    await query.message.reply_text("⑤ *容量* を選択してください。", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REPORT_CAPACITY

async def report_capacity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cap = query.data.replace("cap_", "")
    context.user_data["report"]["current_cap"] = cap
    await query.message.reply_text("⑥ *台数* を入力してください。", parse_mode="Markdown")
    return REPORT_QUANTITY

async def report_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    qty = update.message.text.strip()
    d = context.user_data["report"]
    d["iphones"].append(f"{d['current_model']} {d['current_cap']} x {qty}台")
    
    keyboard = [[InlineKeyboardButton("➕ 別の機種を追加", callback_data="add_more")],
                [InlineKeyboardButton("✅ 入力完了", callback_data="add_done")]]
    await update.message.reply_text("別の機種を追加しますか？", reply_markup=InlineKeyboardMarkup(keyboard))
    return REPORT_ADD_MORE

async def report_add_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "add_more":
        keyboard = [[InlineKeyboardButton("iPhone 16", callback_data="model_iphone16")],
                    [InlineKeyboardButton("iPhone 16e", callback_data="model_iphone16e")]]
        await query.message.reply_text("iPhone機種を選択してください。", reply_markup=InlineKeyboardMarkup(keyboard))
        return REPORT_MODEL
    else:
        d = context.user_data["report"]
        text = (
            "📝 *稼働報告 — 確認*\n\n"
            f"👤 名前: {d['name']}\n"
            f"🏪 店舗数: {d['shop_count']}\n"
            f"📅 日付: {d['date']}\n"
            f"📱 詳細:\n" + "\n".join(d["iphones"]) + "\n\n"
            "送信しますか？"
        )
        keyboard = [[InlineKeyboardButton("✅ 送信", callback_data="report_submit"),
                     InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel")]]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return REPORT_CONFIRM

async def report_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data["report"]
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
    headers = ["タイムスタンプ", "名前", "店舗数", "日付", "詳細"]
    row = [now, d["name"], d["shop_count"], d["date"], " / ".join(d["iphones"])]
    append_to_sheet("稼働報告", headers, row)
    await query.message.reply_text("✅ 稼働報告を送信しました。")
    return ConversationHandler.END

async def report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("❌ キャンセルしました。")
    return ConversationHandler.END


# ── 6. 代行登録フォーム ──────────────────────────────────────────────────
async def registration_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["reg"] = {}
    await query.message.reply_text("📋 *代行登録フォーム*\n\n① お名前（漢字フルネーム）を入力してください。", parse_mode="Markdown")
    return REGISTRATION_NAME

async def registration_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["reg"]["name"] = update.message.text.strip()
    await update.message.reply_text("② *ご住所* を入力してください。", parse_mode="Markdown")
    return REGISTRATION_ADDRESS

async def registration_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["reg"]["address"] = update.message.text.strip()
    await update.message.reply_text("③ *身分証セルフィー* を送信してください（写真送信）。", parse_mode="Markdown")
    return REGISTRATION_PHOTO

async def registration_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]
    context.user_data["reg"]["file_id"] = photo.file_id
    d = context.user_data["reg"]
    text = (
        "📋 *代行登録フォーム — 確認*\n\n"
        f"👤 名前: {d['name']}\n"
        f"📍 住所: {d['address']}\n"
        "📸 写真を受信しました。\n\n"
        "送信しますか？"
    )
    keyboard = [[InlineKeyboardButton("✅ 送信", callback_data="reg_submit"),
                 InlineKeyboardButton("❌ キャンセル", callback_data="reg_cancel")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REGISTRATION_CONFIRM

async def registration_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data["reg"]
    user = query.from_user
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
    headers = ["タイムスタンプ", "User ID", "Username", "名前", "住所", "file_id"]
    row = [now, user.id, user.username or "なし", d["name"], d["address"], d["file_id"]]
    append_to_sheet("代行登録フォーム", headers, row)
    await query.message.reply_text("✅ 登録フォームを送信しました。")
    return ConversationHandler.END

async def registration_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("❌ キャンセルしました。")
    return ConversationHandler.END


# ── Common ─────────────────────────────────────────────────────────────
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("操作をキャンセルしました。")
    return ConversationHandler.END

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "メニューを表示"),
        BotCommand("cancel", "操作をキャンセル"),
    ])

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(reward_start, pattern="^reward$")],
        states={REWARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reward_name)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(transfer_start, pattern="^transfer$")],
        states={
            TRANSFER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_name)],
            TRANSFER_BANK: [CallbackQueryHandler(transfer_bank_callback, pattern="^bank_")],
            TRANSFER_BANK_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_bank_search)],
            TRANSFER_BRANCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_branch)],
            TRANSFER_TYPE: [CallbackQueryHandler(transfer_type, pattern="^transfer_type_")],
            TRANSFER_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_account)],
            TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_amount)],
            TRANSFER_CONFIRM: [CallbackQueryHandler(transfer_submit, pattern="^transfer_submit$"),
                               CallbackQueryHandler(transfer_cancel, pattern="^transfer_cancel$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(report_start, pattern="^report$")],
        states={
            REPORT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_name)],
            REPORT_SHOP: [CallbackQueryHandler(report_shop_button, pattern="^shop_count_")],
            REPORT_DATE: [CallbackQueryHandler(report_date_button, pattern="^date_")],
            REPORT_DATE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_date_input)],
            REPORT_MODEL: [CallbackQueryHandler(report_model, pattern="^model_")],
            REPORT_CAPACITY: [CallbackQueryHandler(report_capacity, pattern="^cap_")],
            REPORT_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_quantity)],
            REPORT_ADD_MORE: [CallbackQueryHandler(report_add_more_callback, pattern="^add_")],
            REPORT_CONFIRM: [CallbackQueryHandler(report_submit, pattern="^report_submit$"),
                             CallbackQueryHandler(report_cancel, pattern="^report_cancel$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(registration_start, pattern="^registration$")],
        states={
            REGISTRATION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_name)],
            REGISTRATION_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_address)],
            REGISTRATION_PHOTO: [MessageHandler(filters.PHOTO, registration_photo)],
            REGISTRATION_CONFIRM: [CallbackQueryHandler(registration_submit, pattern="^reg_submit$"),
                                   CallbackQueryHandler(registration_cancel, pattern="^reg_cancel$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True
    ))
    
    app.add_handler(CallbackQueryHandler(meishi_handler, pattern="^meishi$"))
    app.add_handler(CallbackQueryHandler(touki_handler, pattern="^touki$"))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"))
    
    app.run_polling()

if __name__ == "__main__":
    main()
