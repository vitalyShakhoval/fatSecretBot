from __future__ import annotations

from collections.abc import Iterable
from statistics import mean

from .models import DailyHealth, DailyNutrition


def _average(values: Iterable[float | int | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return mean(present) if present else None


def nutrition_summary(rows: list[DailyNutrition]) -> dict[str, float | int]:
    days = [row for row in rows if row.calories > 0]
    divisor = len(days) or 1
    return {
        "days_with_data": len(days),
        "total_calories": sum(row.calories for row in rows),
        "average_calories": sum(row.calories for row in rows) / divisor,
        "average_protein": sum(row.protein for row in rows) / divisor,
        "average_carbohydrate": sum(row.carbohydrate for row in rows) / divisor,
        "average_fat": sum(row.fat for row in rows) / divisor,
    }


def period_comparison(current: float, previous: float) -> dict[str, float | None]:
    difference = current - previous
    return {"difference": difference, "percent_change": difference / previous * 100 if previous else None}


def weight_trend(rows: list[DailyHealth]) -> dict[str, float | str | None]:
    points = [(r.day, r.weight_kg) for r in rows if r.weight_kg is not None]
    if not points:
        return {"start_kg": None, "end_kg": None, "change_kg": None, "seven_day_average": None, "direction": "no data"}
    points.sort()
    start, end = float(points[0][1]), float(points[-1][1])
    change = end - start
    return {
        "start_kg": start,
        "end_kg": end,
        "change_kg": change,
        "seven_day_average": mean(float(v) for _, v in points[-7:]),
        "direction": "up" if change > 0 else "down" if change < 0 else "stable",
    }


def recovery_summary(rows: list[DailyHealth]) -> dict:
    return {
        "baseline": {
            "hrv": _average(r.hrv for r in rows),
            "resting_hr": _average(r.resting_hr for r in rows),
            "sleep_hours": _average(r.sleep_hours for r in rows),
            "stress": _average(r.stress for r in rows),
            "body_battery": _average(r.body_battery for r in rows),
        },
        "latest": rows[-1] if rows else None,
        "disclaimer": "Это наблюдение по данным трекера, а не медицинский диагноз.",
    }


def training_summary(rows: list[DailyHealth]) -> dict[str, float | int | str]:
    count = len(rows) or 1
    return {
        "days": len(rows),
        "total_steps": sum(r.steps for r in rows),
        "average_steps": sum(r.steps for r in rows) / count,
        "active_minutes": sum(r.active_minutes for r in rows),
        "distance_km": sum(r.distance_km for r in rows),
        "activities": sum(r.activities for r in rows),
        "disclaimer": "Оценка описывает наблюдения по данным трекера, а не состояние здоровья.",
    }
