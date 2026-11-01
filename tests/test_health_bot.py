from __future__ import annotations

import asyncio
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from health_bot.analytics import (
    nutrition_summary,
    period_comparison,
    recovery_summary,
    training_summary,
    weight_trend,
)
from health_bot.bot import AccessPolicy, HealthBotService, build_application, forbidden_garmin_setup_message
from health_bot.charts import chart_file, energy_chart, recovery_chart, training_chart, weight_chart
from health_bot.clients import FatSecretAdapter, GarminAdapter
from health_bot.config import ConfigError, Settings
from health_bot.models import DailyHealth, DailyNutrition
from health_bot.storage import SQLiteStorage


def test_config_requires_bot_and_allowed_ids():
    with pytest.raises(ConfigError):
        Settings.from_mapping({})


def test_config_uses_iana_timezone_and_validates_cache_ttl():
    settings = Settings.from_mapping({
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_USER_ID": "10",
        "TELEGRAM_CHAT_ID": "20",
        "TIMEZONE": "Europe/Moscow",
        "CACHE_TTL_SECONDS": "3600",
    })
    assert settings.telegram_user_id == 10
    assert settings.timezone.key == "Europe/Moscow"
    with pytest.raises(ConfigError):
        Settings.from_mapping({
            "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_USER_ID": "1",
            "TELEGRAM_CHAT_ID": "2", "CACHE_TTL_SECONDS": "86401",
        })


def test_access_policy_requires_both_user_and_chat():
    policy = AccessPolicy(user_id=10, chat_id=20)
    assert policy.allows(SimpleNamespace(effective_user=SimpleNamespace(id=10), effective_chat=SimpleNamespace(id=20)))
    assert not policy.allows(SimpleNamespace(effective_user=SimpleNamespace(id=11), effective_chat=SimpleNamespace(id=20)))
    assert not policy.allows(SimpleNamespace(effective_user=SimpleNamespace(id=10), effective_chat=SimpleNamespace(id=21)))


def test_garmin_password_is_never_requested_in_telegram():
    text = forbidden_garmin_setup_message()
    assert "парол" in text.lower()
    assert ".env" in text


def test_existing_and_new_commands_are_registered():
    settings = Settings.from_mapping(
        {"TELEGRAM_BOT_TOKEN": "123:TEST", "TELEGRAM_USER_ID": "10", "TELEGRAM_CHAT_ID": "20"}
    )
    app = build_application(settings, HealthBotService(None, None, None))
    commands = set()
    for handlers in app.handlers.values():
        for handler in handlers:
            commands.update(getattr(handler, "commands", ()))
            for entry_point in getattr(handler, "entry_points", ()):  # ConversationHandler commands
                commands.update(getattr(entry_point, "commands", ()))
    assert {
        "start", "help", "today", "report", "week", "sendreport", "garmin", "food", "authfat",
        "setupgarmin", "settime", "month", "weight", "recovery", "training", "goals",
    } <= commands


def test_storage_schema_and_goal_crud_do_not_store_secrets(tmp_path):
    db = tmp_path / "health.db"
    storage = SQLiteStorage(db)
    storage.initialize()
    goal = storage.create_goal("steps", 10000, "daily")
    assert storage.list_goals() == [goal]
    updated = storage.update_goal(goal.id, target=12000, active=False)
    assert updated.target == 12000 and not updated.active
    assert storage.delete_goal(goal.id)
    storage.set_setting("report_time", "12:00")
    assert storage.get_setting("report_time") == "12:00"
    storage.set_allowed_identifiers(10, 20)
    assert storage.get_allowed_identifiers() == (10, 20)
    storage.set_sync_state("garmin", "2026-01-01")
    assert storage.get_sync_state("garmin") == "2026-01-01"
    with pytest.raises(ValueError):
        storage.set_setting("oauth_token", "forbidden")
    columns = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(settings)")}
    assert not {"password", "token", "secret"} & columns


def test_ttl_cache_expires_and_cleans_up(tmp_path):
    storage = SQLiteStorage(tmp_path / "health.db", now=lambda: 1_000.0)
    storage.initialize()
    storage.cache_set("fatsecret:2026-01-01", {"calories": 1}, ttl_seconds=60)
    assert storage.cache_get("fatsecret:2026-01-01") == {"calories": 1}
    storage.now = lambda: 1_061.0
    assert storage.cache_get("fatsecret:2026-01-01") is None
    assert storage.cleanup_cache() == 0


def test_cache_rejects_ttl_over_24_hours(tmp_path):
    storage = SQLiteStorage(tmp_path / "health.db")
    storage.initialize()
    with pytest.raises(ValueError):
        storage.cache_set("x", {}, ttl_seconds=86401)


class Entry:
    def __init__(self, calories=100, protein=10, carbohydrate=20, fat=3):
        self.calories, self.protein, self.carbohydrate, self.fat = calories, protein, carbohydrate, fat


class Diary:
    def entries_get_v2(self, date):
        return [Entry()]


@pytest.mark.asyncio
async def test_fatsecret_adapter_daily_monthly_and_cache(tmp_path):
    class Client:
        diary = Diary()
    storage = SQLiteStorage(tmp_path / "db.sqlite", now=lambda: 10.0)
    storage.initialize()
    adapter = FatSecretAdapter(Client(), storage, ttl_seconds=100)
    daily = await adapter.get_day(date(2026, 2, 1))
    month = await adapter.get_month(2026, 2, through=date(2026, 2, 2))
    assert daily.calories == 100
    assert [x.day for x in month] == [date(2026, 2, 1), date(2026, 2, 2)]


