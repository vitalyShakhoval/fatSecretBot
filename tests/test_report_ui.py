from __future__ import annotations

from datetime import date

import pytest

from health_bot.bot import HealthBotService
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
