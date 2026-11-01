from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.ext import CommandHandler, ConversationHandler, MessageHandler

from health_bot.__main__ import TokenRedactionFilter, configure_logging, create_application
from health_bot.bot import HealthBotService, build_application
from health_bot.charts import recovery_chart
from health_bot.clients import FatSecretAdapter, GarminAdapter
from health_bot.config import Settings
from health_bot.models import DailyHealth, DailyNutrition
from health_bot.storage import SQLiteStorage


def _settings() -> Settings:
    return Settings.from_mapping(
        {
            "TELEGRAM_BOT_TOKEN": "123456:exact-secret-token",
            "TELEGRAM_USER_ID": "10",
            "TELEGRAM_CHAT_ID": "20",
        }
    )


def test_telegram_token_is_redacted_before_log_record_is_formatted():
    token = ":".join(("123456", "exact-secret-token"))
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "POST %s returned %s",
        (f"https://api.telegram.org/bot{token}/getUpdates", 500),
        None,
    )

    assert TokenRedactionFilter(token).filter(record)
    rendered = logging.Formatter("%(name)s: %(message)s").format(record)

    assert token not in rendered
    assert "[REDACTED]" in rendered


def test_http_transport_loggers_are_not_enabled_at_info():
    configure_logging("123456:exact-secret-token")
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
    assert logging.getLogger("telegram.request").level >= logging.WARNING


@pytest.mark.asyncio
async def test_garmin_maps_nested_weight_hrv_and_exact_day_body_battery():
    calls: dict[str, tuple] = {}

    class Client:
        def get_stats(self, day):
            return {"totalSteps": 1}

        def get_weigh_ins(self, start, end):
            calls["weight"] = (start, end)
            return {"totalAverage": {"weight": 70_500}}

        def get_hrv_data(self, day):
            return {"hrvSummary": {"lastNightAvg": 47, "weeklyAvg": 44}}

        def get_body_battery(self, start, end):
            calls["body"] = (start, end)
            return [
                {"date": "2026-02-02", "charged": 99},
                {"date": "2026-02-01", "charged": 61},
            ]

    row = await GarminAdapter(Client(), retries=1).get_day(date(2026, 2, 1))

    assert calls == {
        "weight": ("2026-02-01", "2026-02-01"),
        "body": ("2026-02-01", "2026-02-01"),
    }
    assert row.weight_kg == 70.5
    assert row.hrv == 47
    assert row.body_battery == 61


@pytest.mark.asyncio
async def test_garmin_does_not_turn_missing_body_battery_into_zero():
    class Client:
        def get_stats(self, day):
            return {}

        def get_body_battery(self, start, end):
            return [{"date": "2026-02-02", "charged": 0}]

    row = await GarminAdapter(Client(), retries=1).get_day(date(2026, 2, 1))
    assert row.body_battery is None


@pytest.mark.asyncio
async def test_fatsecret_month_endpoint_is_one_call_filtered_and_cached(tmp_path):
    epoch = date(1970, 1, 1)
    monthly_calls: list[date] = []
    daily_calls: list[date] = []

    class Diary:
        def entries_get_month_v2(self, date=None):
            monthly_calls.append(date)
            return [
                SimpleNamespace(
                    date_int=(date_value - epoch).days,
                    calories=calories,
                    protein=10,
                    carbohydrate=20,
                    fat=3,
                )
                for date_value, calories in (
                    (date_cls(2026, 2, 1), 100),
                    (date_cls(2026, 2, 2), 200),
                    (date_cls(2026, 2, 3), 300),
                )
            ]

        def entries_get_v2(self, date=None):
            daily_calls.append(date)
            return []

    date_cls = date
    storage = SQLiteStorage(tmp_path / "db.sqlite")
    storage.initialize()
    adapter = FatSecretAdapter(SimpleNamespace(diary=Diary()), storage, ttl_seconds=86_400)

    first = await adapter.get_month(2026, 2, through=date(2026, 2, 2))
    second = await adapter.get_month(2026, 2, through=date(2026, 2, 1))

    assert monthly_calls == [date(2026, 2, 1)]
    assert daily_calls == []
    assert [(row.day, row.calories) for row in first] == [
        (date(2026, 2, 1), 100),
        (date(2026, 2, 2), 200),
    ]
    assert [row.day for row in second] == [date(2026, 2, 1)]


