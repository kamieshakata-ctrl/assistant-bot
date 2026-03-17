# 稼働報告機能（Googleフォームに合わせて修正）

# ── 5. 稼働報告 ───────────────────────────────────────────────────────────
async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["report"] = {}
    await query.message.reply_text(
        "📝 *稼働報告*\n\n① 稼働者名を入力してください。", parse_mode="Markdown",
    )
    return REPORT_NAME


async def report_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["report"]["name"] = update.message.text.strip()
    # 稼働店舗数を選択
    keyboard = [
        [InlineKeyboardButton("1店舗", callback_data="shop_count_1")],
        [InlineKeyboardButton("2店舗", callback_data="shop_count_2")],
        [InlineKeyboardButton("3店舗", callback_data="shop_count_3")],
        [InlineKeyboardButton("4店舗", callback_data="shop_count_4")],
    ]
    await update.message.reply_text(
        "② *稼働店舗数* を選択してください。",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )
    return REPORT_SHOP


async def report_shop_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # 稼働店舗数を保存
    shop_count = query.data.replace("shop_count_", "")
    context.user_data["report"]["shop_count"] = f"{shop_count}店舗"
    # 日付選択
    today = datetime.now(JST).strftime("%Y/%m/%d")
    keyboard = [
        [InlineKeyboardButton(f"📅 今日（{today}）", callback_data="date_today")],
        [InlineKeyboardButton("✏️ 日付を入力する", callback_data="date_manual")],
    ]
    await query.message.reply_text(
        f"稼働店舗数: *{context.user_data['report']['shop_count']}*\n\n③ *稼働日* を選択してください。",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )
    return REPORT_DATE


async def report_shop_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # この関数は使われなくなるが、互換性のため残す
    context.user_data["report"]["shop_count"] = update.message.text.strip()
    today = datetime.now(JST).strftime("%Y/%m/%d")
    keyboard = [
        [InlineKeyboardButton(f"📅 今日（{today}）", callback_data="date_today")],
        [InlineKeyboardButton("✏️ 日付を入力する", callback_data="date_manual")],
    ]
    await update.message.reply_text(
        "③ *稼働日* を選択してください。",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )
    return REPORT_DATE


async def report_units(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # この関数は使われなくなるが、互換性のため残す
    context.user_data["report"]["shop_count"] = update.message.text.strip()
    today = datetime.now(JST).strftime("%Y/%m/%d")
    keyboard = [
        [InlineKeyboardButton(f"📅 今日（{today}）", callback_data="date_today")],
        [InlineKeyboardButton("✏️ 日付を入力する", callback_data="date_manual")],
    ]
    await update.message.reply_text(
        "③ *稼働日* を選択してください。",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )
    return REPORT_DATE


async def report_date_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "date_today":
        context.user_data["report"]["date"] = datetime.now(JST).strftime("%Y/%m/%d")
        # 確認画面を表示
        d = context.user_data["report"]
        text = (
            "📝 *稼働報告 — 確認*\n\n"
            f"👤 稼働者名: {d['name']}\n"
            f"🏪 稼働店舗数: {d['shop_count']}\n"
            f"📅 稼働日: {d['date']}\n\n"
            "上記の内容で送信しますか?"
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ 送信", callback_data="report_submit"),
                InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel"),
            ]
        ]
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return REPORT_CONFIRM
    else:
        await query.message.reply_text("日付を入力してください（例: 2026/02/18）。")
        return REPORT_DATE_INPUT


async def report_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["report"]["date"] = update.message.text.strip()
    # 確認画面を表示
    d = context.user_data["report"]
    text = (
        "📝 *稼働報告 — 確認*\n\n"
        f"👤 稼働者名: {d['name']}\n"
        f"🏪 稼働店舗数: {d['shop_count']}\n"
        f"📅 稼働日: {d['date']}\n\n"
        "上記の内容で送信しますか?"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ 送信", callback_data="report_submit"),
            InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel"),
        ]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REPORT_CONFIRM


async def report_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # この関数は使われなくなるが、互換性のため残す
    d = context.user_data["report"]
    text = (
        "📝 *稼働報告 — 確認*\n\n"
        f"👤 稼働者名: {d['name']}\n"
        f"🏪 稼働店舗数: {d['shop_count']}\n"
        f"📅 稼働日: {d['date']}\n\n"
        "上記の内容で送信しますか?"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ 送信", callback_data="report_submit"),
            InlineKeyboardButton("❌ キャンセル", callback_data="report_cancel"),
        ]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REPORT_CONFIRM


async def report_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data.get("report", {})
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
    headers = ["タイムスタンプ", "稼働者名", "稼働店舗数", "稼働日"]
    row = [now, d["name"], d["shop_count"], d["date"]]
    try:
        append_to_sheet("稼働報告", headers, row)
        keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
        await query.message.reply_text(
            "✅ *稼働報告を送信しました！*\n\nスプレッドシートに記録されました。",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Error writing to spreadsheet: {e}")
        keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
        await query.message.reply_text(
            "⚠️ 送信に失敗しました。しばらくしてから再度お試しください。",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    context.user_data.pop("report", None)
    return ConversationHandler.END


async def report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("report", None)
    keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
    await query.message.reply_text("❌ 稼働報告をキャンセルしました。", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END
