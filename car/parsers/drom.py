"""Drom.ru парсер на Playwright."""
import json
import logging
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from .base import BaseParser, is_good_car, clean_price

logger = logging.getLogger(__name__)

REGION_SUBDOMAINS = {
    "москва":"moscow","санкт-петербург":"spb","спб":"spb",
    "московская область":"mo","екатеринбург":"ekaterinburg",
    "краснодар":"krasnodar","новосибирск":"novosibirsk","казань":"kazan",
    "нижний новгород":"nn","самара":"samara","ростов-на-дону":"rostov",
    "челябинск":"chelyabinsk","уфа":"ufa","воронеж":"voronezh",
    "пермь":"perm","красноярск":"krasnoyarsk","омск":"omsk",
    "тюмень":"tyumen","волгоград":"volgograd","иркутск":"irkutsk",
    "хабаровск":"khabarovsk","владивосток":"vladivostok",
}
BRAND_SLUGS = {
    "toyota":"toyota","lada":"vaz","лада":"vaz","kia":"kia","hyundai":"hyundai",
    "volkswagen":"volkswagen","skoda":"skoda","renault":"renault","bmw":"bmw",
    "mercedes":"mercedes_benz","audi":"audi","nissan":"nissan","mazda":"mazda",
    "ford":"ford","mitsubishi":"mitsubishi","chevrolet":"chevrolet","geely":"geely",
    "chery":"chery","haval":"haval","honda":"honda","subaru":"subaru","lexus":"lexus",
    "volvo":"volvo","peugeot":"peugeot","citroen":"citroen","opel":"opel",
    "porsche":"porsche","land rover":"land_rover","jeep":"jeep","suzuki":"suzuki",
    "infiniti":"infiniti","changan":"changan","byd":"byd","omoda":"omoda","jetour":"jetour",
}


class DromParser(BaseParser):
    SOURCE_NAME = "Drom.ru"
    ICON        = "🟠"

    def _build_url(self, params: dict) -> str:
        region_raw = params.get("region","").lower().strip()
        subdomain  = REGION_SUBDOMAINS.get(region_raw, "auto")
        brand_raw  = params.get("brand","").lower()
        brand_slug = BRAND_SLUGS.get(brand_raw, brand_raw.replace(" ","_"))
        model_slug = params.get("model","").lower().replace(" ","_").replace("-","_")

        path = (f"/{brand_slug}/{model_slug}/" if brand_slug and model_slug
                else f"/{brand_slug}/" if brand_slug else "/all/")

        q: dict = {}
        if params.get("year_from"):                q["minyear"]   = str(params["year_from"])
        if params.get("year_to"):                  q["maxyear"]   = str(params["year_to"])
        if params.get("price_from") is not None:   q["minprice"]  = str(int(params["price_from"]))
        if params.get("price_to")   is not None:   q["maxprice"]  = str(int(params["price_to"]))
        if params.get("mileage_from") is not None: q["minprobeg"] = str(int(params["mileage_from"]))
        if params.get("mileage_to")   is not None: q["maxprobeg"] = str(int(params["mileage_to"]))
        q["unsold"] = "1"; q["order"] = "date"

        return f"https://{subdomain}.drom.ru{path}" + ("?" + urlencode(q) if q else "")

    def _extract(self, html: str) -> list[dict]:
        results = []
        seen    = set()

        # JSON
        for pat in [
            r'window\.transitState\s*=\s*(\{.+?\});\s*</script>',
            r'"bulls"\s*:\s*(\[.+?\])\s*,\s*"(?:total|count)"',
        ]:
            m = re.search(pat, html, re.S)
            if not m:
                continue
            try:
                data  = json.loads(m.group(1))
                bulls = (data if isinstance(data, list)
                         else data.get("bulls") or data.get("offers") or [])
                for b in bulls:
                    if not isinstance(b, dict):
                        continue
                    title = (b.get("title") or
                             " ".join(filter(None,[b.get("mark",""),b.get("model",""),
                                                   str(b.get("year",""))])))
                    if not title or not is_good_car(title, b.get("description","")):
                        continue
                    url = (b.get("url") or b.get("link") or "")
                    if not url:
                        bid = b.get("id") or b.get("bullId")
                        if bid: url = f"https://auto.drom.ru/sale/{bid}.html"
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    if not url.startswith("http"): url = "https://auto.drom.ru" + url

                    price  = clean_price(str(b.get("price") or b.get("priceRub") or ""))
                    km     = b.get("mileage") or b.get("probeg") or ""
                    km_str = f", {int(km):,} км".replace(",", " ") if km else ""

                    results.append({"source":self.SOURCE_NAME,"icon":self.ICON,
                                    "title":f"{title}{km_str}","price":price or "Цена не указана","url":url})
                    if len(results) >= self.MAX_RESULTS: break
                if results: return results
            except Exception as e:
                logger.warning("Drom JSON: %s", e)

        # HTML fallback
        soup  = BeautifulSoup(html, "lxml")
        cards = (soup.select("a[data-ftid='bull_title']")
                 or [a for a in soup.select("a[href]")
                     if "/sale/" in a.get("href","") and a.get_text(strip=True)])
        for card in cards:
            href  = card.get("href","")
            title = card.get_text(strip=True)
            if not href or href in seen or not title or not is_good_car(title): continue
            seen.add(href)
            parent = card.find_parent(attrs={"data-ftid": re.compile(r"bull")})
            price  = ""
            if parent:
                pe = parent.select_one("[data-ftid='bull_price']") or parent.select_one("[class*='Price']")
                if pe: price = clean_price(pe.get_text())
            if not href.startswith("http"): href = "https://auto.drom.ru" + href
            results.append({"source":self.SOURCE_NAME,"icon":self.ICON,
                            "title":title,"price":price or "Цена не указана","url":href})
            if len(results) >= self.MAX_RESULTS: break
        return results

    async def search(self, params: dict) -> list[dict]:
        url  = self._build_url(params)
        logger.info("Drom.ru → %s", url)
        html = await self._fetch(url, wait_for="a[data-ftid='bull_title']")
        if not html: return self._fallback(url)
        results = self._extract(html)
        logger.info("Drom.ru: %d объявлений", len(results))
        return results or self._fallback(url)

    def _fallback(self, url):
        return [{"source":self.SOURCE_NAME,"icon":self.ICON,
                 "title":"🔗 Открыть Drom.ru с фильтрами","price":"—","url":url}]
