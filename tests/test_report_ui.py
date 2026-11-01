from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram.ext import CommandHandler, MessageHandler

from health_bot import bot as bot_module
from health_bot.bot import HealthBotService, build_application
from health_bot.config import Settings
from health_bot.models import DailyHealth, DailyNutrition


class NutritionSource:
    async def get_day(self, day: date) -> DailyNutrition:
        return DailyNutrition(day, calories=2617, protein=124, carbohydrate=153, fat=168)

    async def get_range(self, start: date, end: date) -> list[DailyNutrition]:
        return [await self.get_day(start)]

    async def get_month(self, year: int, month: int, *, through: date | None = None) -> list[DailyNutrition]:
        current = date(year, month, 1)
        calories = 2617 if month == 11 else 2745
        return [DailyNutrition(current, calories=calories, protein=124, carbohydrate=153, fat=168)]


class GarminSource:
    async def get_day(self, day: date) -> DailyHealth:
        return DailyHealth(
            day,
            steps=4650,
            calories=2380,
            active_calories=620,
            active_minutes=61,
            distance_km=2.4,
            resting_hr=54,
            hrv=48,
            sleep_hours=7.5,
            stress=24,
            body_battery=72,
            weight_kg=82.4,
            activities=1,
        )

    async def get_range(self, start: date, end: date) -> list[DailyHealth]:
        return [await self.get_day(start)]


@pytest.fixture
def service() -> HealthBotService:
    return HealthBotService(NutritionSource(), GarminSource(), None)


@pytest.mark.asyncio
async def test_period_report_uses_emoji_sections_instead_of_dense_sentences(service):
    text = await service.period(date(2026, 11, 1), 1)

    assert "📊 <b>Сводка за день" in text
    assert "🥗 <b>Питание</b>" in text
    assert "🔥 Калории: 2 617 ккал" in text
    assert "🥩 Белки: 124 г" in text
    assert "🍞 Углеводы: 153 г" in text
    assert "🥑 Жиры: 168 г" in text
    assert "🏃 <b>Активность</b>" in text
    assert "👟 Шаги: 4 650" in text
    assert "⏱️ Активные минуты: 61" in text
    assert "📏 Дистанция: 2.4 км" in text
    assert "ℹ️ <i>Все оценки" in text
    assert "Питание: 2 617 ккал;" not in text


@pytest.mark.asyncio
async def test_garmin_and_food_reports_have_old_style_metric_layout(service):
    garmin = await service.garmin_day(date(2026, 11, 1))
    food = await service.food_day(date(2026, 11, 1))

    assert "⌚ <b>Garmin — 01.11.2026</b>" in garmin
    assert "👟 Шаги: 4 650" in garmin
    assert "🔥 Калории: 2 380 ккал" in garmin
    assert "⚡ Активные калории: 620 ккал" in garmin
    assert "💓 Пульс в покое: 54 уд/мин" in garmin

    assert "🥗 <b>Питание — 01.11.2026</b>" in food
    assert "🔥 Калории: 2 617 ккал" in food
    assert "🥩 Белки: 124 г" in food
    assert "🍞 Углеводы: 153 г" in food
    assert "🥑 Жиры: 168 г" in food


@pytest.mark.asyncio
async def test_month_report_separates_totals_activity_and_comparison(service):
    text = await service.month(date(2026, 11, 1))

    assert "📅 <b>Итоги за ноябрь 2026</b>" in text
    assert "🥗 <b>Питание</b>" in text
    assert "🏃 <b>Активность</b>" in text
    assert "📈 <b>К предыдущему периоду</b>" in text
    assert "🔥 Калории:" in text
    assert "👟 Шаги:" in text
    assert "ℹ️ <i>Все оценки" in text
    assert "Активность: 4 650 шагов" not in text


