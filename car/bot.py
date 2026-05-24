"""
Telegram-бот поиска авто по РФ — Auto.ru, Avito, Drom.ru, Am.ru
Поддерживает разовый поиск и мониторинг 24/7 с уведомлениями.
"""
import asyncio
import logging
import os
import re

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

import monitor as mon
from parsers import AutoRuParser, AvitoParser, DromParser

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Состояния ConversationHandler ───────────────────────────────────
(
    ASK_BRAND,
    ASK_BRAND_MANUAL,
    ASK_DETAILS,
    ASK_REGION,
    ASK_REGION_MANUAL,
    ASK_AFTER_SEARCH,       # что делать после поиска: мониторить?
) = range(6)

# ── Кнопки ──────────────────────────────────────────────────────────
BTN_MANUAL      = "✍️ Ввести вручную"
BTN_CANCEL      = "❌ Отмена"
BTN_BACK        = "◀️ Назад"
BTN_SKIP        = "⏭ Пропустить"
BTN_RUSSIA      = "🌍 Вся Россия"
BTN_NEW         = "new_search"
BTN_ADD_MONITOR = "🔔 Включить мониторинг"
BTN_SKIP_MON    = "🚫 Не надо"

POPULAR_BRANDS = [
    ["Toyota",     "Kia",        "Hyundai"],
    ["Lada (ВАЗ)", "Volkswagen", "Skoda"],
    ["BMW",        "Mercedes",   "Audi"],
    ["Haval",      "Geely",      "Chery"],
    ["Nissan",     "Mazda",      "Ford"],
    [BTN_MANUAL],
]

REGIONS = [
    ["Москва",          "Санкт-Петербург"],
    ["Екатеринбург",    "Краснодар"],
    ["Новосибирск",     "Казань"],
    ["Нижний Новгород", "Самара"],
    ["Ростов-на-Дону",  "Челябинск"],
    ["Уфа",             "Воронеж"],
    [BTN_RUSSIA,        BTN_MANUAL],
    [BTN_BACK,          BTN_CANCEL],
]

PARSERS = [AutoRuParser(), AvitoParser(), DromParser()]


# ── Клавиатуры ──────────────────────────────────────────────────────

def brand_kb():
    return ReplyKeyboardMarkup(POPULAR_BRANDS, resize_keyboard=True, one_time_keyboard=True)

def region_kb():
    return ReplyKeyboardMarkup(REGIONS, resize_keyboard=True, one_time_keyboard=True)

def back_cancel_kb():
    return ReplyKeyboardMarkup([[BTN_BACK, BTN_CANCEL]], resize_keyboard=True)

def details_kb():
    return ReplyKeyboardMarkup([[BTN_SKIP], [BTN_BACK, BTN_CANCEL]], resize_keyboard=True)

