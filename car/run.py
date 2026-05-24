"""
Запускает бота И веб-сервер одновременно.
Использование:  python run.py
"""
import asyncio
import logging
import os
import sys
import threading

import uvicorn
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

def run_webapp():
    """Запускает FastAPI сервер в отдельном потоке."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from webapp.server import app
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")

def run_bot():
    """Запускает Telegram бота."""
    from bot import main
    main()

if __name__ == "__main__":
    # Запускаем веб-сервер в фоне
    t = threading.Thread(target=run_webapp, daemon=True)
    t.start()
    logging.info("Веб-сервер запущен на http://localhost:8080")

    # Запускаем бота в основном потоке
    run_bot()