@pytest.mark.asyncio
async def test_weight_recovery_and_training_reports_use_scannable_emoji_lines(service):
    weight, _ = await service.weight(date(2026, 11, 1))
    recovery, _ = await service.recovery(date(2026, 11, 1))
    training, _ = await service.training(date(2026, 11, 1))

    assert "⚖️ <b>Вес" in weight and "🏁 Начало:" in weight and "🎯 Сейчас:" in weight
    assert "💚 <b>Восстановление" in recovery
    assert "💓 Пульс в покое:" in recovery
    assert "😴 Сон:" in recovery
    assert "🔋 Body Battery:" in recovery
    assert "🏋️ <b>Тренировки" in training
    assert "🎯 Активности:" in training
    assert "👟 Шаги:" in training


@pytest.mark.asyncio
async def test_nutrition_metrics_use_protein_fat_carbohydrate_order(service):
    reports = [
        await service.period(date(2026, 11, 1), 1),
        await service.food_day(date(2026, 11, 1)),
        await service.month(date(2026, 11, 1)),
    ]

    for text in reports:
        assert text.index("🥩 Белки:") < text.index("🥑 Жиры:") < text.index("🍞 Углеводы:")


@pytest.mark.asyncio
async def test_period_and_month_reports_show_final_calorie_surplus(service):
    period = await service.period(date(2026, 11, 1), 1)
    month = await service.month(date(2026, 11, 1))

    for text in (period, month):
        assert "⚖️ <b>Сальдо калорий</b>" in text
        assert "🍽 Получено: 2 617 ккал" in text
        assert "🔥 Потрачено: 2 380 ккал" in text
        assert "🔺 Профицит: 237 ккал" in text


@pytest.mark.asyncio
async def test_period_report_labels_calorie_deficit():
    class DeficitNutrition(NutritionSource):
        async def get_day(self, day: date) -> DailyNutrition:
            return DailyNutrition(day, calories=1800, protein=100, carbohydrate=150, fat=60)

    service = HealthBotService(DeficitNutrition(), GarminSource(), None)
    text = await service.period(date(2026, 11, 1), 1)

    assert "🔻 Дефицит: 580 ккал" in text


@pytest.mark.asyncio
async def test_balance_is_not_calculated_from_incomplete_period_data():
    class MissingNutrition(NutritionSource):
        async def get_range(self, start: date, end: date) -> list[DailyNutrition]:
            return [DailyNutrition(start, calories=0)]

    service = HealthBotService(MissingNutrition(), GarminSource(), None)
    text = await service.period(date(2026, 11, 1), 1)

    assert "⚠️ Недостаточно данных для сальдо" in text
    assert "Дефицит" not in text
    assert "Профицит" not in text


@pytest.mark.asyncio
async def test_balance_requires_complete_coverage_for_multi_day_period():
    service = HealthBotService(NutritionSource(), GarminSource(), None)
    text = await service.period(date(2026, 11, 1), 7)

    assert "📝 Питание: 1/7 дней" in text
    assert "🔥 Расход: 1/7 дней" in text
    assert "Дефицит" not in text
    assert "Профицит" not in text


@pytest.mark.asyncio
async def test_balance_coverage_uses_unique_dates_inside_period():
    repeated_day = date(2026, 10, 31)

    class DuplicateNutrition(NutritionSource):
        async def get_range(self, start: date, end: date) -> list[DailyNutrition]:
            row = DailyNutrition(repeated_day, calories=1800, protein=100, carbohydrate=150, fat=60)
            return [row, row]

    class DuplicateGarmin(GarminSource):
        async def get_range(self, start: date, end: date) -> list[DailyHealth]:
            row = DailyHealth(repeated_day, calories=2380)
            return [row, row]

    service = HealthBotService(DuplicateNutrition(), DuplicateGarmin(), None)
    text = await service.period(date(2026, 11, 1), 2)

    assert "📝 Питание: 1/2 дней" in text
    assert "🔥 Расход: 1/2 дней" in text
    assert "Дефицит" not in text
    assert "Профицит" not in text


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs) -> Any:
        self.replies.append((text, kwargs))
        return self


class FakeUpdate:
    def __init__(self, text: str):
        self.effective_user = type("User", (), {"id": 10})()
        self.effective_chat = type("Chat", (), {"id": 20})()
        self.effective_message = FakeMessage(text)


def _settings() -> Settings:
    return Settings.from_mapping(
        {
            "TELEGRAM_BOT_TOKEN": "123:TEST",
            "TELEGRAM_USER_ID": "10",
            "TELEGRAM_CHAT_ID": "20",
        }
    )