def after_search_kb():
    return ReplyKeyboardMarkup(
        [[BTN_ADD_MONITOR], [BTN_SKIP_MON]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ── Парсинг диапазона ────────────────────────────────────────────────

def parse_range(text: str):
    text = text.strip().replace(" ", "").replace("\xa0", "")
    if not text or text in ("-", "—", "–", ".", "*"):
        return None, None
    m = re.match(r"^(\d+)[–\-—](\d+)$", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (a, b) if a <= b else (b, a)
    m2 = re.match(r"^[–\-—](\d+)$", text)
    if m2:
        return None, int(m2.group(1))
    m3 = re.match(r"^(\d+)[–\-—]$", text)
    if m3:
        return int(m3.group(1)), None
    if text.isdigit():
        return None, int(text)
    return None, None


def parse_details(raw: str) -> dict:
    lines = [l.strip() for l in raw.strip().splitlines()]
    while len(lines) < 4:
        lines.append("")
    p: dict = {}

    model = lines[0]
    if model and model not in ("-", "—", "–", ".", "*"):
        p["model"] = model

    yf, yt = parse_range(lines[1])
    if yf: p["year_from"] = max(1970, min(2026, yf))
    if yt: p["year_to"]   = max(1970, min(2026, yt))

    mf, mt = parse_range(lines[2])
    if mf is not None: p["mileage_from"] = mf
    if mt is not None: p["mileage_to"]   = mt

    pf, pt = parse_range(lines[3])
    if pf is not None: p["price_from"] = pf
    if pt is not None: p["price_to"]   = pt

    return p


# ── Форматирование ───────────────────────────────────────────────────

def fmt(v) -> str:
    return f"{int(v):,}".replace(",", " ")

def params_summary(p: dict) -> str:
    lines = []
    if p.get("brand"):  lines.append(f"🚘 Марка: <b>{p['brand']}</b>")
    if p.get("model"):  lines.append(f"🏷 Модель: <b>{p['model']}</b>")
    yr = []
    if p.get("year_from"): yr.append(f"от {p['year_from']}")
    if p.get("year_to"):   yr.append(f"до {p['year_to']}")
    if yr: lines.append(f"📅 Год: <b>{' '.join(yr)}</b>")
    km = []
    if p.get("mileage_from") is not None: km.append(f"от {fmt(p['mileage_from'])} км")
    if p.get("mileage_to")   is not None: km.append(f"до {fmt(p['mileage_to'])} км")
    if km: lines.append(f"🛣 Пробег: <b>{' '.join(km)}</b>")
    pr = []
    if p.get("price_from") is not None: pr.append(f"от {fmt(p['price_from'])} ₽")
    if p.get("price_to")   is not None: pr.append(f"до {fmt(p['price_to'])} ₽")
    if pr: lines.append(f"💰 Цена: <b>{' '.join(pr)}</b>")
    if p.get("region"): lines.append(f"📍 Регион: <b>{p['region']}</b>")
    return "\n".join(lines) if lines else "Параметры не заданы"


EXAMPLE_TEXT = (
    "📝 Введите параметры <b>одним сообщением</b> — каждый с новой строки:\n\n"
    "┌─────────────────────────\n"
    "│ 1️⃣  <b>Модель</b>\n"
    "│ 2️⃣  <b>Год</b>  (от–до)\n"
    "│ 3️⃣  <b>Пробег</b>  (от–до, км)\n"
    "│ 4️⃣  <b>Цена</b>  (от–до, ₽)\n"
    "└─────────────────────────\n\n"
    "💡 <b>Примеры:</b>\n\n"
    "<code>Camry\n2016-2021\n40000-130000\n900000-2200000</code>\n\n"
    "одно число = «до» (максимум):\n\n"
    "<code>-\n2020\n80000\n1500000</code>\n\n"
    "⚡ <b>Правила:</b>\n"
    "  • <code>число-число</code> — диапазон от/до\n"
    "  • одно число — «до» (максимум)\n"
    "  • <code>-</code> или пустая строка — любое значение\n\n"
    "Нажмите <b>⏭ Пропустить</b> чтобы не указывать детали."
)


# ── Поиск ───────────────────────────────────────────────────────────

async def run_search(message, params: dict) -> int:
    """
    Параллельный поиск на всех площадках.
    Каждая площадка возвращает 7-8 объявлений в хорошем состоянии.
    """
    tasks = [p.search(params) for p in PARSERS]
    raw   = await asyncio.gather(*tasks, return_exceptions=True)

    total = 0
    for parser, results in zip(PARSERS, raw):
        if isinstance(results, Exception):
            logger.error("%s: %s", parser.SOURCE_NAME, results)
            await message.reply_text(
                f"{parser.ICON} <b>{parser.SOURCE_NAME}</b>: ошибка при запросе",
                parse_mode="HTML",
            )
            continue

        if not results:
            await message.reply_text(
                f"{parser.ICON} <b>{parser.SOURCE_NAME}</b>: ничего не найдено по вашим параметрам",
                parse_mode="HTML",
            )
            continue

        # Проверяем — это реальные объявления или просто ссылка-fallback?
        is_fallback = (len(results) == 1 and results[0]["title"].startswith("🔗"))

        if is_fallback:
            r = results[0]
            await message.reply_text(
                f"{parser.ICON} <b>{parser.SOURCE_NAME}</b> — заблокировал автоматический парсинг.\n"
                f'Нажмите чтобы открыть с вашими фильтрами: <a href="{r["url"]}">перейти →</a>',
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            continue

        total += len(results)
        lines  = [f"{parser.ICON} <b>{parser.SOURCE_NAME}</b> — {len(results)} объявлений:\n"]
        for r in results:
            title = (r.get("title") or "Объявление")[:80]
            price = r.get("price") or "Цена не указана"
            url   = r.get("url", "")
            lines.append(f'• <a href="{url}">{title}</a>\n  💰 {price}')

        await message.reply_text(
            "\n\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await asyncio.sleep(0.4)

    return total


# ── Обработчики диалога ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    ctx.user_data["params"] = {}

    webapp_url = os.getenv("WEBAPP_URL", "")

    text = (
        "👋 <b>Привет!</b> Ищу авто на Auto.ru, Avito, Drom.ru.\n\n"
        "Выберите марку или введите вручную:"
    )

    if webapp_url:
        # Показываем кнопку Mini App + обычный поиск
        kb_inline = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🚗 Открыть приложение",
                web_app=WebAppInfo(url=webapp_url)
            )
        ]])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_inline)
        await update.message.reply_text(
            "Или используйте поиск прямо здесь — выберите марку:",
            reply_markup=brand_kb(),
        )
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=brand_kb())

    return ASK_BRAND


