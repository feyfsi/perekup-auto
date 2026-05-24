"""
Auto.ru парсер.
Стратегия: открываем страницу через Playwright (stealth),
вытаскиваем данные из window.__INITIAL_STATE__ в HTML.
"""
import json
import logging
import re
from urllib.parse import urlencode

from .base import BaseParser, is_good_car, clean_price

logger = logging.getLogger(__name__)

BRAND_SLUGS = {
    "toyota":"toyota","lada":"vaz","ваз":"vaz","kia":"kia","hyundai":"hyundai",
    "volkswagen":"volkswagen","skoda":"skoda","renault":"renault","bmw":"bmw",
    "mercedes":"mercedes","audi":"audi","nissan":"nissan","mazda":"mazda",
    "ford":"ford","mitsubishi":"mitsubishi","chevrolet":"chevrolet",
    "geely":"geely","chery":"chery","haval":"haval","omoda":"omoda",
    "jetour":"jetour","exeed":"exeed","changan":"changan","honda":"honda",
    "subaru":"subaru","lexus":"lexus","volvo":"volvo","peugeot":"peugeot",
    "citroen":"citroen","opel":"opel","porsche":"porsche",
    "land rover":"land_rover","jeep":"jeep","suzuki":"suzuki",
    "infiniti":"infiniti","byd":"byd",
}
GEO = {
    "москва":"213","санкт-петербург":"2","спб":"2","московская область":"1",
    "екатеринбург":"54","краснодар":"35","новосибирск":"65","казань":"43",
    "нижний новгород":"47","самара":"51","ростов-на-дону":"39",
    "челябинск":"56","уфа":"172","воронеж":"193","пермь":"50",
    "красноярск":"62","омск":"66","тюмень":"55","волгоград":"38",
}


class AutoRuParser(BaseParser):
    SOURCE_NAME = "Auto.ru"
    BASE_URL    = "https://auto.ru"
    ICON        = "🚗"

    def _build_url(self, params: dict) -> str:
        brand_raw  = params.get("brand","").lower()
        brand_slug = BRAND_SLUGS.get(brand_raw, brand_raw.replace(" ","_"))
        model_slug = params.get("model","").lower().replace(" ","_")

        if brand_slug and model_slug:
            path = f"/cars/{brand_slug}/{model_slug}/used/?"
        elif brand_slug:
            path = f"/cars/{brand_slug}/used/?"
        else:
            path = "/cars/used/?"

        q: dict = {}
        if params.get("year_from"):                q["year_from"]   = str(params["year_from"])
        if params.get("year_to"):                  q["year_to"]     = str(params["year_to"])
        if params.get("price_from") is not None:   q["price_from"]  = str(int(params["price_from"]))
        if params.get("price_to")   is not None:   q["price_to"]    = str(int(params["price_to"]))
        if params.get("mileage_from") is not None: q["km_age_from"] = str(int(params["mileage_from"]))
        if params.get("mileage_to")   is not None: q["km_age_to"]   = str(int(params["mileage_to"]))
        q["geo_id"] = GEO.get(params.get("region","").lower(), "225")
        q["sort"]   = "fresh_relevance_1-desc"
        return self.BASE_URL + path + urlencode(q)

    def _extract(self, html: str, web_url: str) -> list[dict]:
        results = []
        seen    = set()

        # Ищем window.__INITIAL_STATE__ = {...}
        # Auto.ru разбивает JSON на несколько присваиваний, берём первый большой блок
        m = re.search(
            r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;?\s*(?:window\.|</script>)',
            html, re.S
        )
        if not m:
            # Запасной вариант — ищем просто массив offers
            m2 = re.search(r'"offers"\s*:\s*(\[.+?\])\s*,\s*"pagination"', html, re.S)
            if m2:
                try:
                    offers = json.loads(m2.group(1))
                    return self._offers_to_list(offers, web_url, seen)
                except Exception:
                    pass
            logger.warning("Auto.ru: __INITIAL_STATE__ не найден")
            return []

        try:
            state  = json.loads(m.group(1))
            offers = (
                state.get("listing", {}).get("data", {}).get("offers") or
                state.get("searchResult", {}).get("offers") or
                state.get("offers", {}).get("offers") or
                []
            )
            return self._offers_to_list(offers, web_url, seen)
        except json.JSONDecodeError:
            # JSON может быть обрезан — ищем offers внутри сырого текста
            m3 = re.search(r'"offers"\s*:\s*(\[.+?\])\s*,\s*"(?:total|pagination|status)"', html, re.S)
            if m3:
                try:
                    offers = json.loads(m3.group(1))
                    return self._offers_to_list(offers, web_url, seen)
                except Exception:
                    pass
            logger.warning("Auto.ru: JSON не распарсился")
            return []

    def _offers_to_list(self, offers: list, web_url: str, seen: set) -> list[dict]:
        results = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            desc = offer.get("description","")
            if not is_good_car(desc):
                continue

            vehicle = offer.get("vehicle_info", {})
            mark    = vehicle.get("mark_info",  {}).get("name","")
            model   = vehicle.get("model_info", {}).get("name","")
            year    = offer.get("documents", {}).get("year","")
            km      = offer.get("state",     {}).get("mileage","")
            price   = offer.get("price_info",{}).get("price","")
            oid     = offer.get("id","")
            hash_   = offer.get("hash","")

            title = " ".join(filter(None,[mark, model, str(year) if year else ""]))
            if not title or title in seen:
                continue
            seen.add(title + str(oid))

            km_str    = f", {int(km):,} км".replace(",", " ") if km else ""
            price_str = f"{int(price):,} ₽".replace(",", " ") if price else "Цена не указана"

            if oid and hash_:
                bs  = BRAND_SLUGS.get(mark.lower(), mark.lower())
                url = f"{self.BASE_URL}/cars/used/sale/{bs}/{oid}-{hash_}/"
            else:
                url = web_url

            results.append({
                "source": self.SOURCE_NAME, "icon": self.ICON,
                "title":  f"{title}{km_str}",
                "price":  price_str, "url": url,
            })
            if len(results) >= self.MAX_RESULTS:
                break
        return results

    async def search(self, params: dict) -> list[dict]:
        web_url = self._build_url(params)
        logger.info("Auto.ru → %s", web_url)

        # Открываем через stealth-браузер
        html = await self._fetch(
            web_url,
            wait_for="[class*='ListingItem']",
        )
        if not html:
            return self._fallback(web_url)

        results = self._extract(html, web_url)
        logger.info("Auto.ru: %d объявлений", len(results))
        return results or self._fallback(web_url)

    def _fallback(self, url: str) -> list[dict]:
        return [{"source":self.SOURCE_NAME,"icon":self.ICON,
                 "title":"🔗 Открыть Auto.ru — фильтры заполнены","price":"—","url":url}]
