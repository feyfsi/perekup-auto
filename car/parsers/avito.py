"""
Avito парсер.
Стратегия: RSS-лента + грубый regex-парсер (не XML, чтобы не падать на битом HTML).
"""
import asyncio
import logging
import random
import re
from urllib.parse import quote_plus

import aiohttp

from .base import is_good_car, clean_price

logger = logging.getLogger(__name__)

REGION_SLUGS = {
    "москва":"moskva","санкт-петербург":"sankt-peterburg","спб":"sankt-peterburg",
    "московская область":"moskovskaya_oblast","екатеринбург":"ekaterinburg",
    "краснодар":"krasnodar","новосибирск":"novosibirsk","казань":"kazan",
    "нижний новгород":"nizhniy_novgorod","самара":"samara",
    "ростов-на-дону":"rostov-na-donu","уфа":"ufa","воронеж":"voronezh",
    "пермь":"perm","красноярск":"krasnoyarsk","омск":"omsk",
    "челябинск":"chelyabinsk","тюмень":"tyumen",
}
MAX_RESULTS = 8


def _qs(params: dict) -> str:
    parts = []
    brand = params.get("brand","").strip()
    model = params.get("model","").strip()
    q     = " ".join(p for p in [brand, model] if p)
    if q: parts.append(f"q={quote_plus(q)}")
    if params.get("year_from"):                parts.append(f"params[8][from]={int(params['year_from'])}")
    if params.get("year_to"):                  parts.append(f"params[8][to]={int(params['year_to'])}")
    if params.get("mileage_from") is not None: parts.append(f"params[10][from]={int(params['mileage_from'])}")
    if params.get("mileage_to")   is not None: parts.append(f"params[10][to]={int(params['mileage_to'])}")
    if params.get("price_from")   is not None: parts.append(f"pmin={int(params['price_from'])}")
    if params.get("price_to")     is not None: parts.append(f"pmax={int(params['price_to'])}")
    parts.append("s=104")
    return "&".join(parts)


def _region_slug(params: dict) -> str:
    return REGION_SLUGS.get(params.get("region","").lower().strip(), "rossiya")


def _rss_url(params: dict) -> str:
    return f"https://www.avito.ru/{_region_slug(params)}/avtomobili.rss?{_qs(params)}"

def _web_url(params: dict) -> str:
    return f"https://www.avito.ru/{_region_slug(params)}/avtomobili?{_qs(params)}"


async def _fetch(url: str) -> str | None:
    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ]),
        "Accept":   "application/rss+xml,application/xml,text/xml,*/*",
        "Referer":  "https://www.avito.ru/",
    }
    try:
        await asyncio.sleep(random.uniform(0.5, 1.5))
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, headers=headers, ssl=False) as r:
                logger.info("Avito RSS HTTP %s", r.status)
                if r.status == 200:
                    return await r.text(errors="replace")
    except Exception as e:
        logger.error("Avito RSS fetch: %s", e)
    return None


def _parse_rss_regex(text: str) -> list[dict]:
    """
    Парсим RSS через regex — не падаем на битом XML/HTML внутри тегов.
    Ищем блоки <item>...</item> и вытаскиваем из каждого нужные поля.
    """
    results = []

    # Находим все блоки <item>
    item_blocks = re.findall(r'<item>(.*?)</item>', text, re.S)
    logger.info("Avito RSS regex: найдено %d item", len(item_blocks))

    for block in item_blocks:
        # Заголовок (может быть в CDATA)
        m_title = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', block, re.S)
        title   = m_title.group(1).strip() if m_title else ""

        # Ссылка
        m_link = re.search(r'<link>(.*?)</link>|<guid[^>]*>(.*?)</guid>', block, re.S)
        url    = ""
        if m_link:
            url = (m_link.group(1) or m_link.group(2) or "").strip()

        # Description (для цены и пробега)
        m_desc = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', block, re.S)
        desc   = m_desc.group(1).strip() if m_desc else ""
        # Убираем HTML теги из description
        desc_clean = re.sub(r'<[^>]+>', ' ', desc)

        if not title or not url:
            continue
        if not is_good_car(title, desc_clean):
            continue

        # Цена
        price = ""
        m_price = re.search(r'(\d[\d\s]{2,})\s*(?:руб|₽|р\.?\b)', desc_clean, re.I)
        if m_price:
            price = clean_price(m_price.group(1))

        # Пробег
        km_str = ""
        m_km = re.search(r'(\d[\d\s]+)\s*км\b', desc_clean, re.I)
        if m_km:
            km_raw = re.sub(r'\s', '', m_km.group(1))
            if km_raw.isdigit():
                km_str = f", {int(km_raw):,} км".replace(",", " ")

        results.append({
            "source": "Avito", "icon": "🔵",
            "title":  f"{title}{km_str}",
            "price":  price or "Цена не указана",
            "url":    url,
        })
        if len(results) >= MAX_RESULTS:
            break

    return results


class AvitoParser:
    SOURCE_NAME = "Avito"
    ICON        = "🔵"

    def _build_url(self, params: dict) -> str:
        return _web_url(params)

    async def search(self, params: dict) -> list[dict]:
        rss = _rss_url(params)
        web = _web_url(params)
        logger.info("Avito RSS → %s", rss)

        text = await _fetch(rss)
        if not text:
            return self._fallback(web)

        results = _parse_rss_regex(text)
        logger.info("Avito: %d объявлений", len(results))
        return results or self._fallback(web)

    def _fallback(self, url: str) -> list[dict]:
        return [{"source":self.SOURCE_NAME,"icon":self.ICON,
                 "title":"🔗 Открыть Avito — фильтры заполнены, нажмите для просмотра",
                 "price":"—","url":url}]