async def hdl_brand(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == BTN_CANCEL: return await do_cancel(update, ctx)
    if text == BTN_MANUAL:
        await update.message.reply_text(
            "✍️ Напишите марку (например: <b>Mitsubishi</b>):",
            parse_mode="HTML", reply_markup=back_cancel_kb(),
        )
        return ASK_BRAND_MANUAL
    brand = "Lada" if text == "Lada (ВАЗ)" else text
    ctx.user_data["params"]["brand"] = brand
    return await _show_details_form(update, ctx, brand)


async def hdl_brand_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == BTN_CANCEL: return await do_cancel(update, ctx)
    if text == BTN_BACK:
        await update.message.reply_text("Выберите марку:", reply_markup=brand_kb())
        return ASK_BRAND
    ctx.user_data["params"]["brand"] = text
    return await _show_details_form(update, ctx, text)


async def _show_details_form(update: Update, ctx: ContextTypes.DEFAULT_TYPE, brand: str) -> int:
    await update.message.reply_text(
        f"✅ Марка: <b>{brand}</b>\n\n" + EXAMPLE_TEXT,
        parse_mode="HTML", reply_markup=details_kb(),
    )
    return ASK_DETAILS


async def hdl_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == BTN_CANCEL: return await do_cancel(update, ctx)
    if text == BTN_BACK:
        ctx.user_data["params"] = {}
        await update.message.reply_text("Выберите марку:", reply_markup=brand_kb())
        return ASK_BRAND
    if text != BTN_SKIP:
        ctx.user_data["params"].update(parse_details(text))
    await update.message.reply_text("📍 Выберите регион поиска:", reply_markup=region_kb())
    return ASK_REGION


async def hdl_region(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == BTN_CANCEL: return await do_cancel(update, ctx)
    if text == BTN_BACK:
        brand = ctx.user_data["params"].get("brand", "")
        await update.message.reply_text(
            f"✅ Марка: <b>{brand}</b>\n\n" + EXAMPLE_TEXT,
            parse_mode="HTML", reply_markup=details_kb(),
        )
        return ASK_DETAILS
    if text == BTN_MANUAL:
        await update.message.reply_text(
            "✍️ Напишите название города или региона\n"
            "(например: <b>Тюмень</b>, <b>Иркутск</b>, <b>Владивосток</b>):",
            parse_mode="HTML", reply_markup=back_cancel_kb(),
        )
        return ASK_REGION_MANUAL

    if text != BTN_RUSSIA:
        ctx.user_data["params"]["region"] = text

    return await _do_search_then_ask_monitor(update, ctx)


async def hdl_region_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == BTN_CANCEL: return await do_cancel(update, ctx)
    if text == BTN_BACK:
        await update.message.reply_text("📍 Выберите регион:", reply_markup=region_kb())
        return ASK_REGION
    ctx.user_data["params"]["region"] = text
    return await _do_search_then_ask_monitor(update, ctx)


async def _do_search_then_ask_monitor(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> int:
    """Выполняет поиск, затем предлагает включить мониторинг."""
    params  = ctx.user_data.get("params", {})
    summary = params_summary(params)

    await update.message.reply_text(
        f"🔍 <b>Начинаю поиск...</b>\n\n{summary}\n\nAuto.ru • Avito • Drom.ru • Am.ru ⏳",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )

    total = await run_search(update.message, params)

    if total == 0:
        note = "😔 По вашим параметрам ничего не найдено. Попробуйте расширить критерии."
    else:
        note = f"✅ <b>Поиск завершён!</b> Найдено объявлений: {total}"

    # Предлагаем мониторинг
    n_mon = mon.count_monitors(update.effective_user.id)
    if n_mon >= mon.MAX_MONITORS_PER_USER:
        # Лимит достигнут — не предлагаем
        new_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Новый поиск", callback_data=BTN_NEW)
        ]])
        await update.message.reply_text(
            note + f"\n\n⚠️ Лимит мониторов достигнут ({mon.MAX_MONITORS_PER_USER}). "
            "Удалите старый через /mymonitors.",
            parse_mode="HTML", reply_markup=new_kb,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        note + "\n\n🔔 <b>Хотите включить мониторинг?</b>\n"
        f"Бот будет проверять площадки каждые {mon.CHECK_INTERVAL_MINUTES} мин "
        "и присылать новые объявления автоматически.",
        parse_mode="HTML",
        reply_markup=after_search_kb(),
    )
    return ASK_AFTER_SEARCH