@pytest.mark.asyncio
async def test_fatsecret_calls_sync_sdk_off_event_loop(tmp_path, monkeypatch):
    called = False
    original = asyncio.to_thread
    async def tracked(func, *args, **kwargs):
        nonlocal called
        called = True
        return await original(func, *args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", tracked)
    storage = SQLiteStorage(tmp_path / "db.sqlite")
    storage.initialize()
    await FatSecretAdapter(SimpleNamespace(diary=Diary()), storage).get_day(date(2026, 1, 1))
    assert called


@pytest.mark.asyncio
async def test_garmin_range_retries_and_tolerates_missing_metrics(monkeypatch):
    attempts = 0
    class Client:
        def get_stats(self, day):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary")
            return {"totalSteps": 5000}
    original_sleep = asyncio.sleep
    monkeypatch.setattr("health_bot.clients.asyncio.sleep", lambda _: original_sleep(0))
    rows = await GarminAdapter(Client(), retries=2).get_range(date(2026, 1, 1), date(2026, 1, 1))
    assert attempts == 2
    assert rows[0].steps == 5000
    assert rows[0].sleep_hours is None


def test_nutrition_month_summary_and_period_comparison():
    rows = [DailyNutrition(date(2026, 1, i), 1000 * i, 10, 20, 30) for i in (1, 2)]
    summary = nutrition_summary(rows)
    assert summary["total_calories"] == 3000
    assert summary["average_calories"] == 1500
    assert period_comparison(100, 80)["percent_change"] == 25


def test_weight_trend_and_seven_day_average():
    rows = [DailyHealth(day=date(2026, 1, i), weight_kg=70 + i) for i in range(1, 9)]
    result = weight_trend(rows)
    assert result["change_kg"] == 7
    assert result["seven_day_average"] == 75
    assert result["direction"] == "up"


def test_recovery_uses_baseline_and_labels_observation():
    rows = [DailyHealth(date(2026, 1, i), resting_hr=60, hrv=50, sleep_hours=8, stress=20, body_battery=70) for i in range(1, 8)]
    result = recovery_summary(rows)
    assert result["baseline"]["hrv"] == 50
    assert "наблюден" in result["disclaimer"].lower()


def test_training_summary_totals_and_averages():
    rows = [DailyHealth(date(2026, 1, 1), steps=1000, active_minutes=30, distance_km=2, activities=1), DailyHealth(date(2026, 1, 2), steps=3000, active_minutes=60, distance_km=4, activities=2)]
    result = training_summary(rows)
    assert result["total_steps"] == 4000
    assert result["average_steps"] == 2000
    assert result["activities"] == 3


@pytest.mark.parametrize("maker,rows", [
    (energy_chart, [DailyNutrition(date(2026, 1, 1), 1000, 80, 100, 40)]),
    (weight_chart, [DailyHealth(date(2026, 1, 1), weight_kg=70)]),
    (recovery_chart, [DailyHealth(date(2026, 1, 1), hrv=50, resting_hr=60)]),
    (training_chart, [DailyHealth(date(2026, 1, 1), steps=5000, active_minutes=30)]),
])
def test_charts_are_png_and_context_removes_temporary_file(maker, rows):
    path = maker(rows)
    assert path.exists() and path.read_bytes().startswith(b"\x89PNG")
    with chart_file(path) as current:
        assert current.exists()
    assert not path.exists()


@pytest.mark.asyncio
async def test_service_month_combines_api_data_and_comparison():
    nutrition = SimpleNamespace(get_month=lambda *a, **k: asyncio.sleep(0, result=[DailyNutrition(date(2026, 2, 1), 2000, 100, 200, 70)]))
    garmin = SimpleNamespace(get_range=lambda *a, **k: asyncio.sleep(0, result=[DailyHealth(date(2026, 2, 1), steps=8000)]))
    service = HealthBotService(nutrition, garmin, None)
    result = await service.month(date(2026, 2, 15))
    assert "2 000" in result
    assert "8 000" in result
    assert "предыдущ" in result.lower()
    assert "наблюдения" in result


@pytest.mark.asyncio
async def test_legacy_period_summary_remains_meaningful():
    nutrition = SimpleNamespace(get_range=lambda *a, **k: asyncio.sleep(0, result=[DailyNutrition(date(2026, 2, 1), 2000, 100, 200, 70)]))
    garmin = SimpleNamespace(get_range=lambda *a, **k: asyncio.sleep(0, result=[DailyHealth(date(2026, 2, 1), steps=8000, active_minutes=30)]))
    text = await HealthBotService(nutrition, garmin, None).period(date(2026, 2, 1), 1)
    assert "2 000" in text and "8 000" in text


def test_report_time_is_validated_and_persisted(tmp_path):
    storage = SQLiteStorage(tmp_path / "db.sqlite")
    storage.initialize()
    service = HealthBotService(None, None, storage)
    assert "сохранено" in service.set_report_time(["08:30"]).lower()
    assert storage.get_setting("report_time") == "08:30"
    assert "формат" in service.set_report_time(["25:00"]).lower()
