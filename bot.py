"""
契約代行アシスタント Telegram Bot
機能:
1. 名刺の自動作成（法人一覧から選択してPNG生成）
2. 支払い依頼フォーム（銀行一覧選択・検索、スプレッドシートに記録）
3. 稼働データ入力フォーム（稼働者名・店舗数・日付・iPhone機種・容量・台数）
"""

import hashlib
import hmac
import io
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import requests
from eth_account import Account
from google import genai
from google.genai import types
from openai import OpenAI
from web3 import Web3
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    BotCommand,
)
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8789721641:AAFzFEpNzKNrJoDTiDQCiqO0YeS2FYA6E9U")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "104JfX8b4VuE6T2yGKI6hLL58z3gZSKQ339TLnQ_Y2iI")
TRANSFER_NOTIFY_GROUP_ID = int(os.environ.get("TRANSFER_NOTIFY_GROUP_ID", "-5006222520"))  # 振込依頼通知先グループ
# 名刺作成機能の許可ユーザーネームリスト（@なしのユーザーネームで指定）
MEISHI_ALLOWED_USERS: set[str] = set(
    u.strip().lstrip("@").lower()
    for u in os.environ.get("MEISHI_ALLOWED_USERS", "kk_12345,ks19970606,kk1_12345").split(",")
    if u.strip()
)
GAS_URL = os.environ.get("GAS_URL", "")  # Google Apps Script Web App URL
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
JST = timezone(timedelta(hours=9))

# ── OpenAI クライアント ──────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
openai_client: OpenAI | None = None
if OPENAI_API_KEY:
    if OPENAI_BASE_URL:
        openai_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    else:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ── Gemini クライアント ──────────────────────────────────────────────────
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
GEMINI_MODEL = "gemini-2.5-flash"

# ── 仮想通貨送金設定 ─────────────────────────────────────────────────────
# 送金許可ユーザー（@なしのユーザーネームで指定）
CRYPTO_ALLOWED_USERS: set[str] = set(
    u.strip().lstrip("@").lower()
    for u in os.environ.get("CRYPTO_ALLOWED_USERS", "kk_12345,ks19970606").split(",")
    if u.strip()
)
# Trust Wallet ニーモニック（環境変数から取得）
TRUST_WALLET_MNEMONIC = os.environ.get("TRUST_WALLET_MNEMONIC", "")
# bitFlyer API認証情報（環境変数から取得）
BITFLYER_API_KEY = os.environ.get("BITFLYER_API_KEY", "")
BITFLYER_API_SECRET = os.environ.get("BITFLYER_API_SECRET", "")
# bitFlyer ETH入金アドレス（環境変数から取得）
BITFLYER_ETH_ADDRESS = os.environ.get("BITFLYER_ETH_ADDRESS", "")
# Ethereum RPC（Infura等）
ETH_RPC_URL = os.environ.get("ETH_RPC_URL", "https://rpc.ankr.com/eth")

SYSTEM_PROMPT = """あなたはau法人契約代行のアシスタントBotです。親切で丁寧に、簡潔に回答してください。

【サービス概要】
- au法人契約の代行サービスです
- スマホ1台の契約につき10,000円の報酬をお受け取りいただけます
- 法人契約のため、1日あたり5〜7万円前後の報酬が見込めます
- 今なら登録完了で、現金8,000円プレゼント

【契約の流れ】
1. 代行登録フォームから登録（名前・稼働エリア・身分証を提出）
2. 法務局で登記簿謄本を取得
3. 名刺を印刷（スタッフが自動作成します）
4. auショップで法人契約を行う
5. 契約したスマホを指定ロッカーに預け入れ
6. 確認後、報酬が銀行口座に振り込まれます

【報酬について】
- スマホ1台の契約につき10,000円
- 1日あたり5〜7台の契約が可能で、5〜7万円前後の報酬
- 報酬は支払い依頼フォームから申請後、銀行振込で支払われます

【登録について】
- 代行登録フォームから名前と稼働エリアを入力し、身分証の写真を送信するだけで登録完了
- 登録完了で現金8,000円プレゼント

【対応キャリアと支払い方法】
- au・UQモバイルともに法人契約が可能です
- au・UQモバイルともに法人契約で請求書払い（後払い）に対応しています
- 請求書払いにより、初期費用を抑えて契約できます

【注意事項】
- 具体的な法的アドバイスや、このサービスの範囲外の質問には回答できません
- 不明な点があれば、スタッフに確認するよう案内してください
- 回答は短く簡潔にしてください（200文字以内を目安）
"""

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────
# 名刺作成
MEISHI_SELECT = 100
MEISHI_PAGE = 101
MEISHI_NAME = 102  # 担当者名入力ステップ

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

# 代行登録
REG_INFO = 400   # 名前/稼働エリア入力
REG_ID_PHOTO = 401  # 身分証写真送信

# 仮想通貨送金
CRYPTO_AMOUNT = 500   # 送金額入力
CRYPTO_CONFIRM = 501  # 送金確認
CRYPTO_SELL_AMOUNT = 502  # ETH売却量入力
CRYPTO_SELL_CONFIRM = 503  # ETH売却確認
CRYPTO_WITHDRAW_AMOUNT = 504  # JPY出金額入力
CRYPTO_WITHDRAW_CONFIRM = 505  # JPY出金確認

HOJIN_NAME = 601
HOJIN_CONFIRM = 602


# ── Google Apps Script helpers ────────────────────────────────────────────

# ── 新規法人登録機能 ───────────────────────────────────────────────────────
import random
import string

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")

def _generate_twilio_050() -> str:
    """Twilio APIを使って050番号を取得する。未設定やエラーの場合はダミーを返す"""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return f"050-{random.randint(1000,9999)}-{random.randint(1000,9999)} (Twilio未設定)"
    
    try:
        # 実際にはここにTwilioのAvailablePhoneNumbers/JP/Local検索と購入APIが入る
        # ※購入にはRegulatory Bundle (IdentitySid/AddressSid) が必要なため、今回はモック実装
        return f"050-{random.randint(1000,9999)}-{random.randint(1000,9999)} (Twilio API連携準備中)"
    except Exception as e:
        logger.error(f"Twilio API Error: {e}")
        return "取得エラー"

async def start_hojin_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "🏢 **新規法人の登録**

登録する「法人名」を入力してください。
(例: 株式会社〇〇)",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("キャンセル", callback_data="hojin_cancel")]])
    )
    return HOJIN_NAME

