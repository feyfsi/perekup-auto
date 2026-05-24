"""
Менеджер мониторинга объявлений.

Хранит активные подписки в JSON-файле (monitors.json).
Каждые INTERVAL минут проверяет новые объявления по всем площадкам
и уведомляет пользователя если появилось что-то новое.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

STORAGE_FILE = Path("monitors.json")
CHECK_INTERVAL_MINUTES = 2           # как часто проверять (минут)
MAX_MONITORS_PER_USER  = 5           # максимум подписок на одного пользователя
MAX_SEEN_URLS          = 500         # сколько ссылок хранить в истории


def _load() -> dict:
    """Загружает данные из файла. Структура: {user_id: [monitor, ...]}"""
    if not STORAGE_FILE.exists():
        return {}
    try:
        return json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("monitor load error: %s", e)
        return {}


def _save(data: dict) -> None:
    try:
        STORAGE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error("monitor save error: %s", e)


def list_monitors(user_id: int) -> list[dict]:
    data = _load()
    return data.get(str(user_id), [])


def count_monitors(user_id: int) -> int:
    return len(list_monitors(user_id))


def add_monitor(user_id: int, params: dict) -> int:
    """
    Добавляет монитор. Возвращает ID монитора (порядковый номер).
    Поднимает ValueError если достигнут лимит.
    """
    data = _load()
    key  = str(user_id)
    if key not in data:
        data[key] = []

    if len(data[key]) >= MAX_MONITORS_PER_USER:
        raise ValueError(
            f"Достигнут лимит {MAX_MONITORS_PER_USER} мониторов. "
            "Удалите старый через /mymonitors."
        )

    monitor_id = int(time.time())   # уникальный ID = unix timestamp
    data[key].append({
        "id":         monitor_id,
        "params":     params,
        "created_at": time.strftime("%d.%m.%Y %H:%M"),
        "seen_urls":  [],           # уже отправленные ссылки
        "last_check": None,
    })
    _save(data)
    return monitor_id


def remove_monitor(user_id: int, monitor_id: int) -> bool:
    """Удаляет монитор. Возвращает True если удалён."""
    data = _load()
    key  = str(user_id)
    before = len(data.get(key, []))
    data[key] = [m for m in data.get(key, []) if m["id"] != monitor_id]
    if len(data[key]) < before:
        _save(data)
        return True
    return False


def remove_all_monitors(user_id: int) -> int:
    """Удаляет все мониторы пользователя. Возвращает кол-во удалённых."""
    data = _load()
    key  = str(user_id)
    n    = len(data.get(key, []))
    data[key] = []
    _save(data)
    return n


def _mark_seen(data: dict, key: str, monitor_id: int, urls: list[str]) -> None:
    for m in data.get(key, []):
        if m["id"] == monitor_id:
            seen = m["seen_urls"]
            seen.extend(u for u in urls if u not in seen)
            # Ограничиваем размер истории
            m["seen_urls"]  = seen[-MAX_SEEN_URLS:]
            m["last_check"] = time.strftime("%d.%m.%Y %H:%M")
            break
    _save(data)


def _params_label(params: dict) -> str:
    parts = []
    if params.get("brand"):  parts.append(params["brand"])
    if params.get("model"):  parts.append(params["model"])
    if params.get("year_from") or params.get("year_to"):
        yr = f"{params.get('year_from','')}–{params.get('year_to','')}".strip("–")
        parts.append(f"{yr} г.")
    if params.get("price_to"):
        parts.append(f"до {int(params['price_to']):,} ₽".replace(",", " "))
    if params.get("region"):
        parts.append(params["region"])
    return " • ".join(parts) if parts else "Все авто"


# ── Фоновая задача ──────────────────────────────────────────────────

async def check_all_monitors(context) -> None:
    """
    Запускается планировщиком каждые CHECK_INTERVAL_MINUTES минут.
    Проходит по всем пользователям и их мониторам, ищет новые объявления.
    """
    from parsers import AutoRuParser, AvitoParser, DromParser
    parsers = [AutoRuParser(), AvitoParser(), DromParser()]

    data = _load()
    if not data:
        return

    for user_id_str, monitors in data.items():
        if not monitors:
            continue
        user_id = int(user_id_str)

        for monitor in monitors:
            params     = monitor["params"]
            seen_urls  = set(monitor["seen_urls"])
            monitor_id = monitor["id"]
            label      = _params_label(params)

            try:
                import asyncio
                tasks = [p.search(params) for p in parsers]
                all_results = await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.error("monitor check error uid=%s: %s", user_id, e)
                continue

            # Собираем новые объявления
            raw_new = []
            new_urls = []
            for parser, results in zip(parsers, all_results):
                if isinstance(results, Exception) or not results:
                    continue
                for r in results:
                    url = r.get("url", "")
                    if url and url not in seen_urls and url not in new_urls:
                        raw_new.append(r)
                        new_urls.append(url)

            if raw_new:
                # Умный фильтр
                from ai_matcher import smart_filter
                ranked, used_ai = await smart_filter(raw_new, params)

                # Только реально подходящие
                new_listings = [
                    r for r in ranked
                    if not r.get("_damaged") and r.get("_ai_ok", True)
                ]
                ai_note = " (AI ✨)" if used_ai else ""

                # Обновляем историю
                _mark_seen(data, user_id_str, monitor_id, new_urls)

                if not new_listings:
                    logger.info("Монитор uid=%s: новые есть, но все отфильтрованы", user_id)
                    continue

                header = (
                    f"🔔 <b>Новые объявления{ai_note}!</b>\n"
                    f"🔎 {label}\n\n"
                )
                lines = [header]
                for r in new_listings[:10]:
                    title = (r.get("title") or "Объявление")[:70]
                    price = r.get("price") or "Цена не указана"
                    url   = r.get("url", "")
                    icon  = r.get("icon", "")
                    src   = r.get("source", "")
                    reason = r.get("_ai_reason", "")
                    ai_sc  = r.get("_ai_score")
                    score_str  = f" [{ai_sc}/10]" if ai_sc and used_ai else ""
                    reason_str = f"\n  💬 {reason}" if reason else ""
                    lines.append(
                        f'{icon} <b>{src}</b>{score_str}\n'
                        f'• <a href="{url}">{title}</a>\n'
                        f'  💰 {price}{reason_str}'
                    )

                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="\n".join(lines),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    logger.info(
                        "✅ Уведомление отправлено uid=%s, монитор=%s, новых=%d",
                        user_id, monitor_id, len(new_listings),
                    )
                except Exception as e:
                    logger.error("send notification uid=%s: %s", user_id, e)
            else:
                # Обновляем время последней проверки даже если новых нет
                _mark_seen(data, user_id_str, monitor_id, [])
                logger.info(
                    "ℹ️  Монитор uid=%s id=%s: новых нет", user_id, monitor_id
                )