async def hdl_after_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text   = update.message.text.strip()
    new_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Новый поиск", callback_data=BTN_NEW)
    ]])

    if text == BTN_ADD_MONITOR:
        params = ctx.user_data.get("params", {})
        try:
            mid = mon.add_monitor(update.effective_user.id, params)
            summary = params_summary(params)
            await update.message.reply_text(
                f"✅ <b>Мониторинг включён!</b>\n\n"
                f"{summary}\n\n"
                f"🔔 Проверка каждые {mon.CHECK_INTERVAL_MINUTES} мин.\n"
                f"🆔 ID монитора: <code>{mid}</code>\n\n"
                f"Управление: /mymonitors",
                parse_mode="HTML",
                reply_markup=new_kb,
            )
        except ValueError as e:
            await update.message.reply_text(
                f"⚠️ {e}", parse_mode="HTML", reply_markup=new_kb,
            )
    else:
        await update.message.reply_text(
            "Хорошо! Нажмите кнопку ниже чтобы начать новый поиск.",
            reply_markup=new_kb,
        )

    return ConversationHandler.END


# ── Команды управления мониторами ────────────────────────────────────

async def cmd_mymonitors(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Список активных мониторов пользователя."""
    uid      = update.effective_user.id
    monitors = mon.list_monitors(uid)

    if not monitors:
        await update.message.reply_text(
            "У вас нет активных мониторов.\n"
            "Запустите /search и включите мониторинг после поиска.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    lines = [f"🔔 <b>Ваши мониторы ({len(monitors)}/{mon.MAX_MONITORS_PER_USER}):</b>\n"]
    buttons = []

    for i, m in enumerate(monitors, 1):
        label      = mon._params_label(m["params"])
        created    = m.get("created_at", "—")
        last_check = m.get("last_check", "ещё не проверялся")
        mid        = m["id"]
        lines.append(
            f"{i}. <b>{label}</b>\n"
            f"   📅 Создан: {created}\n"
            f"   🕐 Последняя проверка: {last_check}\n"
            f"   🆔 ID: <code>{mid}</code>"
        )
        buttons.append([
            InlineKeyboardButton(f"🗑 Удалить #{i} ({label[:30]})", callback_data=f"del_mon:{mid}")
        ])

    buttons.append([
        InlineKeyboardButton("🗑 Удалить все", callback_data="del_mon:all")
    ])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cbk_delete_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка кнопки удаления монитора."""
    query = update.callback_query
    await query.answer()
    uid  = update.effective_user.id
    data = query.data  # "del_mon:12345" или "del_mon:all"

    _, target = data.split(":", 1)

    if target == "all":
        n = mon.remove_all_monitors(uid)
        await query.edit_message_text(f"🗑 Удалено мониторов: {n}")
    else:
        ok = mon.remove_monitor(uid, int(target))
        if ok:
            await query.edit_message_text("✅ Монитор удалён.")
        else:
            await query.edit_message_text("⚠️ Монитор не найден.")


async def cbk_new_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data.clear()
    ctx.user_data["params"] = {}
    await update.callback_query.message.reply_text("Выберите марку:", reply_markup=brand_kb())
    return ASK_BRAND


async def do_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text(
        "❌ Поиск отменён. /search — начать заново.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 <b>Бот поиска авто по РФ</b>\n\n"
        "<b>Команды:</b>\n"
        "/search — новый поиск\n"
        "/mymonitors — мои мониторы (список, удаление)\n"
        "/help — эта справка\n\n"
        "<b>Площадки:</b> Auto.ru • Avito • Drom.ru • Am.ru\n\n"
        "<b>Мониторинг:</b>\n"
        f"После каждого поиска можно включить мониторинг — бот будет "
        f"проверять новые объявления каждые {mon.CHECK_INTERVAL_MINUTES} мин "
        f"и присылать уведомления.\n"
        f"Максимум {mon.MAX_MONITORS_PER_USER} активных мониторов.",
        parse_mode="HTML",
    )


# ── Запуск ──────────────────────────────────────────────────────────

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example → .env")

    app = Application.builder().token(token).build()

    # ── ConversationHandler ──
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",  cmd_start),
            CommandHandler("search", cmd_start),
            CallbackQueryHandler(cbk_new_search, pattern=f"^{BTN_NEW}$"),
        ],
        states={
            ASK_BRAND:         [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_brand)],
            ASK_BRAND_MANUAL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_brand_manual)],
            ASK_DETAILS:       [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_details)],
            ASK_REGION:        [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_region)],
            ASK_REGION_MANUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_region_manual)],
            ASK_AFTER_SEARCH:  [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_after_search)],
        },
        fallbacks=[
            CommandHandler("cancel", do_cancel),
            CommandHandler("start",  cmd_start),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("mymonitors", cmd_mymonitors))
    app.add_handler(CallbackQueryHandler(cbk_new_search,     pattern=f"^{BTN_NEW}$"))
    app.add_handler(CallbackQueryHandler(cbk_delete_monitor, pattern=r"^del_mon:"))

    # ── Фоновая задача мониторинга ──
    job_queue = app.job_queue
    job_queue.run_repeating(
        callback=mon.check_all_monitors,
        interval=mon.CHECK_INTERVAL_MINUTES * 60,
        first=60,   # первый запуск через 60 сек после старта
        name="monitor_check",
    )

    logger.info(
        "Бот запущен ✅ | Мониторинг каждые %d мин",
        mon.CHECK_INTERVAL_MINUTES,
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