async def hojin_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    
    # パース処理 (法人番号、名前、住所、最終更新日)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    number = ""
    name = ""
    address = ""
    
    for i, line in enumerate(lines):
        if line.startswith("法人番号："):
            number = line.replace("法人番号：", "").replace(" ", "")
            # 通常、次の行が法人名、その次が住所
            if i + 1 < len(lines):
                # 法人名とフリガナがスペースで区切られている場合の処理
                name_parts = lines[i+1].split()
                if len(name_parts) > 1 and "アーカイブ" in name_parts: # 特殊ケース対応
                   name = name_parts[-1] 
                else:
                   name = lines[i+1]
            if i + 2 < len(lines) and not lines[i+2].startswith("最終更新"):
                address = lines[i+2]
            break
            
    # もしパースに失敗したら、全体を名前として扱うフォールバック
    if not name:
        name = lines[0] if lines else "不明な法人"
        
    context.user_data["hojin_name"] = name
    context.user_data["hojin_number"] = number
    context.user_data["hojin_address"] = address
    
    # kamies.net の捨てメアドを自動生成 (API不要のCatch-all方式)
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    email = f"{random_str}@kamies.net"
    context.user_data["hojin_email"] = email
    
    await update.message.reply_text(
        f"以下の内容で登録・発番しますか？

"
        f"🏢 法人名: `{hojin_name}`
"
        f"📧 メール: `{email}`
"
        f"📞 電話番号: `Twilioから自動発番(050)`

"
        f"よろしければ「発行する」を押してください。",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 発行してシートに記録", callback_data="hojin_submit")],
            [InlineKeyboardButton("❌ キャンセル", callback_data="hojin_cancel")]
        ])
    )
    return HOJIN_CONFIRM

