from __future__ import annotations

import asyncio
import calendar
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from .models import DailyHealth, DailyNutrition
from .storage import SQLiteStorage


class SourceUnavailableError(RuntimeError):
    """A data source is unavailable without exposing its underlying error."""

    def __init__(self, source: str):
        self.source = source
        self.user_message = f"⚠️ {source} временно недоступен. Попробуйте позже."
        super().__init__(self.user_message)


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _number(value: Any, default: float = 0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    return None if value is None else _number(value)


class FatSecretAdapter:
    def __init__(self, client: Any, cache: SQLiteStorage, ttl_seconds: int = 3600):
        self.client, self.cache, self.ttl_seconds = client, cache, ttl_seconds

    async def get_day(self, day: date) -> DailyNutrition:
        key = f"fatsecret:{day.isoformat()}"
        cached = self.cache.cache_get(key)
        if cached:
            return DailyNutrition(day=day, **cached)
        diary = getattr(self.client, "diary", self.client)
        method = getattr(diary, "entries_get_v2", None) or getattr(diary, "entries_get", None)
        if method is None:
            raise RuntimeError("FatSecret SDK does not support diary entries")
        entries = await asyncio.to_thread(method, date=day)
        totals = {name: 0.0 for name in ("calories", "protein", "carbohydrate", "fat")}
        for entry in entries or []:
            for name in totals:
                totals[name] += _number(_value(entry, name))
        self.cache.cache_set(key, totals, self.ttl_seconds)
        return DailyNutrition(day=day, **totals)

    async def get_range(self, start: date, end: date) -> list[DailyNutrition]:
        if end < start:
            raise ValueError("end must not precede start")
        return [await self.get_day(start + timedelta(days=i)) for i in range((end - start).days + 1)]

    async def get_month(self, year: int, month: int, *, through: date | None = None) -> list[DailyNutrition]:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        if through is not None and through < end:
            end = through
        if end < start:
            return []

        diary = getattr(self.client, "diary", self.client)
        method = getattr(diary, "entries_get_month_v2", None) or getattr(diary, "entries_get_month_v1", None)
        if method is None:
            return await self.get_range(start, end)

        key = f"fatsecret:month:{year:04d}-{month:02d}"
        cached = self.cache.cache_get(key)
        if cached is None:
            records = await asyncio.to_thread(method, date=start)
            epoch = date(1970, 1, 1)
            days = []
            for record in records or []:
                day_number = _value(record, "date_int")
                if day_number is None:
                    continue
                record_day = epoch + timedelta(days=int(day_number))
                days.append(
                    {
                        "day": record_day.isoformat(),
                        "calories": _number(_value(record, "calories")),
                        "protein": _number(_value(record, "protein")),
                        "carbohydrate": _number(_value(record, "carbohydrate")),
                        "fat": _number(_value(record, "fat")),
                    }
                )
            cached = {"days": days}
            self.cache.cache_set(key, cached, min(self.ttl_seconds, 86_400))
        return [
            DailyNutrition(day=date.fromisoformat(item["day"]), **{k: v for k, v in item.items() if k != "day"})
            for item in cached.get("days", [])
            if start <= date.fromisoformat(item["day"]) <= end
        ]


class GarminAdapter:
    """Capability-detecting async wrapper around synchronous Garmin SDKs."""

    def __init__(self, client: Any, retries: int = 3, retry_delay: float = 0.2):
        self.client, self.retries, self.retry_delay = client, max(1, retries), retry_delay

    async def _call(self, names: tuple[str, ...], *args: Any, optional: bool = False) -> Any:
        method: Callable[..., Any] | None = next((getattr(self.client, n) for n in names if hasattr(self.client, n)), None)
        if method is None:
            if optional:
                return None
            raise RuntimeError(f"Garmin SDK lacks capability: {names[0]}")
        for attempt in range(self.retries):
            try:
                return await asyncio.to_thread(method, *args)
            except Exception:
                if attempt + 1 == self.retries:
                    if optional:
                        return None
                    raise
                await asyncio.sleep(self.retry_delay * (2**attempt))
        return None

    async def get_day(self, day: date) -> DailyHealth:
        iso = day.isoformat()
        stats = await self._call(("get_stats", "get_stats_and_body"), iso) or {}
        heart = await self._call(("get_heart_rates",), iso, optional=True) or {}
        sleep = await self._call(("get_sleep_data",), iso, optional=True) or {}
        hrv = await self._call(("get_hrv_data",), iso, optional=True) or {}
        body = await self._call(("get_body_battery",), iso, iso, optional=True)
        weight = await self._call(("get_body_composition", "get_weigh_ins"), iso, iso, optional=True) or {}
        activities = await self._call(("get_activities_by_date",), iso, iso, optional=True) or []
        sleep_seconds = _value(sleep, "sleepTimeSeconds", _value(_value(sleep, "dailySleepDTO", {}), "sleepTimeSeconds"))
        weight_summary = _value(weight, "totalAverage", {})
        weight_value = _value(weight_summary, "weight", _value(weight, "weight", _value(weight, "weightKg")))
        weight_kg = _optional_number(weight_value)
        if weight_kg is not None and weight_kg > 1_000:
            weight_kg /= 1_000
        hrv_summary = _value(hrv, "hrvSummary", {})
        hrv_value = _value(hrv_summary, "lastNightAvg", _value(hrv_summary, "weeklyAvg"))
        body_record = None
        if isinstance(body, list):
            body_record = next(
                (item for item in body if _value(item, "date", _value(item, "calendarDate")) == iso),
                None,
            )
        elif isinstance(body, dict):
            body_record = body
        return DailyHealth(
            day=day,
            steps=int(_number(_value(stats, "totalSteps"))),
            calories=_optional_number(_value(stats, "totalKilocalories")),
            active_calories=_optional_number(_value(stats, "activeKilocalories")),
            active_minutes=int(_number(_value(stats, "activeSeconds"))) // 60,
            distance_km=_number(_value(stats, "totalDistanceMeters")) / 1000,
            weight_kg=weight_kg,
            resting_hr=_optional_number(_value(heart, "restingHeartRate", _value(stats, "restingHeartRate"))),
            hrv=_optional_number(hrv_value),
            sleep_hours=_number(sleep_seconds) / 3600 if sleep_seconds is not None else None,
            stress=_optional_number(_value(stats, "averageStressLevel")),
            body_battery=_optional_number(
                _value(body_record, "charged") if body_record is not None else _value(stats, "bodyBatteryMostRecentValue")
            ),
            activities=len(activities) if isinstance(activities, list) else 0,
        )

    async def get_range(self, start: date, end: date) -> list[DailyHealth]:
        if end < start:
            raise ValueError("end must not precede start")
        rows = []
        for offset in range((end - start).days + 1):
            rows.append(await self.get_day(start + timedelta(days=offset)))
        return rows
