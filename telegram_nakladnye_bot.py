"""
Telegram-бот для приёма фото накладных из магазинов.

Запуск:
    export TELEGRAM_BOT_TOKEN="your-bot-token"
    export CRM_API_URL="http://localhost:20008"   # или прод URL
    export OPENAI_API_KEY="sk-..."                  # для OCR через GPT-4o-mini
    python telegram_nakladnye_bot.py

Зависимости:
    pip install python-telegram-bot openai
"""
import os
import json
import asyncio
import logging
from io import BytesIO
from datetime import datetime

# Загрузка .env если есть (локальная разработка)
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, CallbackQueryHandler,
        MessageHandler, filters, ContextTypes, ConversationHandler,
    )
except ImportError:
    print("Установите: pip install python-telegram-bot")
    exit(1)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("nakladnye_bot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CRM_API_URL = os.environ.get("CRM_API_URL", "http://localhost:20008")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
OCR_MODEL = os.environ.get("OCR_MODEL", "qwen/qwen2.5-vl-72b-instruct")

STORES = {
    "matushevicha": {"label": "Матусевича, 72", "value": "Матусевича"},
    "bogdanovicha": {"label": "Богдановича, 142", "value": "Богдановича"},
    "dombrovskaya": {"label": "Домбровская, 15", "value": "Домбровская"},
}

CHOOSING_STORE, RECEIVING_PHOTOS = range(2)


def get_store_keyboard():
    buttons = [
        [InlineKeyboardButton(s["label"], callback_data=f"store:{key}")]
        for key, s in STORES.items()
    ]
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


def get_done_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Готово, сохранить", callback_data="done")],
        [InlineKeyboardButton("📸 Ещё фото", callback_data="more")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📦 *Бот приёма накладных*\n\n"
        "Выберите склад, куда пришёл товар:",
        reply_markup=get_store_keyboard(),
        parse_mode="Markdown",
    )
    return CHOOSING_STORE


async def choose_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Отменено. /start — начать заново.")
        return ConversationHandler.END

    store_key = query.data.replace("store:", "")
    store = STORES.get(store_key)
    if not store:
        await query.edit_message_text("Неизвестный склад. /start — начать заново.")
        return ConversationHandler.END

    context.user_data["store"] = store["value"]
    context.user_data["photos"] = []
    context.user_data["store_key"] = store_key

    await query.edit_message_text(
        f"📍 Склад: *{store['label']}*\n\n"
        "Отправьте фото накладных (можно несколько).\n"
        "Когда все фото отправлены — нажмите «Готово».",
        reply_markup=get_done_keyboard(),
        parse_mode="Markdown",
    )
    return RECEIVING_PHOTOS


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # наибольшее разрешение
    file = await photo.get_file()
    buf = BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    context.user_data["photos"].append({
        "data": buf.getvalue(),
        "filename": f"photo_{len(context.user_data['photos']) + 1}.jpg",
    })

    count = len(context.user_data["photos"])
    await update.message.reply_text(
        f"📷 Фото #{count} получено. Ещё или «Готово».",
        reply_markup=get_done_keyboard(),
    )
    return RECEIVING_PHOTOS


async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Отменено. /start — начать заново.")
        return ConversationHandler.END

    if query.data == "more":
        await query.edit_message_text("Отправьте ещё фото.")
        return RECEIVING_PHOTOS

    if query.data == "done":
        photos = context.user_data.get("photos", [])
        store = context.user_data.get("store", "")

        if not photos:
            await query.edit_message_text("Нет фото. Отправьте хотя бы одно. /start")
            return ConversationHandler.END

        await query.edit_message_text("⏳ Обрабатываю накладные...")

        results = []
        for photo in photos:
            ocr = await ocr_photo(photo["data"])
            if ocr:
                results.append(ocr)

        import httpx
        async with httpx.AsyncClient() as client:
            for i, ocr in enumerate(results):
                payload = {
                    "supplier_name": ocr.get("supplier_name", ""),
                    "doc_type": ocr.get("doc_type", ""),
                    "doc_series": ocr.get("doc_series", ""),
                    "doc_number": ocr.get("doc_number", ""),
                    "doc_date": ocr.get("doc_date", ""),
                    "amount": ocr.get("amount"),
                    "unload_address": ocr.get("unload_address", ""),
                    "store": store,
                    "status": "new",
                }

                dup_check = await client.get(
                    f"{CRM_API_URL}/nakladnye/bot/check-duplicate",
                    headers={"X-Bot-Token": BOT_TOKEN},
                    params={
                        "doc_type": ocr.get("doc_type", ""),
                        "doc_number": ocr.get("doc_number", ""),
                    },
                )
                if dup_check.status_code == 200 and dup_check.json().get("duplicate"):
                    nak_id = dup_check.json().get("id")
                    await client.post(
                        f"{CRM_API_URL}/nakladnye/bot/{nak_id}/photos",
                        headers={"X-Bot-Token": BOT_TOKEN},
                        files={"file": (photo["filename"], photo["data"], "image/jpeg")},
                    )
                    continue

                resp = await client.post(
                    f"{CRM_API_URL}/nakladnye/bot/create",
                    headers={"X-Bot-Token": BOT_TOKEN},
                    json=payload,
                )
                if resp.status_code == 200:
                    nak = resp.json()
                    nak_id = nak["id"]
                    await client.post(
                        f"{CRM_API_URL}/nakladnye/bot/{nak_id}/photos",
                        headers={"X-Bot-Token": BOT_TOKEN},
                        files={"file": (photo["filename"], photo["data"], "image/jpeg")},
                    )
                else:
                    logger.error(f"CRM error: {resp.status_code} {resp.text}")

            if not results:
                resp = await client.post(
                    f"{CRM_API_URL}/nakladnye/bot/create",
                    headers={"X-Bot-Token": BOT_TOKEN},
                    json={
                        "supplier_name": "Не распознано",
                        "store": store,
                        "status": "new",
                    },
                )
                if resp.status_code == 200:
                    nak = resp.json()
                    for photo in photos:
                        await client.post(
                            f"{CRM_API_URL}/nakladnye/bot/{nak['id']}/photos",
                            headers={"X-Bot-Token": BOT_TOKEN},
                            files={"file": (photo["filename"], photo["data"], "image/jpeg")},
                        )

        summary_lines = []
        for r in results:
            name = r.get("supplier_name", "?")
            num = r.get("doc_number", "?")
            amt = r.get("amount", "—")
            summary_lines.append(f"• {name} №{num} — {amt} BYN")

        if not results:
            summary_lines = ["• Фото сохранены, данные не распознаны — проверьте вручную"]

        await query.edit_message_text(
            f"✅ *Сохранено в CRM*\n\n"
            f"Склад: {store}\n"
            f"Накладных: {max(len(results), 1)}\n\n"
            + "\n".join(summary_lines)
            + "\n\n/start — принять ещё",
            parse_mode="Markdown",
        )
        return ConversationHandler.END


async def ocr_photo(image_bytes: bytes):
    if not OPENAI_API_KEY or OpenAI is None:
        return None

    import base64
    b64 = base64.b64encode(image_bytes).decode()

    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты OCR-ассистент для распознавания белорусских накладных. "
                        "Извлеки из фото накладной следующие поля и верни ТОЛЬКО JSON:\n"
                        '{"supplier_name": "название поставщика", '
                        '"doc_type": "ТН или ТТН или УПД", '
                        '"doc_series": "серия (если есть)", '
                        '"doc_number": "номер накладной", '
                        '"doc_date": "YYYY-MM-DD", '
                        '"amount": число_с_НДС, '
                        '"unload_address": "адрес разгрузки (для ТТН)"}\n'
                        "Если поле не распознано — null. Без пояснений, только JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Распознай накладную на этом фото."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                },
            ],
            max_tokens=500,
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return None


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. /start — начать заново.")
    return ConversationHandler.END


def main():
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN не задан!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CHOOSING_STORE: [CallbackQueryHandler(choose_store)],
            RECEIVING_PHOTOS: [
                MessageHandler(filters.PHOTO, receive_photo),
                CallbackQueryHandler(handle_done),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(conv)

    logger.info("Бот запущен. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