@pytest.mark.asyncio
async def test_garmin_and_food_services_do_not_touch_the_other_source():
    class Nutrition:
        async def get_day(self, day):
            return DailyNutrition(day, calories=1234, protein=50, carbohydrate=100, fat=40)

    class Garmin:
        async def get_day(self, day):
            return DailyHealth(day, steps=8765, active_minutes=42, distance_km=6.5)

    class DisabledSource:
        async def get_day(self, day):
            raise AssertionError("disabled unrelated source was queried")

    garmin_service = HealthBotService(DisabledSource(), Garmin(), None)
    food_service = HealthBotService(Nutrition(), DisabledSource(), None)

    assert "8 765" in await garmin_service.garmin_day(date(2026, 2, 1))
    assert "1 234" in await food_service.food_day(date(2026, 2, 1))


@pytest.mark.asyncio
async def test_weight_and_recovery_return_no_chart_rows_without_measured_metrics():
    async def no_metrics(*_):
        return [DailyHealth(date(2026, 2, 1), steps=100)]

    service = HealthBotService(None, SimpleNamespace(get_range=no_metrics), None)
    weight_text, weight_rows = await service.weight(date(2026, 2, 1))
    recovery_text, recovery_rows = await service.recovery(date(2026, 2, 1))

    assert "нет данных" in weight_text.lower()
    assert weight_rows == []
    assert "нет данных" in recovery_text.lower()
    assert recovery_rows == []


def test_recovery_chart_omits_missing_points_instead_of_plotting_zero(monkeypatch):
    captured = None

    def fake_plot(title, series):
        nonlocal captured
        captured = series
        return SimpleNamespace()

    monkeypatch.setattr("health_bot.charts._plot", fake_plot)
    recovery_chart(
        [
            DailyHealth(date(2026, 2, 1), hrv=50),
            DailyHealth(date(2026, 2, 2), resting_hr=60),
        ]
    )

    assert captured == [
        ("HRV", ["2026-02-01"], [50.0]),
        ("Resting HR", ["2026-02-02"], [60.0]),
    ]


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id=10, chat_id=20, text=""):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage(text)


def _command_handler(app, command):
    return next(
        handler
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler) and command in handler.commands
    )


def _application_error_handler(app):
    return next(iter(app.error_handlers))


def test_dockerfile_checks_garmin_token_directory_is_writable_by_final_user():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()

    assert "install -d -o bot -g bot /home/bot/.garminconnect" in dockerfile
    user_position = dockerfile.index("USER bot")
    write_check_position = dockerfile.index("test -w /home/bot/.garminconnect")
    command_position = dockerfile.index('CMD ["python", "-m", "health_bot"]')
    assert user_position < write_check_position < command_position


@pytest.mark.asyncio
async def test_garmin_login_failure_does_not_block_food_application(monkeypatch, tmp_path, caplog):
    credential = "never-log-this-password"
    sensitive_url = "https://connect.example/session?" + "token=never-log-this-token"

    class FailingGarmin:
        def __init__(self, email, password):
            assert password == credential

        def login(self, session_dir):
            raise RuntimeError(f"MFA unavailable at {sensitive_url} using {credential}")

    class Diary:
        def entries_get_v2(self, date=None):
            return [SimpleNamespace(calories=1234, protein=50, carbohydrate=100, fat=40)]

    class WorkingFatSecret:
        def __init__(self, *_args, **_kwargs):
            self.diary = Diary()

    monkeypatch.setattr("health_bot.__main__.Garmin", FailingGarmin)
    monkeypatch.setattr("health_bot.__main__.Fatsecret", WorkingFatSecret)
    settings = Settings.from_mapping(
        {
            "TELEGRAM_BOT_TOKEN": "123456:test-token",
            "TELEGRAM_USER_ID": "10",
            "TELEGRAM_CHAT_ID": "20",
            "DATABASE_PATH": str(tmp_path / "db.sqlite"),
            "EMAIL": "owner@example.com",
            "PASSWORD": credential,
            "FATSECRET_CONSUMER_KEY": "consumer-key",
            "FATSECRET_CONSUMER_SECRET": "consumer-secret",
        }
    )
    caplog.set_level(logging.WARNING)

    app = create_application(settings)
    food_update = FakeUpdate(text="/food")
    await _command_handler(app, "food").callback(food_update, SimpleNamespace(args=[]))

    assert "1 234" in food_update.effective_message.replies[-1]
    assert credential not in caplog.text
    assert sensitive_url not in caplog.text

    garmin_update = FakeUpdate(text="/garmin")
    with pytest.raises(Exception) as raised:
        await _command_handler(app, "garmin").callback(garmin_update, SimpleNamespace(args=[]))
    await _application_error_handler(app)(garmin_update, SimpleNamespace(error=raised.value))
    assert "garmin" in garmin_update.effective_message.replies[-1].lower()
    assert "временно недоступ" in garmin_update.effective_message.replies[-1].lower()