def _command_callback(app, command: str):
    return next(
        handler.callback
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler) and command in handler.commands
    )


def _menu_callback(app):
    return next(
        handler.callback
        for handlers in app.handlers.values()
        for handler in handlers
        if isinstance(handler, MessageHandler)
    )


@pytest.mark.asyncio
async def test_data_command_sends_immediate_loading_feedback(service):
    app = build_application(_settings(), service)
    update = FakeUpdate("/garmin")

    await _command_callback(app, "garmin")(cast(Any, update), type("Context", (), {"args": []})())

    assert update.effective_message.replies[0][0].startswith("⏳")
    assert "Garmin" in update.effective_message.replies[-1][0]


@pytest.mark.asyncio
async def test_start_has_structured_text_and_reply_keyboard(service):
    app = build_application(_settings(), service)
    update = FakeUpdate("/start")

    await _command_callback(app, "start")(cast(Any, update), type("Context", (), {"args": []})())

    text, kwargs = update.effective_message.replies[-1]
    assert "👋 <b>Health Bot</b>" in text
    assert "📊 <b>Отчёты</b>" in text
    assert "⌚ <b>Источники</b>" in text
    keyboard = kwargs.get("reply_markup")
    assert keyboard is not None
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert {"📊 Сегодня", "📋 Вчера", "📈 Неделя", "📅 Месяц"} <= set(labels)
    assert getattr(bot_module, "MENU_ACTIONS", {}).get("📊 Сегодня") == "today"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "days", "day_offset"),
    (("📊 Сегодня", 1, 0), ("📋 Вчера", 1, 1), ("📈 Неделя", 7, 0)),
)
async def test_report_menu_buttons_request_the_correct_period(label, days, day_offset):
    class TrackingService:
        def __init__(self):
            self.calls = []

        async def period(self, end, requested_days):
            self.calls.append((end, requested_days))
            return "готово"

    service = TrackingService()
    settings = _settings()
    app = build_application(settings, cast(Any, service))
    update = FakeUpdate(label)

    await _menu_callback(app)(cast(Any, update), type("Context", (), {"args": []})())

    expected_end = datetime.now(settings.timezone).date() - timedelta(days=day_offset)
    assert service.calls == [(expected_end, days)]


@pytest.mark.asyncio
async def test_loading_message_is_edited_when_telegram_supports_edit(service):
    class EditableStatus:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    class EditableMessage(FakeMessage):
        def __init__(self, text):
            super().__init__(text)
            self.status = EditableStatus()

        async def reply_text(self, text: str, **kwargs) -> Any:
            self.replies.append((text, kwargs))
            return self.status

    app = build_application(_settings(), service)
    update = FakeUpdate("/garmin")
    update.effective_message = EditableMessage("/garmin")

    await _command_callback(app, "garmin")(cast(Any, update), type("Context", (), {"args": []})())

    assert len(update.effective_message.replies) == 1
    assert update.effective_message.replies[0][0].startswith("⏳")
    assert "Garmin" in update.effective_message.status.edits[0][0]


@pytest.mark.asyncio
async def test_weekly_report_job_runs_on_monday_at_report_time():
    class TrackingService:
        def __init__(self):
            self.calls = []

        async def period(self, end, days):
            self.calls.append((end, days))
            return "weekly report"

    class Bot:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text, **kwargs):
            self.messages.append((chat_id, text, kwargs))

    service = TrackingService()
    settings = _settings()
    app = build_application(settings, cast(Any, service))
    assert app.job_queue is not None
    jobs = {job.name: job for job in app.job_queue.jobs()}

    assert "weekly-report" in jobs
    assert "day_of_week='mon'" in str(jobs["weekly-report"].job.trigger)

    bot = Bot()
    await jobs["weekly-report"].callback(SimpleNamespace(bot=bot))

    assert service.calls == [(datetime.now(settings.timezone).date(), 7)]
    assert bot.messages == [
        (settings.telegram_chat_id, "weekly report", {"parse_mode": "HTML"})
    ]
