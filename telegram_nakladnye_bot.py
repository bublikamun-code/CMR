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

        await query.edit_message_text(f"⏳ Обрабатываю {len(photos)} фото...")

        # Batch OCR: все фото одним запросом для точного группирования
        all_ocr = await ocr_photos_batch([p["data"] for p in photos])
        if not all_ocr:
            # Fallback: поштучный OCR
            all_ocr = []
            for photo in photos:
                ocr = await ocr_photo(photo["data"])
                if ocr:
                    all_ocr.append(ocr)

        # Привязываем фото к результатам OCR
        for i, ocr in enumerate(all_ocr):
            ocr["_photo_indices"] = ocr.get("photo_indices", [i] if i < len(photos) else [])

        # Группируем по номеру накладной
        invoices = {}
        warnings = []
        for ocr in all_ocr:
            num = ocr.get("doc_number") or "unknown"
            if num not in invoices:
                invoices[num] = []
            invoices[num].append(ocr)

        # Для каждой группы: данные с первой страницы, суммы с последней
        merged = []
        for num, pages in invoices.items():
            first = pages[0]
            last = pages[-1]
            page_num = first.get("page_number")
            total_pages = first.get("total_pages")

            # Собираем все фото для этой накладной
            inv_photos = []
            for p in pages:
                for idx in p.get("_photo_indices", []):
                    if idx < len(photos):
                        inv_photos.append(photos[idx])

            if not inv_photos and len(photos) > 0:
                inv_photos = [photos[0]]

            if page_num and total_pages and len(pages) < total_pages:
                warnings.append(
                    f"⚠️ {first.get('doc_type','')} №{num}: "
                    f"ожидалось {total_pages} стр., получено {len(pages)}"
                )
            merged.append({
                "supplier_name": first.get("supplier_name", ""),
                "doc_type": first.get("doc_type", ""),
                "doc_series": first.get("doc_series", ""),
                "doc_number": first.get("doc_number", ""),
                "doc_date": first.get("doc_date", ""),
                "amount": last.get("amount"),
                "vat_amount": last.get("vat_amount"),
                "unload_address": first.get("unload_address", ""),
                "photos": inv_photos,
            })

        import httpx
        async with httpx.AsyncClient() as client:
            for inv in merged:
                payload = {
                    "supplier_name": inv["supplier_name"],
                    "doc_type": inv["doc_type"],
                    "doc_series": inv["doc_series"],
                    "doc_number": inv["doc_number"],
                    "doc_date": inv["doc_date"],
                    "amount": inv["amount"],
                    "vat_amount": inv["vat_amount"],
                    "unload_address": inv["unload_address"],
                    "store": store,
                    "status": "new",
                }

                dup_check = await client.get(
                    f"{CRM_API_URL}/nakladnye/bot/check-duplicate",
                    headers={"X-Bot-Token": BOT_TOKEN},
                    params={
                        "doc_type": inv["doc_type"],
                        "doc_number": inv["doc_number"],
                    },
                )
                if dup_check.status_code == 200 and dup_check.json().get("duplicate"):
                    nak_id = dup_check.json().get("id")
                    for photo in inv["photos"]:
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
                    for photo in inv["photos"]:
                        await client.post(
                            f"{CRM_API_URL}/nakladnye/bot/{nak_id}/photos",
                            headers={"X-Bot-Token": BOT_TOKEN},
                            files={"file": (photo["filename"], photo["data"], "image/jpeg")},
                        )
                else:
                    logger.error(f"CRM error: {resp.status_code} {resp.text}")

            if not merged:
                resp = await client.post(
                    f"{CRM_API_URL}/nakladnye/bot/create",
                    headers={"X-Bot-Token": BOT_TOKEN},
                    json={"supplier_name": "Не распознано", "store": store, "status": "new"},
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
        for inv in merged:
            name = inv["supplier_name"] or "?"
            num = inv["doc_number"] or "?"
            amt = inv["amount"] or "—"
            dtype = inv["doc_type"] or ""
            pages_count = len(inv["photos"])
            pages_str = f" ({pages_count} стр.)" if pages_count > 1 else ""
            summary_lines.append(f"• {dtype} {name} №{num} — {amt} BYN{pages_str}")

        if not merged:
            summary_lines = ["• Фото сохранены, данные не распознаны — проверьте вручную"]

        warn_text = ("\n\n" + "\n".join(warnings)) if warnings else ""

        await query.edit_message_text(
            f"✅ *Сохранено в CRM*\n\n"
            f"Склад: {store}\n"
            f"Накладных: {max(len(merged), 1)}\n\n"
            + "\n".join(summary_lines)
            + warn_text
            + "\n\n/start — принять ещё",
            parse_mode="Markdown",
        )
        return ConversationHandler.END


OCR_PROMPT = (
    "Ты OCR-ассистент для белорусских товарных накладных.\n"
    "На фото может быть несколько накладных (по 1-2 фото на каждую).\n\n"
    "ТИПЫ ДОКУМЕНТОВ:\n"
    "• ТН = ТОВАРНАЯ НАКЛАДНАЯ (заголовок содержит «ТОВАРНАЯ НАКЛАДНАЯ»)\n"
    "• ТТН = ТОВАРНО-ТРАНСПОРТНАЯ НАКЛАДНАЯ (заголовок содержит «ТТН» или «ТРАНСПОРТНАЯ»)\n"
    "• УПД = УНИВЕРСАЛЬНЫЙ ПЕРЕДАТОЧНЫЙ ДОКУМЕНТ\n"
    "ВНИМАНИЕ: ТН и ТТН — это РАЗНЫЕ типы! Не путай.\n\n"
    "ПРАВИЛА:\n"
    "• supplier_name — ГРУЗООТПРАВИТЕЛЬ (компания). «Свет в доме» = грузополучатель, НЕ указывай.\n"
    "  НЕ указывай должности и ФИО людей (комплектовщик, директор, водитель и т.д.)!\n"
    "• Серия — 2 буквы СЛЕВА (ЯЖ, АВ, МК, АС и т.д.)\n"
    "• Номер — под штрихкодом или внизу, только цифры.\n"
    "• unload_address — ТОЛЬКО для ТТН! Для ТН всегда null.\n\n"
    "СУММЫ:\n"
    "• amount = «Стоимость с НДС» / «Всего с НДС» — ОБЩАЯ СУММА (всегда > НДС)\n"
    "• vat_amount = «Сумма НДС» / «в т.ч. НДС» — только налог (всегда < amount)\n"
    "• Числа с десятичной точкой: 653.09, 108.84. Запятая = точка!\n"
    "• amount > vat_amount — всегда.\n\n"
    "Верни МАССИВ JSON-объектов (по одному на каждую ОТДЕЛЬНУЮ накладную):\n"
    "[{\n"
    '  "supplier_name": "грузоотправитель (ИП, ООО, ОДО, ЧТУП)",\n'
    '  "doc_type": "ТН" | "ТТН" | "УПД",\n'
    '  "doc_series": "буквы серии",\n'
    '  "doc_number": "цифры номера",\n'
    '  "doc_date": "YYYY-MM-DD",\n'
    '  "amount": число_с_НДС,\n'
    '  "vat_amount": число_НДС,\n'
    '  "unload_address": "адрес (ТОЛЬКО для ТТН, иначе null)",\n'
    '  "photo_indices": [0] или [0, 1] — индексы фото (с 0), относящихся к этой накладной\n'
    "}]\n"
    "Если 2 фото = одна накладная (страница 1 и 2) — один объект с photo_indices: [0, 1].\n"
    "Если не распознано — null. ТОЛЬКО JSON массив, без markdown."
)


async def ocr_photos_batch(images_bytes: list):
    """OCR всех фото одним запросом — модель видит всё сразу."""
    if not OPENAI_API_KEY or OpenAI is None:
        return None

    import base64
    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    content = [{"type": "text", "text": f"Распознай все накладные на этих {len(images_bytes)} фото."}]
    for i, img_bytes in enumerate(images_bytes):
        b64 = base64.b64encode(img_bytes).decode()
        content.append({"type": "text", "text": f"--- Фото {i} ---"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    try:
        response = client.chat.completions.create(
            model=OCR_MODEL,
            messages=[
                {"role": "system", "content": OCR_PROMPT},
                {"role": "user", "content": content},
            ],
            max_tokens=1500,
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)

        if not isinstance(data, list):
            data = [data]

        # Валидация каждой записи
        for item in data:
            amt = item.get("amount")
            vat = item.get("vat_amount")

            def fix_decimal(v):
                if v is None:
                    return v
                v = float(v)
                if v == int(v) and v >= 10000:
                    v = v / 100
                return round(v, 2)

            if amt is not None:
                item["amount"] = fix_decimal(amt)
            if vat is not None:
                item["vat_amount"] = fix_decimal(vat)

            if (item.get("amount") and item.get("vat_amount")
                    and item["amount"] < item["vat_amount"]):
                item["amount"], item["vat_amount"] = item["vat_amount"], item["amount"]

            # unload_address только для ТТН
            if item.get("doc_type") != "ТТН":
                item["unload_address"] = None

        return data
    except Exception as e:
        logger.error(f"Batch OCR error: {e}")
        return None


async def ocr_photo(image_bytes: bytes):
    if not OPENAI_API_KEY or OpenAI is None:
        return None

    import base64
    b64 = base64.b64encode(image_bytes).decode()

    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    try:
        response = client.chat.completions.create(
            model=OCR_MODEL,
            messages=[
                {"role": "system", "content": OCR_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Распознай накладную на этом фото. Верни массив из одного элемента."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                },
            ],
            max_tokens=1500,
            temperature=0,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)

        # Если пришёл массив — берём первый элемент
        if isinstance(data, list):
            data = data[0] if data else {}

        def fix_decimal(v):
            if v is None:
                return v
            v = float(v)
            if v == int(v) and v >= 10000:
                v = v / 100
            return round(v, 2)

        if data.get("amount") is not None:
            data["amount"] = fix_decimal(data["amount"])
        if data.get("vat_amount") is not None:
            data["vat_amount"] = fix_decimal(data["vat_amount"])

        # amount всегда >= vat_amount
        if (data.get("amount") and data.get("vat_amount")
                and data["amount"] < data["vat_amount"]):
            data["amount"], data["vat_amount"] = data["vat_amount"], data["amount"]

        # unload_address только для ТТН
        if data.get("doc_type") != "ТТН":
            data["unload_address"] = None

        # Ratio check
        amt = data.get("amount")
        vat = data.get("vat_amount")
        if amt and vat and vat > 0:
            ratio = amt / vat
            if ratio < 4 or ratio > 8:
                logger.warning(f"OCR suspicious ratio: amount={amt}, vat={vat}, ratio={ratio:.1f}")

        return data
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
