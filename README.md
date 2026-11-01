# fatSecretBot

Telegram-бот для API-first аналитики FatSecret и Garmin Connect. Данные питания и здоровья запрашиваются по требованию; SQLite хранит только цели, настройки расписания, разрешённые идентификаторы, sync state и TTL-кеш не дольше 24 часов.

## Команды

Сохранены прежние `/today`, `/report`, `/week`, `/sendreport`, `/garmin`, `/food`, `/start`, `/help`, `/authfat`, `/setupgarmin`, `/settime`.

Новая аналитика:

- `/month` — итоги и средние за текущий месяц;
- `/weight` — тренд веса и среднее за последние 7 доступных измерений + PNG;
- `/recovery` — baseline HRV, RHR, сна, стресса и Body Battery + PNG;
- `/training` — активности, шаги, активные минуты и дистанция + PNG;
- `/goals` — список целей;
- `/goals add steps 10000 daily` — добавить цель;
- `/goals set 1 12000`, `/goals pause 1`, `/goals resume 1`, `/goals delete 1` — изменить цель.

Оценки и корреляции в ответах являются наблюдениями по данным трекеров, а не медицинскими диагнозами.

## Безопасность

- Каждый входящий command handler проверяет одновременно `TELEGRAM_USER_ID` и `TELEGRAM_CHAT_ID`.
- Garmin-пароль и OAuth-токены нельзя вводить или получать через Telegram. `/setupgarmin` и `/authfat` направляют к локальной настройке `.env`.
- Секреты не пишутся в SQLite, ответы или логи. `.env`, Garmin tokens и базы исключены из Git/Docker context.
- Время задаётся IANA-зоной `TIMEZONE` и обрабатывается через `zoneinfo` с учётом DST.
- FatSecret cache автоматически удаляет истёкшие записи и запрещает TTL больше 86400 секунд. Долгосрочная история калорий/БЖУ/веса не сохраняется.

## Локальный запуск

Требуется Python 3.12+.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполните .env локально
python -m health_bot
# прежняя точка входа также работает:
python telegram_bot.py
```

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID` и `TELEGRAM_CHAT_ID` обязательны. `TIMEZONE` — имя из IANA, например `Europe/Moscow`, а не `UTC+3`.

Адаптеры обнаруживают доступные методы SDK. Синхронные запросы диапазонов выполняются через `asyncio.to_thread`; Garmin использует retry с exponential backoff, а отсутствующие метрики остаются `None`.

## Docker

```bash
cp .env.example .env
# заполните .env
docker compose up --build -d
docker compose ps
```

State и Garmin session tokens размещаются в отдельных volumes. Health check запускает `python -m health_bot.healthcheck`.

## Разработка и проверки

```bash
pytest -q
ruff check .
python -m compileall -q health_bot telegram_bot.py tests
```

CI в `.github/workflows/tests.yml` выполняет тесты, Ruff, compileall и Gitleaks. Тесты используют фикстуры SDK и не обращаются к реальным API или секретам.

## Структура

```text
health_bot/
  config.py       # строгая конфигурация и zoneinfo
  models.py       # доменные модели
  clients.py      # API adapters, retries, capability fallback
  storage.py      # SQLite schema, goals, settings/state, TTL cache
  analytics.py    # периодные итоги, weight/recovery/training
  charts.py       # временные PNG и безопасное удаление
  bot.py          # authorization policy, commands and services
telegram_bot.py   # совместимая точка входа
```
