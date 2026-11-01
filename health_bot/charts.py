from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .models import DailyHealth, DailyNutrition


def _plot(title: str, series: list[tuple[str, list, list[float]]]) -> Path:
    fd, name = tempfile.mkstemp(prefix="health-bot-", suffix=".png")
    Path(name).unlink(missing_ok=True)
    try:
        import os
        os.close(fd)
        fig, ax = plt.subplots(figsize=(9, 4.5))
        for label, x, y in series:
            ax.plot(x, y, marker="o", label=label)
        ax.set_title(title)
        ax.grid(alpha=0.25)
        if len(series) > 1:
            ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(name, format="png", dpi=120)
        plt.close(fig)
        return Path(name)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def energy_chart(rows: list[DailyNutrition]) -> Path:
    x = [r.day.isoformat() for r in rows]
    return _plot("Energy and nutrition", [("Calories", x, [r.calories for r in rows]), ("Protein", x, [r.protein for r in rows]), ("Carbs", x, [r.carbohydrate for r in rows]), ("Fat", x, [r.fat for r in rows])])


def weight_chart(rows: list[DailyHealth]) -> Path:
    selected = [r for r in rows if r.weight_kg is not None]
    return _plot("Weight", [("kg", [r.day.isoformat() for r in selected], [float(r.weight_kg) for r in selected])])


def recovery_chart(rows: list[DailyHealth]) -> Path:
    series = []
    for label, attribute in (("HRV", "hrv"), ("Resting HR", "resting_hr"), ("Sleep", "sleep_hours")):
        selected = [(row.day.isoformat(), getattr(row, attribute)) for row in rows if getattr(row, attribute) is not None]
        if selected:
            series.append((label, [day for day, _ in selected], [float(value) for _, value in selected]))
    return _plot("Recovery observations", series)


def training_chart(rows: list[DailyHealth]) -> Path:
    x = [r.day.isoformat() for r in rows]
    return _plot("Training", [("Steps", x, [r.steps for r in rows]), ("Active minutes", x, [r.active_minutes for r in rows])])


@contextmanager
def chart_file(path: Path) -> Iterator[Path]:
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
