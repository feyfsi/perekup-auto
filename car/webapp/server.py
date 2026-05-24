"""
FastAPI сервер для Telegram Mini App.
Отдаёт HTML страницу и принимает поисковые запросы от неё.
"""
import asyncio
import logging
import os
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Добавляем корневую папку проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import AutoRuParser, AvitoParser, DromParser
import monitor as mon

logger = logging.getLogger(__name__)

app = FastAPI(title="CarSearch Mini App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PARSERS = [AutoRuParser(), AvitoParser(), DromParser()]


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.post("/search")
async def search(request: Request):
    """Принимает параметры поиска, возвращает объявления со всех площадок."""
    try:
        params = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # Нормализуем числовые поля
    for key in ("year_from", "year_to", "price_from", "price_to", "mileage_from", "mileage_to"):
        val = params.get(key)
        if val is not None and str(val).strip():
            try:
                params[key] = int(str(val).replace(" ", "").replace("\xa0", ""))
            except ValueError:
                params.pop(key, None)
        else:
            params.pop(key, None)

    tasks   = [p.search(params) for p in PARSERS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = []
    for parser, res in zip(PARSERS, results):
        if isinstance(res, Exception):
            logger.error("%s: %s", parser.SOURCE_NAME, res)
            continue
        for r in (res or []):
            output.append({
                "source": r.get("source", ""),
                "icon":   r.get("icon", ""),
                "title":  r.get("title", ""),
                "price":  r.get("price", ""),
                "url":    r.get("url", ""),
            })

    return JSONResponse({"results": output, "count": len(output)})


@app.post("/monitor/add")
async def add_monitor(request: Request):
    """Добавляет монитор для пользователя."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    user_id = body.get("user_id")
    params  = body.get("params", {})

    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)

    try:
        mid = mon.add_monitor(int(user_id), params)
        return JSONResponse({"ok": True, "monitor_id": mid})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/monitor/list/{user_id}")
async def list_monitors(user_id: int):
    monitors = mon.list_monitors(user_id)
    return JSONResponse({"monitors": monitors})


@app.delete("/monitor/{user_id}/{monitor_id}")
async def delete_monitor(user_id: int, monitor_id: int):
    ok = mon.remove_monitor(user_id, monitor_id)
    return JSONResponse({"ok": ok})
