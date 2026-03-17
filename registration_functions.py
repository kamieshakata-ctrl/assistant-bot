# 代行登録フォーム機能

# ── 6. 代行登録フォーム ───────────────────────────────────────────────────────────
async def registration_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["registration"] = {}
    await query.message.reply_text(
        "📋 *代行登録フォーム*\n\n① お名前（漢字フルネーム）を入力してください。", parse_mode="Markdown",
    )
    return REGISTRATION_NAME


async def registration_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["registration"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"お名前: *{context.user_data['registration']['name']}*\n\n② *ご住所* を入力してください。",
        parse_mode="Markdown",
    )
    return REGISTRATION_ADDRESS


async def registration_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["registration"]["address"] = update.message.text.strip()
    await update.message.reply_text(
        f"ご住所: *{context.user_data['registration']['address']}*\n\n③ *身分証セルフィー* を送信してください。\n\n📸 身分証と顔が一緒に写った写真を送ってください。",
        parse_mode="Markdown",
    )
    return REGISTRATION_PHOTO


async def registration_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❌ 写真を送信してください。")
        return REGISTRATION_PHOTO
    
    # 最大サイズの写真を取得
    photo = update.message.photo[-1]
    context.user_data["registration"]["photo_file_id"] = photo.file_id
    
    # 確認画面を表示
    d = context.user_data["registration"]
    text = (
        "📋 *代行登録フォーム — 確認*\n\n"
        f"👤 お名前: {d['name']}\n"
        f"🏠 ご住所: {d['address']}\n"
        f"📸 身分証セルフィー: 受信済み\n\n"
        "上記の内容で送信しますか?"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ 送信", callback_data="registration_submit"),
            InlineKeyboardButton("❌ キャンセル", callback_data="registration_cancel"),
        ]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return REGISTRATION_CONFIRM


async def registration_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data.get("registration", {})
    now = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")
    
    # 写真をダウンロードしてGoogle Driveに保存
    photo_file_id = d.get("photo_file_id", "")
    photo_gdrive_path = ""
    
    try:
        # Telegram APIから写真をダウンロード
        file = await context.bot.get_file(photo_file_id)
        photo_bytes = await file.download_as_bytearray()
        
        # 一時ファイルに保存
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
            tmp_file.write(photo_bytes)
            tmp_path = tmp_file.name
        
        # Google Driveにアップロード
        timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        safe_name = d["name"].replace(" ", "_").replace("　", "_")
        gdrive_filename = f"身分証_{safe_name}_{timestamp}.jpg"
        
        # rcloneでアップロード
        result = subprocess.run(
            [
                "rclone", "copyto",
                tmp_path,
                f"manus_google_drive:{gdrive_filename}",
                "--config", RCLONE_CONFIG
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # 一時ファイルを削除
        os.unlink(tmp_path)
        
        if result.returncode == 0:
            photo_gdrive_path = gdrive_filename
            logger.info(f"Photo uploaded to Google Drive: {gdrive_filename}")
        else:
            logger.error(f"Failed to upload photo: {result.stderr}")
            photo_gdrive_path = f"[アップロード失敗] file_id: {photo_file_id}"
    
    except Exception as e:
        logger.error(f"Error uploading photo to Google Drive: {e}")
        photo_gdrive_path = f"[エラー] file_id: {photo_file_id}"
    
    # スプレッドシートに記録
    headers = ["タイムスタンプ", "お名前", "ご住所", "身分証写真（Google Drive）", "Telegram file_id"]
    row = [now, d["name"], d["address"], photo_gdrive_path, photo_file_id]
    
    try:
        append_to_sheet("代行登録フォーム", headers, row)
        keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
        await query.message.reply_text(
            "✅ *代行登録フォームを送信しました！*\n\nスプレッドシートに記録されました。",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Error writing to spreadsheet: {e}")
        keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
        await query.message.reply_text(
            "⚠️ 送信に失敗しました。しばらくしてから再度お試しください。",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    context.user_data.pop("registration", None)
    return ConversationHandler.END


async def registration_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("registration", None)
    keyboard = [[InlineKeyboardButton("🔙 メニューに戻る", callback_data="back_to_menu")]]
    await query.message.reply_text("❌ 代行登録フォームをキャンセルしました。", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END
