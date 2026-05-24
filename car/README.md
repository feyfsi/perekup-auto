# 🚗 CarSearchBot — Telegram-бот поиска авто по РФ

Бот ищет автомобили одновременно на четырёх крупнейших площадках России:

| Площадка | Иконка | Статус |
|----------|--------|--------|
| Auto.ru  | 🚗 | ✅ |
| Avito    | 🔵 | ✅ (антибот — fallback-ссылка) |
| Drom.ru  | 🟠 | ✅ |
| Am.ru    | 🟢 | ✅ |

---

## 📋 Требования

- Python 3.10+
- Telegram Bot Token (получить у [@BotFather](https://t.me/BotFather))

---

## ⚡ Быстрый старт

### 1. Клонируйте / распакуйте проект

```bash
cd car_bot
```

### 2. Создайте виртуальное окружение

```bash
python -m venv venv
source venv/bin/activate      # Linux / macOS
venv\Scripts\activate         # Windows
```

### 3. Установите зависимости

```bash
pip install -r requirements.txt
```

### 4. Настройте токен

```bash
cp .env.example .env
```

Откройте `.env` и вставьте токен бота:

```
BOT_TOKEN=123456789:AABBccDDeEFfGgHhIiJjKkLlMm...
```

### 5. Запустите бота

```bash
python bot.py
```

---

## 🔎 Как пользоваться ботом

1. Напишите боту `/start` или `/search`
2. Выберите **марку** (кнопки или вручную)
3. Введите **модель** (или пропустите)
4. Укажите **год от/до** (или пропустите)
5. Укажите **цену от/до** (или пропустите)
6. Укажите **максимальный пробег** (или пропустите)
7. Выберите **регион** (или вся Россия)
8. Нажмите **🔍 Искать!**

Бот параллельно опрашивает все площадки и выдаёт результаты по каждой отдельным блоком.

---

## 📁 Структура проекта

```
car_bot/
├── bot.py                  # Основной файл — логика бота и диалог
├── requirements.txt        # Зависимости
├── .env.example            # Шаблон переменных окружения
├── .env                    # Ваш токен (не коммитить в git!)
└── parsers/
    ├── __init__.py         # Экспорт парсеров
    ├── base.py             # Базовый класс (HTTP, retry, user-agent)
    ├── autoru.py           # Парсер Auto.ru
    ├── avito.py            # Парсер Avito
    ├── drom.py             # Парсер Drom.ru
    └── amru.py             # Парсер Am.ru
```

---

## ⚙️ Параметры поиска

| Параметр | Обязателен | Пример |
|----------|-----------|--------|
| Марка | ✅ | Toyota |
| Модель | ❌ | Camry |
| Год от | ❌ | 2018 |
| Год до | ❌ | 2023 |
| Цена от | ❌ | 500000 |
| Цена до | ❌ | 2000000 |
| Пробег до | ❌ | 100000 |
| Регион | ❌ | Москва |

---

## 🛡️ Замечания по парсингу

Площадки периодически меняют вёрстку и могут использовать антибот-защиту:

- **Auto.ru** — парсится напрямую, работает стабильно.
- **Avito** — активная антибот-защита (капча). При блокировке бот возвращает прямую ссылку на поиск — пользователь переходит и видит результаты.
- **Drom.ru** — парсится напрямую, работает стабильно.
- **Am.ru** — парсится напрямую, работает стабильно.

Если площадка заблокировала парсер, бот возвращает **прямую ссылку на поиск** с уже заполненными параметрами — пользователь может открыть её в браузере одним кликом.

---

## 🔧 Добавление новой площадки

1. Создайте файл `parsers/mysite.py`, наследуясь от `BaseParser`
2. Реализуйте методы `_build_url(params)` и `search(params)`
3. Добавьте класс в `parsers/__init__.py`
4. Добавьте экземпляр в список `PARSERS` в `bot.py`

Пример минимального парсера:

```python
from .base import BaseParser
from bs4 import BeautifulSoup

class MySiteParser(BaseParser):
    SOURCE_NAME = "MySite"
    BASE_URL = "https://mysite.ru"
    ICON = "🔷"

    def _build_url(self, params):
        return f"{self.BASE_URL}/search?brand={params.get('brand', '')}"

    async def search(self, params):
        url = self._build_url(params)
        html = await self._get(url)
        if not html:
            return [{"source": self.SOURCE_NAME, "icon": self.ICON,
                     "title": "Открыть поиск", "price": "—", "url": url}]
        soup = BeautifulSoup(html, "lxml")
        results = []
        for card in soup.select("a.car-card")[:self.MAX_RESULTS]:
            results.append({
                "source": self.SOURCE_NAME,
                "icon": self.ICON,
                "title": card.get_text(strip=True),
                "price": "—",
                "url": card.get("href", url),
            })
        return results
```

---

## 🚀 Деплой на сервер (Ubuntu/Debian)

```bash
# Systemd-сервис для автозапуска
sudo nano /etc/systemd/system/carbot.service
```

```ini
[Unit]
Description=CarSearchBot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/car_bot
ExecStart=/home/ubuntu/car_bot/venv/bin/python bot.py
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/car_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable carbot
sudo systemctl start carbot
sudo systemctl status carbot
```

---

## 📝 Лицензия

MIT — используйте свободно.