async def hojin_submit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "hojin_cancel":
        await query.message.reply_text("❌ 法人登録をキャンセルしました。")
        return ConversationHandler.END
        
    await query.message.reply_text("⏳ Twilioから050番号を取得し、シートに書き込んでいます...")
    
    hojin_name = context.user_data.get("hojin_name", "不明な法人")
    email = context.user_data.get("hojin_email", "")
    
    # Twilio APIで050発番
    phone_number = _generate_twilio_050()
    
    # スプレッドシートに書き込み (法人一覧シートと仮定)
    try:
        number = context.user_data.get("hojin_number", "")
        address = context.user_data.get("hojin_address", "")
        gas_append("法人一覧シート", ["法人番号", "法人名", "住所", "電話番号", "メールアドレス"], [number, hojin_name, address, phone_number, email])
        await query.message.reply_text(
            f"🎉 **登録完了！**

"
            f"🏢 法人名: `{hojin_name}`
"
            f"📞 050番号: `{phone_number}`
"
            f"📧 メール: `{email}`

"
            f"スプレッドシートに書き込みました！",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Sheet append error: {e}")
        await query.message.reply_text(f"❌ シートへの書き込みに失敗しました。
エラー: {e}")

    return ConversationHandler.END

def gas_read(sheet_name: str) -> list:
    """Apps Script経由でシートのデータを取得する"""
    if not GAS_URL:
        raise RuntimeError("GAS_URL が設定されていません")
    resp = requests.get(GAS_URL, params={"action": "read", "sheet": sheet_name}, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"GAS error: {result.get('error')}")
    return result.get("data", [])


def gas_append(sheet_name: str, headers: list, row: list) -> None:
    """Apps Script経由でシートに行を追加する"""
    if not GAS_URL:
        raise RuntimeError("GAS_URL が設定されていません")
    payload = {
        "action": "append",
        "sheet": sheet_name,
        "headers": headers,
        "row": row,
    }
    resp = requests.post(GAS_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"GAS error: {result.get('error')}")


# ── 法人一覧取得 ───────────────────────────────────────────────────────────
def get_hojin_list() -> list[dict]:
    """スプレッドシートの法人一覧シートから法人データを取得する（Apps Script経由）"""
    try:
        rows = gas_read("法人一覧シート")
        hojin_list = []
        for row in rows[2:]:  # 1行目はヘッダー、2行目はサブヘッダー
            if len(row) > 1 and row[1]:  # B列: 法人名
                hojin = {
                    "name": str(row[1]),
                    "zip": str(row[2]) if len(row) > 2 and row[2] and not str(row[2]).startswith("=") else "",
                    "pref": str(row[3]) if len(row) > 3 and row[3] else "",
                    "city": str(row[4]) if len(row) > 4 and row[4] else "",
                    "addr": str(row[5]) if len(row) > 5 and row[5] else "",
                    "tel": str(row[6]) if len(row) > 6 and row[6] else "",
                    "email": str(row[7]) if len(row) > 7 and row[7] else "",
                    "tantousha": str(row[9]) if len(row) > 9 and row[9] else "",
                }
                hojin_list.append(hojin)
        return hojin_list
    except Exception as e:
        logger.error(f"法人一覧取得エラー: {e}")
        return []


# ── /start コマンド ────────────────────────────────────────────────────────
WELCOME_MESSAGE = """スマホ1台の契約につき10,000円の報酬をお受け取りいただけます。法人契約のため、1日あたり5〜7万円前後の報酬が見込めます。

今なら登録完了で、現金8,000円プレゼント。

代行登録はコチら👇"""


# お仕事の流れ画像パス
OSHIGOTO_FLOW_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "oshigoto_flow.jpg")


def _is_meishi_allowed(update: Update) -> bool:
    """名刺作成機能の利用が許可されているユーザーか判定する"""
    user = update.effective_user
    if not user:
        return False
    username = (user.username or "").lower()
    return username in MEISHI_ALLOWED_USERS


def _is_crypto_allowed(update: Update) -> bool:
    """仮想通貨送金機能の利用が許可されているユーザーか判定する"""
    user = update.effective_user
    if not user:
        return False
    username = (user.username or "").lower()
    return username in CRYPTO_ALLOWED_USERS


def _make_reply_keyboard() -> ReplyKeyboardMarkup:
    """常時表示するキーボードメニューを生成する"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🏦 支払い依頼"), KeyboardButton("📝 稼働データ入力")],
            [KeyboardButton("📋 メニューを表示")],
        ],
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 一般ユーザー向け：代行登録ボタン + FAQ + 経費・持ち物
    welcome_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 代行登録フォーム", callback_data="menu_register")],
        [InlineKeyboardButton("❓ よくある質問", callback_data="faq_top")],
        [InlineKeyboardButton("💰 経費・持ち物について", callback_data="faq_expenses")],
    ])
    await update.message.reply_text(
        WELCOME_MESSAGE,
        reply_markup=welcome_markup,
    )

    # お仕事の流れインフォグラフィック画像を送信
    try:
        with open(OSHIGOTO_FLOW_IMAGE, "rb") as photo:
            await update.message.reply_photo(photo=photo)
    except Exception as e:
        logger.error(f"お仕事の流れ画像の送信に失敗: {e}")


# ── FAQ / 経費・持ち物 コールバックハンドラ ─────────────────────────────────────

FAQ_ITEMS = {
    "faq_black": {
        "q": "Q.携帯ブラックでも対応可能ですか",
        "a": (
            "法人の名義のため、窓口担当者様の信用情報は関係ありません。"
            "また、信用情報に傷がつくこともございません。"
        ),
    },
    "faq_reward": {
        "q": "Q.報酬はいつもらえますか",
        "a": (
            "契約完了後、動作確認を致します。\n"
            "その後、問題なければ\n\n"
            "🔸口座振込\n"
            "🔸暗号通貨\n\n"
            "をお選びいただけます。"
        ),
    },
    "faq_film": {
        "q": "Q.フィルムは剥がしてもいいですか？",
        "a": "フィルムは剥がさないでください。",
    },
    "faq_giga": {
        "q": "Q.ギガ数はどれを選べばいいですか？",
        "a": "できるだけ大きいモデル（容量）を選択してください。",
    },
    "faq_invoice": {
        "q": "Q.請求書払いはできますか？",
        "a": "店舗にてご確認ください。",
    },
    "faq_seal": {
        "q": "Q.印鑑が必要と言われたら？",
        "a": "その旨を店舗担当者にご確認いただくか、ご相談ください。",
    },
    "faq_option": {
        "q": "Q.オプションは何にしますか？",
        "a": "店舗指定のもの以外は不要（またはご相談）となります。",
    },
    "faq_color": {
        "q": "Q.色は何色にすればいいですか？",
        "a": "なんでもいいです。在庫のある色をお選びください。",
    },
    "faq_time": {
        "q": "Q.審査の時間はどれくらいですか？",
        "a": "審査の時間は、およそ1時間ほどです。",
    },
    "faq_money": {
        "q": "Q.前金などはありますか？",
        "a": "頭金がない店舗のみこちらでピックアップしますので、当日の支払いは発生しません。ご安心ください。",
    },
    "faq_people": {
        "q": "Q.お店には何人で入りますか？",
        "a": "基本的に1人です。分からないことはその都度メッセージで聞いていただければお答えします。",
    },
}

FAQ_EXPENSES_TEXT = (
    "≪経費・持ち物について≫\n\n"
    "【📄登記簿取得時】\n\n"
    "●印紙代が登記簿1枚につき、600円がかかります。"
    "電子マネー等は使えませんので、必ず現金をご用意ください。\n\n"
    "●支払い時にまとめて経費精算しますので、"
    "最初に立て替えをお願い致します。\n\n"
    "※難しい場合はご相談ください。\n\n"
    "●持ち物等は必要ありませんが、"
    "書類を入れておけるクリアファイルがあると便利です。"
)


async def faq_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """よくある質問・経費持ち物のコールバックハンドラ"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "faq_top":
        # FAQ一覧を表示
        keyboard = []
        for key, item in FAQ_ITEMS.items():
            keyboard.append([InlineKeyboardButton(item["q"], callback_data=key)])
        keyboard.append([InlineKeyboardButton("⬅️ 戻る", callback_data="faq_back_start")])
        await query.message.reply_text(
            "❓ **よくある質問**\n\n質問を選んでください。",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    elif data in FAQ_ITEMS:
        # 個別のFAQ回答を表示
        item = FAQ_ITEMS[data]
        keyboard = [
            [InlineKeyboardButton("⬅️ 質問一覧に戻る", callback_data="faq_top")],
            [InlineKeyboardButton("🏠 メニューに戻る", callback_data="faq_back_start")],
        ]
        await query.message.reply_text(
            f"**{item['q']}**\n\n{item['a']}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    elif data == "faq_expenses":
        # 経費・持ち物について
        keyboard = [
            [InlineKeyboardButton("🏠 メニューに戻る", callback_data="faq_back_start")],
        ]
        await query.message.reply_text(
            FAQ_EXPENSES_TEXT,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    elif data == "faq_back_start":
        # メニューに戻る（ウェルカムボタンを再表示）
        welcome_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 代行登録フォーム", callback_data="menu_register")],
            [InlineKeyboardButton("❓ よくある質問", callback_data="faq_top")],
            [InlineKeyboardButton("💰 経費・持ち物について", callback_data="faq_expenses")],
        ])
        await query.message.reply_text(
            WELCOME_MESSAGE,
            reply_markup=welcome_markup,
        )


async def staff_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """スタッフ用メニュー（許可ユーザーのみ）"""
    if not _is_meishi_allowed(update):
        await update.message.reply_text("❌ このコマンドは許可されたスタッフのみ利用可能です。")
        return

    # スタッフ用インラインボタンメニュー
    inline_keyboard = []
    inline_keyboard.append([InlineKeyboardButton("🏢 新規法人を登録 (050/Email発行)", callback_data="menu_hojin")])
    inline_keyboard.append([InlineKeyboardButton("🪦 名刺の自動作成", callback_data="menu_meishi")])
    inline_keyboard.append([InlineKeyboardButton("🏦 支払い依頼フォーム", callback_data="menu_transfer")])
    inline_keyboard.append([InlineKeyboardButton("📝 稼働データ入力フォーム", callback_data="menu_report")])
    # 送金機能は許可ユーザーのみ表示
    if _is_crypto_allowed(update):
        inline_keyboard.append([InlineKeyboardButton("💸 ETH送金・売却（bitFlyer）", callback_data="menu_crypto")])

    await update.message.reply_text(
        "🛠 **スタッフ用メニュー**",
        reply_markup=InlineKeyboardMarkup(inline_keyboard),
        parse_mode="Markdown"
    )
    # 常時表示キーボードを設置
    await update.message.reply_text(
        "↓ スタッフ用ショートカットキーボードを設置しました。",
        reply_markup=_make_reply_keyboard(),
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ConversationHandlerに属さないメニューコールバック用（フォールバック）"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_meishi":
        # 許可ユーザー以外は拒否
        if not _is_meishi_allowed(update):
            await query.answer("この機能は利用できません。", show_alert=True)
            return
        return await start_meishi(update, context)
    elif data == "menu_transfer":
        return await start_transfer(update, context)
    elif data == "menu_report":
        return await start_report(update, context)
    elif data == "menu_register":
        return await start_register(update, context)
    elif data == "menu_crypto":
        # 送金機能は許可ユーザーのみ
        if not _is_crypto_allowed(update):
            await query.answer("この機能は利用できません。", show_alert=True)
            return
        return await start_crypto(update, context)


# ═══════════════════════════════════════════════════════════════════════════
# 機能1: 名刺の自動作成
# ═══════════════════════════════════════════════════════════════════════════

MEISHI_PAGE_SIZE = 8  # 1ページあたりの法人数


async def start_meishi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    # 許可ユーザー以外は実行不可
    if not _is_meishi_allowed(update):
        await query.message.reply_text("❌ この機能は利用できません。")
        return ConversationHandler.END

    msg = await query.message.reply_text("⏳ 法人一覧を読み込み中...")
    hojin_list = get_hojin_list()

    if not hojin_list:
        await msg.edit_text("❌ 法人一覧の取得に失敗しました。")
        return ConversationHandler.END

    context.user_data["hojin_list"] = list(reversed(hojin_list))  # 新しい法人が先頭に来るよう逆順
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

        # 選択した法人を保存し、担当者名入力ステップへ
        hojin = hojin_list[idx]
        context.user_data["meishi_selected_hojin"] = hojin
        await query.message.edit_text(
            f"📝 **{hojin['name']}** の名刺を作成します。\n\n"
            f"担当者名を入力してください：",
            parse_mode="Markdown",
        )
        return MEISHI_NAME

    return MEISHI_SELECT


async def meishi_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """担当者名入力を受け取り名刺を生成する"""
    tantousha = update.message.text.strip()
    hojin = context.user_data.get("meishi_selected_hojin", {})

    if not hojin:
        await update.message.reply_text("❌ エラーが発生しました。最初からやり直してください。")
        return ConversationHandler.END

    msg = await update.message.reply_text(f"⏳ 「{hojin['name']}」 / 担当者: {tantousha} の名刺を生成中...")

    try:
        # 住所を組み立て
        address_parts = [hojin.get("pref", ""), hojin.get("city", ""), hojin.get("addr", "")]
        address = "".join(p for p in address_parts if p)

        # 名刺PNG生成（入力された担当者名を使用）
        png_data = create_business_card(
            hojin_name=hojin["name"],
            tel=hojin.get("tel", ""),
            address=address,
            tantousha=tantousha,
        )

        from meishi_generator import generate_email
        email = generate_email(hojin["name"])

        caption = (
            f"🪦 **{hojin['name']}**\n"
            f"担当者: {tantousha}\n"
            f"TEL: {hojin.get('tel', '') or '未設定'}\n"
            f"MAIL: {email}\n"
            f"住所: {address or '未設定'}"
        )

        await update.message.reply_photo(
            photo=io.BytesIO(png_data),
            caption=caption,
            parse_mode="Markdown",
        )
        await msg.edit_text("✅ 名刺を生成しました。")

    except Exception as e:
        logger.error(f"名刺生成エラー: {e}")
        await msg.edit_text(f"❌ 名刺の生成に失敗しました: {e}")

    return ConversationHandler.END


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
    # 口座種別は「普通」固定（ユーザーに選択させない）
    context.user_data["transfer"]["type"] = "普通"
    await update.message.reply_text("口座番号を入力してください：")
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
        now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
        gas_append(
            "支払い依頼",
            ["タイムスタンプ", "お名前", "銀行名", "支店名", "口座種別", "口座番号", "振込金額"],
            [now, t["name"], t["bank"], t["branch"], t["type"], t["account"], t["amount"]],
        )
        await query.message.edit_text("✅ 振込依頼を送信しました。")

        # ── 振込依頼内容をグループに自動転送 ──────────────────────────────
        notify_text = (
            f"💰 **振込依頼が届きました**\n\n"
            f"受付日時：{now}\n"
            f"お名前：{t['name']}\n"
            f"銀行名：{t['bank']}\n"
            f"支店名：{t['branch']}\n"
            f"口座種別：{t['type']}\n"
            f"口座番号：{t['account']}\n"
            f"振込金額：¥{t['amount']:,}"
        )
        try:
            await context.bot.send_message(
                chat_id=TRANSFER_NOTIFY_GROUP_ID,
                text=notify_text,
                parse_mode="Markdown",
            )
            logger.info(f"振込依頼をグループ {TRANSFER_NOTIFY_GROUP_ID} に転送しました")
        except Exception as notify_err:
            logger.error(f"グループへの転送に失敗しました: {notify_err}")

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
        now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
        gas_append(
            "稼働データ",
            ["タイムスタンプ", "稼働者名", "稼働店舗数", "稼働日", "iPhone詳細"],
            [now, r["name"], r["shops"], r["date"], iphone_detail],
        )
        await query.message.edit_text("✅ 稼働データを送信しました。")
    except Exception as e:
        logger.error(f"稼働報告送信エラー: {e}")
        await query.message.edit_text(f"❌ 送信に失敗しました: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# 機能4: 代行登録フォーム
# ═══════════════════════════════════════════════════════════════════════════

async def start_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """代行登録フォームの開始"""
    context.user_data["register"] = {}
    msg_text = (
        "📝 **代行登録フォーム**\n\n"
        "名前と稼働エリアを入力してください。\n"
        "例）代行太郎/東京都世田谷区"
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(msg_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, parse_mode="Markdown")
    return REG_INFO


async def reg_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """名前/稼働エリア入力を受け取る"""
    text = update.message.text.strip()
    # 「/」で分割
    if "/" in text:
        parts = text.split("/", 1)
        context.user_data["register"]["name"] = parts[0].strip()
        context.user_data["register"]["area"] = parts[1].strip()
    else:
        # 分割できない場合はそのまま保存
        context.user_data["register"]["name"] = text
        context.user_data["register"]["area"] = ""
    await update.message.reply_text(
        "📷 身分証の写真を送信してください。"
    )
    return REG_ID_PHOTO


# 代行登録通知グループID
REGISTER_NOTIFY_GROUP_ID = -5294992039


async def reg_id_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """身分証写真を受け取りスプレッドシートに保存する"""
    user = update.message.from_user
    tg_user_id = str(user.id)
    tg_username = f"@{user.username}" if user.username else f"ID:{user.id}"
    r = context.user_data["register"]
    name = r.get("name", "")
    area = r.get("area", "")
    name_area = f"{name}/{area}" if area else name

    try:
        now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
        gas_append(
            "代行登録",
            ["タイムスタンプ", "TGユーザーID", "TGユーザー名", "名前", "稼働エリア", "身分証"],
            [now, tg_user_id, tg_username, name, area, "送信済み"],
        )
        await update.message.reply_text(
            "✅ 登録が完了しました！\n担当者からご連絡いたします。しばらくお待ちください。"
        )
    except Exception as e:
        logger.error(f"代行登録保存エラー: {e}")
        await update.message.reply_text(f"❌ 登録に失敗しました: {e}")

    # グループへ通知と身分証画像の転送
    try:
        notify_text = (
            "🆕\n"
            "新規代行登録がありました。\n\n"
            f"・テレグラムID: {tg_username}\n"
            f"・名前/居住地: {name_area}\n"
            "・身分証画像: 下記参照"
        )
        await context.bot.send_message(
            chat_id=REGISTER_NOTIFY_GROUP_ID,
            text=notify_text,
        )
        # 身分証画像を転送
        if update.message.photo:
            photo = update.message.photo[-1]  # 最高解像度
            await context.bot.send_photo(
                chat_id=REGISTER_NOTIFY_GROUP_ID,
                photo=photo.file_id,
                caption=f"身分証 - {name_area} ({tg_username})",
            )
        elif update.message.document:
            await context.bot.send_document(
                chat_id=REGISTER_NOTIFY_GROUP_ID,
                document=update.message.document.file_id,
                caption=f"身分証 - {name_area} ({tg_username})",
            )
    except Exception as e:
        logger.error(f"代行登録グループ通知エラー: {e}")

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# メイン
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(application: Application) -> None:
    """起動時にset_my_commandsでコマンドメニューを登録する"""
    commands = [
        BotCommand("start", "利用開始・代行登録"),
        BotCommand("staff", "スタッフ専用メニュー"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands registered.")


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/menuコマンドやキーボードボタンからインラインメニューを表示する"""
    inline_keyboard = []
    if _is_meishi_allowed(update):
        inline_keyboard.append([InlineKeyboardButton("🏢 新規法人を登録 (050/Email発行)", callback_data="menu_hojin")])
    inline_keyboard.append([InlineKeyboardButton("🪦 名刺の自動作成", callback_data="menu_meishi")])
    inline_keyboard.append([InlineKeyboardButton("🏦 支払い依頼フォーム", callback_data="menu_transfer")])
    inline_keyboard.append([InlineKeyboardButton("📝 稼働データ入力フォーム", callback_data="menu_report")])
    inline_markup = InlineKeyboardMarkup(inline_keyboard)
    msg = update.message or (update.callback_query and update.callback_query.message)
    if msg:
        await msg.reply_text(
            "↓ 以下のボタンから選択してください：",
            reply_markup=inline_markup,
        )


async def _keyboard_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """常時表示キーボードのボタンテキストに応じて各機能を起動する"""
    text = update.message.text
    if text == "🏦 支払い依頼":
        await start_transfer(update, context)
    elif text == "📝 稼働データ入力":
        await start_report(update, context)
    else:  # "📋 メニューを表示"
        await show_menu(update, context)


# ═══════════════════════════════════════════════════════════════════════
# 機能5: 仮想通貨送金（Trust Wallet → bitFlyer ETH → JPY売却）
# ═══════════════════════════════════════════════════════════════════════

def _bitflyer_request(method: str, path: str, body: dict | None = None) -> dict:
    """bitFlyer Lightning APIへの認証付きリクエストを送信する"""
    timestamp = str(time.time())
    body_str = json.dumps(body, separators=(",", ":")) if body else ""
    text = timestamp + method + path + body_str
    sign = hmac.new(
        BITFLYER_API_SECRET.encode("utf-8"),
        text.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "ACCESS-KEY": BITFLYER_API_KEY,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-SIGN": sign,
        "Content-Type": "application/json",
    }
    url = "https://api.bitflyer.com" + path
    if method == "GET":
        resp = requests.get(url, headers=headers, timeout=30)
    else:
        resp = requests.post(url, headers=headers, data=body_str, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get_eth_balance_and_address() -> tuple[str, float]:
    """Trust WalletのニーモニックからETHアドレスと残高を取得する"""
    Account.enable_unaudited_hdwallet_features()
    acct = Account.from_mnemonic(TRUST_WALLET_MNEMONIC)
    w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))
    balance_wei = w3.eth.get_balance(acct.address)
    balance_eth = float(Web3.from_wei(balance_wei, "ether"))
    return acct.address, balance_eth


def _send_eth_to_bitflyer(amount_eth: float) -> str:
    """Trust WalletからbitFlyerのETH入金アドレスへETHを送金し、txハッシュを返す"""
    Account.enable_unaudited_hdwallet_features()
    acct = Account.from_mnemonic(TRUST_WALLET_MNEMONIC)
    w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))

    nonce = w3.eth.get_transaction_count(acct.address)
    gas_price = w3.eth.gas_price
    gas_limit = 21000
    amount_wei = Web3.to_wei(amount_eth, "ether")

    tx = {
        "nonce": nonce,
        "to": Web3.to_checksum_address(BITFLYER_ETH_ADDRESS),
        "value": amount_wei,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": 1,  # Ethereum Mainnet
    }
    signed = w3.eth.account.sign_transaction(tx, acct.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()


def _sell_eth_on_bitflyer(amount_eth: float) -> dict:
    """bitFlyer APIでETHを日本円に成行売却する"""
    body = {
        "product_code": "ETH_JPY",
        "child_order_type": "MARKET",
        "side": "SELL",
        "size": round(amount_eth, 8),
    }
    return _bitflyer_request("POST", "/v1/me/sendchildorder", body)


def _get_bitflyer_eth_balance() -> float:
    """bitFlyer口座のETH残高を取得する"""
    data = _bitflyer_request("GET", "/v1/me/getbalance")
    for item in data:
        if item.get("currency_code") == "ETH":
            return float(item.get("available", 0))
    return 0.0


def _get_bitflyer_jpy_balance() -> float:
    """bitFlyer口座のJPY残高を取得する"""
    data = _bitflyer_request("GET", "/v1/me/getbalance")
    for item in data:
        if item.get("currency_code") == "JPY":
            return float(item.get("available", 0))
    return 0.0


def _get_bitflyer_bank_accounts() -> list[dict]:
    """bitFlyerに登録された銀行口座一覧を取得する"""
    return _bitflyer_request("GET", "/v1/me/getbankaccounts")


def _withdraw_jpy_from_bitflyer(bank_account_id: int, amount: int) -> dict:
    """bitFlyerから銀行口座へJPYを出金する"""
    body = {
        "currency_code": "JPY",
        "bank_account_id": bank_account_id,
        "amount": amount,
    }
    return _bitflyer_request("POST", "/v1/me/withdraw", body)


def _get_eth_price_jpy() -> float:
    """bitFlyerのETH/JPY現在価格を取得する"""
    resp = requests.get(
        "https://api.bitflyer.com/v1/ticker",
        params={"product_code": "ETH_JPY"},
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json().get("ltp", 0))


async def start_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """仮想通貨統合メニュー（許可ユーザーのみ）"""
    # コールバッククエリとコマンド両対応
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        reply_func = query.message.reply_text
    else:
        reply_func = update.message.reply_text

    if not _is_crypto_allowed(update):
        await reply_func("❌ この機能は許可されたスタッフのみ利用可能です。")
        return ConversationHandler.END

    if not BITFLYER_API_KEY:
        await reply_func("❌ bitFlyer APIキーが設定されていません。")
        return ConversationHandler.END

    # 残高情報を取得
    await reply_func("⏳ 残高情報を取得中...")
    try:
        # Trust Wallet残高
        tw_address, tw_balance = "未設定", 0.0
        if TRUST_WALLET_MNEMONIC:
            try:
                tw_address, tw_balance = _get_eth_balance_and_address()
            except Exception as e:
                logger.warning(f"Trust Wallet balance fetch failed: {e}")
                tw_address, tw_balance = "取得エラー", 0.0

        # bitFlyer残高
        bf_eth = _get_bitflyer_eth_balance()
        bf_jpy = _get_bitflyer_jpy_balance()
        eth_price = _get_eth_price_jpy()
        tw_jpy = tw_balance * eth_price
        bf_eth_jpy = bf_eth * eth_price

        msg = (
            f"💰 **仮想通貨・口座管理メニュー**\n\n"
            f"━━ Trust Wallet ━━\n"
            f"アドレス: `{tw_address}`\n"
            f"ETH: `{tw_balance:.6f} ETH` (¥{tw_jpy:,.0f})\n\n"
            f"━━ bitFlyer 口座 ━━\n"
            f"ETH: `{bf_eth:.6f} ETH` (¥{bf_eth_jpy:,.0f})\n"
            f"JPY: `¥{bf_jpy:,.0f}`\n\n"
            f"ETH/JPY: `¥{eth_price:,.0f}`\n\n"
            f"下のボタンから操作を選択してください。"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 ETH送金（Wallet→bitFlyer）", callback_data="crypto_action_send")],
            [InlineKeyboardButton("💹 ETH売却（ETH→JPY）", callback_data="crypto_action_sell")],
            [InlineKeyboardButton("🏧 JPY出金（bitFlyer→銀行）", callback_data="crypto_action_withdraw")],
            [InlineKeyboardButton("🔄 残高更新", callback_data="crypto_action_refresh")],
            [InlineKeyboardButton("⬅️ 戻る", callback_data="crypto_action_back")],
        ])

        await reply_func(msg, reply_markup=keyboard, parse_mode="Markdown")
        context.user_data["crypto_wallet_balance"] = tw_balance
        context.user_data["crypto_eth_price"] = eth_price
        context.user_data["crypto_bf_eth"] = bf_eth
        context.user_data["crypto_bf_jpy"] = bf_jpy
        return CRYPTO_AMOUNT  # メニュー表示後、アクションコールバックで分岐
    except Exception as e:
        logger.error(f"残高確認エラー: {e}")
        await reply_func(f"❌ 残高確認に失敗しました。\nエラー: {e}")
        return ConversationHandler.END


async def crypto_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """統合メニューのアクションボタンコールバック"""
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "crypto_action_send":
        # ETH送金フロー
        if not TRUST_WALLET_MNEMONIC:
            await query.message.reply_text("❌ Trust Walletニーモニックが設定されていません。")
            return ConversationHandler.END
        balance = context.user_data.get("crypto_wallet_balance", 0)
        await query.message.reply_text(
            f"📤 **ETH送金（Trust Wallet → bitFlyer）**\n\n"
            f"Trust Wallet残高: `{balance:.6f} ETH`\n"
            f"送金先: `{BITFLYER_ETH_ADDRESS}`\n\n"
            f"送金するETH量を入力してください（例: `0.1`）\n"
            f"キャンセル: /cancel",
            parse_mode="Markdown",
        )
        context.user_data["crypto_current_action"] = "send"
        return CRYPTO_AMOUNT

    elif action == "crypto_action_sell":
        # ETH売却フロー
        bf_eth = context.user_data.get("crypto_bf_eth", 0)
        eth_price = context.user_data.get("crypto_eth_price", 0)
        if bf_eth < 0.001:
            await query.message.reply_text(
                f"⚠️ bitFlyer ETH残高が少なすぎます。\n"
                f"残高: `{bf_eth:.6f} ETH`\n"
                f"先にTrust WalletからETHを送金してください。",
                parse_mode="Markdown",
            )
            return ConversationHandler.END
        await query.message.reply_text(
            f"💹 **ETH売却（ETH → JPY）**\n\n"
            f"bitFlyer ETH残高: `{bf_eth:.6f} ETH`\n"
            f"現在価格: `¥{eth_price:,.0f}/ETH`\n\n"
            f"売却するETH量を入力してください（例: `0.1`）\n"
            f"全額売却する場合は `{bf_eth:.6f}` と入力\n"
            f"キャンセル: /cancel",
            parse_mode="Markdown",
        )
        context.user_data["crypto_current_action"] = "sell"
        return CRYPTO_AMOUNT

    elif action == "crypto_action_withdraw":
        # JPY出金フロー
        bf_jpy = context.user_data.get("crypto_bf_jpy", 0)
        if bf_jpy < 1:
            await query.message.reply_text(
                f"⚠️ bitFlyer JPY残高がありません。\n"
                f"残高: `¥{bf_jpy:,.0f}`\n"
                f"先にETHを売却してJPYに変換してください。",
                parse_mode="Markdown",
            )
            return ConversationHandler.END
        # 銀行口座一覧を取得
        try:
            banks = _get_bitflyer_bank_accounts()
            if not banks:
                await query.message.reply_text(
                    "❌ bitFlyerに銀行口座が登録されていません。\n"
                    "bitFlyerアプリから銀行口座を登録してください。"
                )
                return ConversationHandler.END
            # 銀行口座を表示
            bank_text = ""
            bank_buttons = []
            for b in banks:
                verified = "✅" if b.get("is_verified") else "❌未認証"
                bank_text += (
                    f"{verified} {b.get('bank_name', '')} {b.get('branch_name', '')}\n"
                    f"   口座: {b.get('account_type', '')} {b.get('account_number', '')}\n"
                    f"   名義: {b.get('account_name', '')}\n\n"
                )
                if b.get("is_verified"):
                    bank_buttons.append([InlineKeyboardButton(
                        f"🏦 {b.get('bank_name', '')} {b.get('account_number', '')}",
                        callback_data=f"crypto_bank_{b['id']}"
                    )])
            bank_buttons.append([InlineKeyboardButton("❌ キャンセル", callback_data="crypto_cancel")])

            await query.message.reply_text(
                f"🏧 **JPY出金（bitFlyer → 銀行口座）**\n\n"
                f"bitFlyer JPY残高: `¥{bf_jpy:,.0f}`\n\n"
                f"登録済み銀行口座:\n{bank_text}"
                f"出金先の口座を選択してください。",
                reply_markup=InlineKeyboardMarkup(bank_buttons),
                parse_mode="Markdown",
            )
            context.user_data["crypto_current_action"] = "withdraw"
            return CRYPTO_AMOUNT
        except Exception as e:
            logger.error(f"銀行口座取得エラー: {e}")
            await query.message.reply_text(f"❌ 銀行口座の取得に失敗しました。\nエラー: {e}")
            return ConversationHandler.END

    elif action == "crypto_action_refresh":
        # 残高更新（メニューを再表示）
        return await start_crypto(update, context)

    elif action == "crypto_action_back":
        await query.message.reply_text("⬅️ メニューに戻りました。")
        return ConversationHandler.END

    return ConversationHandler.END


async def crypto_bank_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """出金先銀行口座選択コールバック"""
    query = update.callback_query
    await query.answer()
    bank_id = int(query.data.replace("crypto_bank_", ""))
    context.user_data["crypto_withdraw_bank_id"] = bank_id
    bf_jpy = context.user_data.get("crypto_bf_jpy", 0)

    await query.message.reply_text(
        f"🏧 **JPY出金額を入力**\n\n"
        f"bitFlyer JPY残高: `¥{bf_jpy:,.0f}`\n\n"
        f"出金する金額（円）を入力してください（例: `50000`）\n"
        f"※ 出金手数料が別途かかります\n"
        f"キャンセル: /cancel",
        parse_mode="Markdown",
    )
    context.user_data["crypto_current_action"] = "withdraw_amount"
    return CRYPTO_AMOUNT


async def crypto_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """送金・売却・出金の金額入力を受け取り確認画面を表示する"""
    text = update.message.text.strip()
    action = context.user_data.get("crypto_current_action", "send")

    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError("0以下は無効")
    except ValueError:
        await update.message.reply_text("❌ 有効な数値を入力してください", parse_mode="Markdown")
        return CRYPTO_AMOUNT

    eth_price = context.user_data.get("crypto_eth_price", 0)

    if action == "send":
        # ETH送金確認
        balance = context.user_data.get("crypto_wallet_balance", 0)
        if amount > balance:
            await update.message.reply_text(
                f"❌ 残高不足です。\n残高: `{balance:.6f} ETH` / 指定: `{amount:.6f} ETH`",
                parse_mode="Markdown",
            )
            return CRYPTO_AMOUNT
        jpy_estimate = amount * eth_price
        context.user_data["crypto_send_amount"] = amount
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 送金を実行する", callback_data="crypto_execute_send")],
            [InlineKeyboardButton("❌ キャンセル", callback_data="crypto_cancel")],
        ])
        await update.message.reply_text(
            f"📋 **ETH送金確認**\n\n"
            f"送金量: `{amount:.6f} ETH`\n"
            f"概算円換算: `¥{jpy_estimate:,.0f}`\n"
            f"送金先: `{BITFLYER_ETH_ADDRESS}`\n\n"
            f"⚠️ 送金後は取り消しできません。実行しますか？",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return CRYPTO_CONFIRM

    elif action == "sell":
        # ETH売却確認
        bf_eth = context.user_data.get("crypto_bf_eth", 0)
        if amount > bf_eth:
            await update.message.reply_text(
                f"❌ bitFlyer ETH残高不足です。\n残高: `{bf_eth:.6f} ETH` / 指定: `{amount:.6f} ETH`",
                parse_mode="Markdown",
            )
            return CRYPTO_AMOUNT
        jpy_estimate = amount * eth_price
        context.user_data["crypto_sell_amount"] = amount
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 売却を実行する", callback_data="crypto_execute_sell")],
            [InlineKeyboardButton("❌ キャンセル", callback_data="crypto_cancel")],
        ])
        await update.message.reply_text(
            f"📋 **ETH売却確認**\n\n"
            f"売却量: `{amount:.6f} ETH`\n"
            f"概算受取額: `¥{jpy_estimate:,.0f}`\n\n"
            f"⚠️ 成行注文のため、約定価格は市場価格に依存します。実行しますか？",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return CRYPTO_CONFIRM

    elif action == "withdraw_amount":
        # JPY出金確認
        amount_int = int(amount)
        bf_jpy = context.user_data.get("crypto_bf_jpy", 0)
        if amount_int > bf_jpy:
            await update.message.reply_text(
                f"❌ JPY残高不足です。\n残高: `¥{bf_jpy:,.0f}` / 指定: `¥{amount_int:,}`",
                parse_mode="Markdown",
            )
            return CRYPTO_AMOUNT
        context.user_data["crypto_withdraw_amount"] = amount_int
        bank_id = context.user_data.get("crypto_withdraw_bank_id", 0)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 出金を実行する", callback_data="crypto_execute_withdraw")],
            [InlineKeyboardButton("❌ キャンセル", callback_data="crypto_cancel")],
        ])
        await update.message.reply_text(
            f"📋 **JPY出金確認**\n\n"
            f"出金額: `¥{amount_int:,}`\n"
            f"出金先口座ID: `{bank_id}`\n\n"
            f"⚠️ 出金手数料が別途かかります。実行しますか？",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return CRYPTO_CONFIRM

    return ConversationHandler.END


async def crypto_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """送金・売却・出金の確認コールバック"""
    query = update.callback_query
    await query.answer()

    if query.data == "crypto_cancel":
        await query.message.reply_text("❌ 操作をキャンセルしました。")
        return ConversationHandler.END

    if query.data == "crypto_execute_send":
        # ETH送金実行
        amount = context.user_data.get("crypto_send_amount", 0)
        if not amount:
            await query.message.reply_text("❌ 送金額が不明です。最初からやり直してください。")
            return ConversationHandler.END
        await query.message.reply_text(f"⏳ `{amount:.6f} ETH` をbitFlyerへ送金中...", parse_mode="Markdown")
        try:
            tx_hash = _send_eth_to_bitflyer(amount)
            await query.message.reply_text(
                f"✅ **ETH送金完了！**\n\n"
                f"送金量: `{amount:.6f} ETH`\n"
                f"TXハッシュ: `{tx_hash}`\n"
                f"Etherscan: https://etherscan.io/tx/{tx_hash}\n\n"
                f"⏳ bitFlyerへの入金確認後、「ETH売却」でJPYに変換できます。",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"ETH送金エラー: {e}")
            await query.message.reply_text(
                f"❌ **送金に失敗しました。**\n\nエラー: `{e}`",
                parse_mode="Markdown",
            )
        return ConversationHandler.END

    elif query.data == "crypto_execute_sell":
        # ETH売却実行
        amount = context.user_data.get("crypto_sell_amount", 0)
        if not amount:
            await query.message.reply_text("❌ 売却量が不明です。最初からやり直してください。")
            return ConversationHandler.END
        await query.message.reply_text(f"⏳ `{amount:.6f} ETH` を成行売却中...", parse_mode="Markdown")
        try:
            result = _sell_eth_on_bitflyer(amount)
            order_id = result.get("child_order_acceptance_id", "不明")
            await query.message.reply_text(
                f"✅ **ETH売却注文完了！**\n\n"
                f"売却量: `{amount:.6f} ETH`\n"
                f"注文ID: `{order_id}`\n\n"
                f"成行注文のため、約定価格は市場価格に依存します。\n"
                f"売却後、「JPY出金」で銀行口座に出金できます。",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"ETH売却エラー: {e}")
            await query.message.reply_text(
                f"❌ **売却に失敗しました。**\n\nエラー: `{e}`",
                parse_mode="Markdown",
            )
        return ConversationHandler.END

    elif query.data == "crypto_execute_withdraw":
        # JPY出金実行
        amount = context.user_data.get("crypto_withdraw_amount", 0)
        bank_id = context.user_data.get("crypto_withdraw_bank_id", 0)
        if not amount or not bank_id:
            await query.message.reply_text("❌ 出金情報が不明です。最初からやり直してください。")
            return ConversationHandler.END
        await query.message.reply_text(f"⏳ `¥{amount:,}` を銀行口座へ出金中...", parse_mode="Markdown")
        try:
            result = _withdraw_jpy_from_bitflyer(bank_id, amount)
            msg_id = result.get("message_id", "不明")
            await query.message.reply_text(
                f"✅ **JPY出金リクエスト完了！**\n\n"
                f"出金額: `¥{amount:,}`\n"
                f"メッセージID: `{msg_id}`\n\n"
                f"出金は通常翌営業日までに反映されます。\n"
                f"bitFlyerアプリで出金状況をご確認ください。",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"JPY出金エラー: {e}")
            await query.message.reply_text(
                f"❌ **出金に失敗しました。**\n\nエラー: `{e}`\n\n"
                f"※ 二段階認証が必要な場合、bitFlyerアプリから直接出金してください。",
                parse_mode="Markdown",
            )
        return ConversationHandler.END

    return ConversationHandler.END


async def crypto_sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """bitFlyer口座のETH残高を確認し、成行売却を実行する（許可ユーザーのみ）"""
    if not _is_crypto_allowed(update):
        await update.message.reply_text("❌ この機能は許可されたスタッフのみ利用可能です。")
        return

    if not BITFLYER_API_KEY or not BITFLYER_API_SECRET:
        await update.message.reply_text("❌ bitFlyer APIキーが設定されていません。")
        return

    await update.message.reply_text("⏳ bitFlyer残高を確認中...")
    try:
        eth_balance = _get_bitflyer_eth_balance()
        eth_price = _get_eth_price_jpy()
        jpy_estimate = eth_balance * eth_price

        if eth_balance < 0.001:
            await update.message.reply_text(
                f"⚠️ bitFlyer ETH残高が少なすぎます。\n"
                f"残高: `{eth_balance:.6f} ETH`\n"
                f"先にTrust WalletからETHを送金してください。",
                parse_mode="Markdown",
            )
            return

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ {eth_balance:.6f} ETH を全額売却", callback_data=f"sell_all_{eth_balance:.8f}")],
            [InlineKeyboardButton("❌ キャンセル", callback_data="sell_cancel")],
        ])
        await update.message.reply_text(
            f"💹 **bitFlyer ETH売却確認**\n\n"
            f"ETH残高: `{eth_balance:.6f} ETH`\n"
            f"現在価格: `¥{eth_price:,.0f}/ETH`\n"
            f"概算受取額: `¥{jpy_estimate:,.0f}`\n\n"
            f"全額売却しますか？",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"bitFlyer残高確認エラー: {e}")
        await update.message.reply_text(f"❌ bitFlyer残高確認に失敗しました。\nエラー: {e}")


async def crypto_sell_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """bitFlyer ETH売却確認コールバック"""
    query = update.callback_query
    await query.answer()

    if query.data == "sell_cancel":
        await query.message.reply_text("❌ 売却をキャンセルしました。")
        return

    if query.data.startswith("sell_all_"):
        amount_str = query.data.replace("sell_all_", "")
        try:
            amount = float(amount_str)
        except ValueError:
            await query.message.reply_text("❌ 売却量の取得に失敗しました。")
            return

        await query.message.reply_text(f"⏳ `{amount:.6f} ETH` を成行売却中...", parse_mode="Markdown")
        try:
            result = _sell_eth_on_bitflyer(amount)
            order_id = result.get("child_order_acceptance_id", "不明")
            await query.message.reply_text(
                f"✅ **ETH売却注文完了！**\n\n"
                f"売却量: `{amount:.6f} ETH`\n"
                f"注文ID: `{order_id}`\n\n"
                f"成行注文のため、約定価格は市場価格に依存します。\n"
                f"bitFlyerアプリで約定状況をご確認ください。",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"ETH売却エラー: {e}")
            await query.message.reply_text(
                f"❌ **売却に失敗しました。**\n\nエラー: `{e}`",
                parse_mode="Markdown",
            )


# ═══════════════════════════════════════════════════════════════════════
# LLM 自動応答
# ═══════════════════════════════════════════════════════════════════════

# ユーザーごとの会話履歴を保持（最大10往復）
MAX_HISTORY = 10
user_chat_history: dict[int, list[dict]] = {}


async def llm_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """メニューボタン以外のテキストメッセージにLLMで自動応答する"""
    # キーボードボタンのテキストはスキップ（別のハンドラで処理済み）
    text = update.message.text
    if text in ("🏦 支払い依頼", "📝 稼働データ入力", "📋 メニューを表示"):
        return  # キーボードボタンは別ハンドラで処理

    if not openai_client:
        await update.message.reply_text("申し訳ございません。現在自動応答機能が利用できません。")
        return

    user_id = update.effective_user.id

    # 会話履歴を取得・更新
    if user_id not in user_chat_history:
        user_chat_history[user_id] = []
    history = user_chat_history[user_id]
    history.append({"role": "user", "content": text})

    # 履歴が長すぎる場合は古いものを削除
    if len(history) > MAX_HISTORY * 2:
        history[:] = history[-(MAX_HISTORY * 2):]

    # OpenAI APIで応答を生成
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )
        reply_text = response.choices[0].message.content.strip()

        # アシスタントの応答を履歴に追加
        history.append({"role": "assistant", "content": reply_text})

        await update.message.reply_text(reply_text)
    except Exception as e:
        logger.error(f"LLM応答エラー: {e}")
        await update.message.reply_text(
            "申し訳ございません、一時的に応答できません。\n"
            "代行登録をご希望の場合は /start を送信してください。"
        )


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # /start コマンド
    app.add_handler(CommandHandler("start", start))

    # /menu コマンド
    app.add_handler(CommandHandler("menu", show_menu))

    # /staff コマンド
    app.add_handler(CommandHandler("staff", staff_menu))

    # キーボード「📋 メニューを表示」ボタンのハンドラ（インラインメニューを表示）
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^📋 メニューを表示$"),
        show_menu,
    ))


    # 法人登録 ConversationHandler
    hojin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_hojin_register, pattern="^menu_hojin$")],
        states={
            HOJIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, hojin_name_input)],
            HOJIN_CONFIRM: [CallbackQueryHandler(hojin_submit_callback, pattern="^hojin_")],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: (u.message.reply_text("❌ キャンセルしました"), ConversationHandler.END)[1]),
            CallbackQueryHandler(menu_callback, pattern="^menu_"),
            CallbackQueryHandler(lambda u,c: (u.callback_query.message.reply_text("キャンセルしました"), ConversationHandler.END)[1], pattern="^hojin_cancel$")
        ],
        per_user=True, per_chat=True, allow_reentry=True
    )
    app.add_handler(hojin_conv)
    # 名刺作成 ConversationHandler
    meishi_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_meishi, pattern="^menu_meishi$")],
        states={
            MEISHI_SELECT: [CallbackQueryHandler(meishi_callback, pattern="^meishi_")],
            MEISHI_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, meishi_name_input)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(menu_callback, pattern="^menu_"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(meishi_conv)

    # 支払い依頼 ConversationHandler
    transfer_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_transfer, pattern="^menu_transfer$"),
            CommandHandler("transfer", start_transfer),
            MessageHandler(filters.TEXT & filters.Regex(r"^🏦 支払い依頼$"), start_transfer),
        ],
        states={
            TRANSFER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_name)],
            TRANSFER_BANK: [CallbackQueryHandler(transfer_bank_callback, pattern="^(bank_|transfer_cancel)")],
            TRANSFER_BANK_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_bank_search),
                CallbackQueryHandler(transfer_bank_callback, pattern="^(bank_|transfer_cancel)"),
            ],
            TRANSFER_BRANCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_branch)],
            TRANSFER_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_account)],
            TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, transfer_amount)],
            TRANSFER_CONFIRM: [CallbackQueryHandler(transfer_confirm_callback, pattern="^transfer_")],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(menu_callback, pattern="^menu_"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(transfer_conv)

    # 稼働報告 ConversationHandler
    report_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_report, pattern="^menu_report$"),
            CommandHandler("report", start_report),
            MessageHandler(filters.TEXT & filters.Regex(r"^📝 稼働データ入力$"), start_report),
        ],
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
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(menu_callback, pattern="^menu_"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(report_conv)

    # 代行登録 ConversationHandler
    register_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_register, pattern="^menu_register$"),
            CommandHandler("register", start_register),
        ],
        states={
            REG_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_info)],
            REG_ID_PHOTO: [MessageHandler(filters.PHOTO, reg_id_photo)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(menu_callback, pattern="^menu_"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(register_conv)

    # 仮想通貨統合 ConversationHandler
    crypto_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_crypto, pattern="^menu_crypto$"),
            CommandHandler("crypto", start_crypto),
        ],
        states={
            CRYPTO_AMOUNT: [
                CallbackQueryHandler(crypto_action_callback, pattern="^crypto_action_"),
                CallbackQueryHandler(crypto_bank_select_callback, pattern="^crypto_bank_"),
                CallbackQueryHandler(crypto_confirm_callback, pattern="^crypto_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_amount_input),
            ],
            CRYPTO_CONFIRM: [
                CallbackQueryHandler(crypto_confirm_callback, pattern="^crypto_"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", lambda u, c: (u.message.reply_text("❌ 操作をキャンセルしました。"), ConversationHandler.END)[1]),
            CallbackQueryHandler(menu_callback, pattern="^menu_"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(crypto_conv)

    # FAQ / 経費・持ち物コールバックハンドラ
    app.add_handler(CallbackQueryHandler(faq_callback, pattern="^faq_"))

    # bitFlyer ETH売却コールバックハンドラ（旧/sellコマンド用の後方互換）
    app.add_handler(CallbackQueryHandler(crypto_sell_callback, pattern="^sell_"))

    # メニューコールバック（ConversationHandlerにマッチしない場合のフォールバック）
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))

    # LLM自動応答ハンドラ（ConversationHandlerにマッチしないテキストメッセージ用）
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, llm_reply))

    # ダミーサーバーをバックグラウンドで起動
    from server_dummy import start_server_in_background
    start_server_in_background()

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
