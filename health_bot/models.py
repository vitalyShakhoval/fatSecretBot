from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyNutrition:
    day: date
    calories: float = 0
    protein: float = 0
    carbohydrate: float = 0
    fat: float = 0


@dataclass(frozen=True)
class DailyHealth:
    day: date
    steps: int = 0
    calories: float | None = None
    active_calories: float | None = None
    active_minutes: int = 0
    distance_km: float = 0
    weight_kg: float | None = None
    resting_hr: float | None = None
    hrv: float | None = None
    sleep_hours: float | None = None
    stress: float | None = None
    body_battery: float | None = None
    activities: int = 0


@dataclass(frozen=True)
class Goal:
    id: int
    metric: str
    target: float
    period: str
    active: bool = True
