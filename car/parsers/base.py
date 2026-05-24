"""
Базовый парсер на Playwright + stealth.
playwright-stealth скрывает все признаки автоматизации — сайты видят обычный браузер.
"""
import asyncio
import logging
import random
import re

logger = logging.getLogger(__name__)

BAD_WORDS = [
    "битый","битая","бита","после дтп","после аварии","дтп",
    "аварийн","конструктор","не на ходу","не заводится",
    "утопленник","утоплен","под разбор","на запчасти",
    "требует ремонта","требует вложений","не исправен",
    "восстановлен после","в ремонте","не исправна","неисправн",
    "кузовной ремонт","структурные повреждения",
]

def is_good_car(title: str, description: str = "") -> bool:
    text = (title + " " + description).lower()
    return not any(w in text for w in BAD_WORDS)

def clean_price(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", raw)
    if digits and len(digits) >= 4:
        return f"{int(digits):,} ₽".replace(",", " ")
    return raw.strip()


_browser  = None
_pw       = None

async def get_browser():
    global _browser, _pw
    if _browser is None or not _browser.is_connected():
        from playwright.async_api import async_playwright
        _pw      = await async_playwright().start()
        _browser = await _pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1366,768",
            ],
        )
        logger.info("Playwright браузер запущен")
    return _browser


# Реалистичные User-Agent'ы актуальных версий Chrome
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


async def new_page():
    browser = await get_browser()
    ctx = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=random.choice(USER_AGENTS),
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        extra_http_headers={
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        },
        java_script_enabled=True,
        # Имитируем обычные разрешения устройства
        color_scheme="light",
    )

    # Применяем stealth — скрываем ВСЕ признаки автоматизации
    try:
        from playwright_stealth import stealth_async
        page = await ctx.new_page()
        await stealth_async(page)
    except ImportError:
        # Если stealth не установлен — вручную патчим основные признаки
        page = await ctx.new_page()
        await page.add_init_script("""
            // Скрываем webdriver
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            // Добавляем плагины (пустой список = бот)
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    {name:'Chrome PDF Plugin'},
                    {name:'Chrome PDF Viewer'},
                    {name:'Native Client'},
                ],
            });
            // Добавляем языки
            Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru','en-US','en']});
            // Chrome runtime
            window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
            // Убираем следы headless
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({state: Notification.permission}) :
                    originalQuery(parameters)
            );
            Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 1});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
        """)

    return page, ctx


class BaseParser:
    SOURCE_NAME = "Unknown"
    ICON        = "❓"
    MAX_RESULTS = 8
    TIMEOUT     = 45_000  # мс — даём больше времени на загрузку

    async def _fetch(self, url: str, wait_for: str = None) -> str | None:
        """
        Открывает страницу в stealth-браузере.
        wait_for — CSS-селектор, которого ждём после загрузки (опционально).
        """
        page = ctx = None
        try:
            page, ctx = await new_page()

            # Сначала заходим на главную сайта — устанавливаем cookies как живой пользователь
            base = "/".join(url.split("/")[:3])
            try:
                await page.goto(base, wait_until="domcontentloaded", timeout=15_000)
                await asyncio.sleep(random.uniform(1.0, 2.0))
            except Exception:
                pass  # Не критично

            # Теперь идём на нужный URL
            await page.goto(url, wait_until="networkidle", timeout=self.TIMEOUT)
            await asyncio.sleep(random.uniform(2.0, 4.0))

            # Плавная прокрутка — триггерит ленивую загрузку контента
            for scroll in [300, 600, 900, 1200]:
                await page.evaluate(f"window.scrollTo({{top: {scroll}, behavior: 'smooth'}})")
                await asyncio.sleep(0.4)

            # Ждём конкретный элемент если указан
            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=10_000)
                except Exception:
                    pass

            html = await page.content()
            logger.info("%s: загружено %d байт", self.SOURCE_NAME, len(html))
            return html

        except Exception as e:
            logger.error("%s fetch: %s", self.SOURCE_NAME, e)
            return None
        finally:
            try:
                if page: await page.close()
                if ctx:  await ctx.close()
            except Exception:
                pass

    async def search(self, params: dict) -> list[dict]:
        raise NotImplementedError