@pytest.mark.asyncio
async def test_application_error_handler_is_registered_safe_and_authorized_only(caplog):
    app = build_application(_settings(), HealthBotService(None, None, None))
    assert len(app.error_handlers) == 1
    handler = _application_error_handler(app)
    secret_error = RuntimeError(
        "request failed https://api.example/path?token=secret-token password=secret-password"
    )
    caplog.set_level(logging.ERROR)

    authorized = FakeUpdate(text="/food")
    await handler(authorized, SimpleNamespace(error=secret_error))

    assert authorized.effective_message.replies == ["⚠️ Не удалось выполнить команду. Попробуйте позже."]
    assert "RuntimeError" in caplog.text
    assert "secret-token" not in caplog.text
    assert "secret-password" not in caplog.text
    assert "api.example" not in caplog.text

    unauthorized = FakeUpdate(user_id=999, text="/food")
    await handler(unauthorized, SimpleNamespace(error=secret_error))
    assert unauthorized.effective_message.replies == []



def _settime_conversation(app):
    return next(
        handler
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, ConversationHandler)
    )


@pytest.mark.asyncio
async def test_settime_conversation_registers_authorized_text_and_cancel(tmp_path):
    storage = SQLiteStorage(tmp_path / "db.sqlite")
    storage.initialize()
    app = build_application(_settings(), HealthBotService(None, None, storage))
    conversation = _settime_conversation(app)

    assert len(conversation.entry_points) == 1
    assert isinstance(conversation.entry_points[0], CommandHandler)
    state_handler = next(iter(conversation.states.values()))[0]
    assert isinstance(state_handler, MessageHandler)
    cancel_handler = conversation.fallbacks[0]
    assert isinstance(cancel_handler, CommandHandler)

    context = SimpleNamespace(args=[])
    prompt_update = FakeUpdate(text="/settime")
    state = await conversation.entry_points[0].callback(prompt_update, context)
    assert state in conversation.states
    assert "чч:мм" in prompt_update.effective_message.replies[-1].lower()

    unauthorized = FakeUpdate(user_id=11, text="08:30")
    await state_handler.callback(unauthorized, SimpleNamespace(args=[]))
    assert storage.get_setting("report_time") is None
    assert "доступ запрещ" in unauthorized.effective_message.replies[-1].lower()

    authorized = FakeUpdate(text="08:30")
    result = await state_handler.callback(authorized, SimpleNamespace(args=[]))
    assert result == ConversationHandler.END
    assert storage.get_setting("report_time") == "08:30"

    rejected_cancel = FakeUpdate(user_id=11, text="/cancel")
    await cancel_handler.callback(rejected_cancel, SimpleNamespace(args=[]))
    assert "доступ запрещ" in rejected_cancel.effective_message.replies[-1].lower()


@pytest.mark.asyncio
async def test_settime_with_argument_still_saves_immediately(tmp_path):
    storage = SQLiteStorage(tmp_path / "db.sqlite")
    storage.initialize()
    app = build_application(_settings(), HealthBotService(None, None, storage))
    conversation = _settime_conversation(app)
    update = FakeUpdate(text="/settime 09:45")

    result = await conversation.entry_points[0].callback(update, SimpleNamespace(args=["09:45"]))

    assert result == ConversationHandler.END
    assert storage.get_setting("report_time") == "09:45"
