from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from .analytics import nutrition_summary, period_comparison, recovery_summary, training_summary, weight_trend
from .charts import chart_file, energy_chart, recovery_chart, training_chart, weight_chart
from .clients import FatSecretAdapter, GarminAdapter, SourceUnavailableError
from .config import Settings
from .storage import SQLiteStorage

logger = logging.getLogger(__name__)
OBSERVATION = "Все оценки — наблюдения по данным сервисов, а не медицинские диагнозы."
SET_REPORT_TIME = 1
MONTH_NAMES = (
    "",
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def _number(value: float | int | str, digits: int = 0) -> str:
    return f"{float(value):,.{digits}f}".replace(",", " ")


def _observation() -> str:
    return f"ℹ️ <i>{OBSERVATION}</i>"


def _change(value: float | None) -> str:
    if value is None:
        return "— нет данных"
    arrow = "↗️" if value > 0 else "↘️" if value < 0 else "➡️"
    return f"{arrow} {value:+.1f}%"


@dataclass(frozen=True)
class AccessPolicy:
    user_id: int
    chat_id: int

    def allows(self, update: Update | Any) -> bool:
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        return user is not None and chat is not None and user.id == self.user_id and chat.id == self.chat_id


def forbidden_garmin_setup_message() -> str:
    return "🔒 Пароль Garmin нельзя отправлять через Telegram. Настройте EMAIL и PASSWORD локально в .env и перезапустите бот."


class HealthBotService:
    def __init__(self, nutrition: FatSecretAdapter | Any, garmin: GarminAdapter | Any, storage: SQLiteStorage | None):
        self.nutrition, self.garmin, self.storage = nutrition, garmin, storage

    async def period(self, end: date, days: int) -> str:
        start = end - timedelta(days=days - 1)
        nutrition = await self.nutrition.get_range(start, end)
        health = await self.garmin.get_range(start, end)
        ns, ts = nutrition_summary(nutrition), training_summary(health)
        title = f"Сводка за день — {end:%d.%m.%Y}" if days == 1 else f"Сводка за {days} дней"
        date_line = "" if days == 1 else f"\n🗓 {start:%d.%m}–{end:%d.%m.%Y}"
        return (
            f"📊 <b>{title}</b>{date_line}\n\n"
            "🥗 <b>Питание</b>\n"
            f"🔥 Калории: {_number(ns['total_calories'])} ккал\n"
            f"🥩 Белки: {_number(ns['average_protein'])} г/день\n"
            f"🍞 Углеводы: {_number(ns['average_carbohydrate'])} г/день\n"
            f"🥑 Жиры: {_number(ns['average_fat'])} г/день\n"
            f"📝 Дней с данными: {ns['days_with_data']}/{days}\n\n"
            "🏃 <b>Активность</b>\n"
            f"👟 Шаги: {_number(ts['total_steps'])}\n"
            f"⏱️ Активные минуты: {ts['active_minutes']}\n"
            f"📏 Дистанция: {_number(ts['distance_km'], 1)} км\n\n"
            f"{_observation()}"
        )

    async def garmin_day(self, day: date) -> str:
        row = await self.garmin.get_day(day)
        lines = [
            f"⌚ <b>Garmin — {day:%d.%m.%Y}</b>",
            "",
            f"👟 Шаги: {_number(row.steps)}",
            f"📏 Дистанция: {_number(row.distance_km, 1)} км",
            f"⏱️ Активные минуты: {row.active_minutes}",
        ]
        if row.calories is not None:
            lines.append(f"🔥 Калории: {_number(row.calories)} ккал")
        if row.active_calories is not None:
            lines.append(f"⚡ Активные калории: {_number(row.active_calories)} ккал")
        if row.resting_hr is not None:
            lines.append(f"💓 Пульс в покое: {_number(row.resting_hr)} уд/мин")
        lines.extend(("", _observation()))
        return "\n".join(lines)

    async def food_day(self, day: date) -> str:
        row = await self.nutrition.get_day(day)
        return (
            f"🥗 <b>Питание — {day:%d.%m.%Y}</b>\n\n"
            f"🔥 Калории: {_number(row.calories)} ккал\n"
            f"🥩 Белки: {_number(row.protein)} г\n"
            f"🍞 Углеводы: {_number(row.carbohydrate)} г\n"
            f"🥑 Жиры: {_number(row.fat)} г"
        )

    async def month(self, today: date) -> str:
        start = today.replace(day=1)
        nutrition = await self.nutrition.get_month(today.year, today.month, through=today)
        health = await self.garmin.get_range(start, today)
        ns, ts = nutrition_summary(nutrition), training_summary(health)
        previous_end = start - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        elapsed_end = min(previous_start + timedelta(days=today.day - 1), previous_end)
        previous_nutrition = await self.nutrition.get_month(
            previous_start.year,
            previous_start.month,
            through=elapsed_end,
        )
        previous_health = await self.garmin.get_range(previous_start, elapsed_end)
        previous_ns = nutrition_summary(previous_nutrition)
        previous_ts = training_summary(previous_health)
        calorie_change = period_comparison(
            float(ns["total_calories"]), float(previous_ns["total_calories"])
        )["percent_change"]
        step_change = period_comparison(float(ts["total_steps"]), float(previous_ts["total_steps"]))[
            "percent_change"
        ]

        return (
            f"📅 <b>Итоги за {MONTH_NAMES[today.month]} {today.year}</b>\n"
            f"🗓 Дней: {today.day}\n\n"
            "🥗 <b>Питание</b>\n"
            f"🔥 Калории: {_number(ns['total_calories'])} ккал\n"
            f"📊 В среднем: {_number(ns['average_calories'])} ккал/день\n"
            f"🥩 Белки: {_number(ns['average_protein'])} г/день\n"
            f"🍞 Углеводы: {_number(ns['average_carbohydrate'])} г/день\n"
            f"🥑 Жиры: {_number(ns['average_fat'])} г/день\n"
            f"📝 Дней с данными: {ns['days_with_data']}/{today.day}\n\n"
            "🏃 <b>Активность</b>\n"
            f"👟 Шаги: {_number(ts['total_steps'])}\n"
            f"📊 В среднем: {_number(ts['average_steps'])} шагов/день\n"
            f"⏱️ Активные минуты: {ts['active_minutes']}\n"
            f"📏 Дистанция: {_number(ts['distance_km'], 1)} км\n\n"
            "📈 <b>К предыдущему периоду</b>\n"
            f"🔥 Калории: {_change(calorie_change)}\n"
            f"👟 Шаги: {_change(step_change)}\n\n"
            f"{_observation()}"
        )

    async def weight(self, today: date, days: int = 30) -> tuple[str, list]:
        rows = await self.garmin.get_range(today - timedelta(days=days - 1), today)
        trend = weight_trend(rows)
        if trend["end_kg"] is None:
            return "⚖️ Нет данных о весе за период.", []
        return (
            f"⚖️ <b>Вес за {days} дней</b>\n"
            f"🏁 Начало: {trend['start_kg']:.1f} кг\n"
            f"🎯 Сейчас: {trend['end_kg']:.1f} кг\n"
            f"📈 Изменение: {trend['change_kg']:+.1f} кг\n"
            f"〰️ Среднее за 7 измерений: {trend['seven_day_average']:.1f} кг\n\n"
            f"{_observation()}"
        ), rows

    async def recovery(self, today: date, days: int = 28) -> tuple[str, list]:
        rows = await self.garmin.get_range(today - timedelta(days=days - 1), today)
        baseline = recovery_summary(rows)["baseline"]
        def show(value: float | None, suffix: str = "") -> str:
            return "нет данных" if value is None else f"{value:.1f}{suffix}"
        text = (
            f"💚 <b>Восстановление за {days} дней</b>\n\n"
            f"💗 HRV: {show(baseline['hrv'], ' мс')}\n"
            f"💓 Пульс в покое: {show(baseline['resting_hr'], ' уд/мин')}\n"
            f"😴 Сон: {show(baseline['sleep_hours'], ' ч')}\n"
            f"🧠 Стресс: {show(baseline['stress'])}\n"
            f"🔋 Body Battery: {show(baseline['body_battery'])}\n\n"
            f"{_observation()}"
        )
        chart_rows = [row for row in rows if any(value is not None for value in (row.hrv, row.resting_hr, row.sleep_hours))]
        return text, chart_rows

    async def training(self, today: date, days: int = 7) -> tuple[str, list]:
        rows = await self.garmin.get_range(today - timedelta(days=days - 1), today)
        summary = training_summary(rows)
        return (
            f"🏋️ <b>Тренировки за {days} дней</b>\n\n"
            f"🎯 Активности: {summary['activities']}\n"
            f"⏱️ Активные минуты: {summary['active_minutes']}\n"
            f"👟 Шаги: {_number(summary['total_steps'])}\n"
            f"📊 В среднем: {_number(summary['average_steps'])} шагов/день\n"
            f"📏 Дистанция: {_number(summary['distance_km'], 1)} км\n\n"
            f"{_observation()}"
        ), rows

    def goals(self, args: list[str]) -> str:
        if self.storage is None:
            return "Хранилище целей недоступно."
        if not args or args[0] == "list":
            goals = self.storage.list_goals()
            return "🎯 Цели отсутствуют." if not goals else "🎯 <b>Цели</b>\n" + "\n".join(f"#{g.id} {html.escape(g.metric)}: {g.target:g} ({g.period}) {'✅' if g.active else '⏸'}" for g in goals)
        try:
            action = args[0].lower()
            if action == "add" and len(args) == 4:
                goal = self.storage.create_goal(args[1], float(args[2]), args[3])
                return f"✅ Цель #{goal.id} добавлена."
            if action == "delete" and len(args) == 2:
                return "✅ Цель удалена." if self.storage.delete_goal(int(args[1])) else "Цель не найдена."
            if action in {"pause", "resume"} and len(args) == 2:
                self.storage.update_goal(int(args[1]), active=action == "resume")
                return "✅ Цель обновлена."
            if action == "set" and len(args) == 3:
                self.storage.update_goal(int(args[1]), target=float(args[2]))
                return "✅ Цель обновлена."
        except (ValueError, KeyError):
            return "Некорректная цель или идентификатор."
        return "Использование: /goals [list|add METRIC TARGET daily|weekly|monthly|set ID TARGET|pause ID|resume ID|delete ID]"

    def set_report_time(self, args: list[str]) -> str:
        if self.storage is None or len(args) != 1:
            return "Формат: /settime ЧЧ:ММ"
        try:
            hour_text, minute_text = args[0].split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            return "Формат: /settime ЧЧ:ММ (часы 0–23, минуты 0–59)"
        value = f"{hour:02d}:{minute:02d}"
        self.storage.set_setting("report_time", value)
        return f"✅ Время отчёта сохранено: {value}. Перезапустите бот для обновления активной задачи."


def _authorized(policy: AccessPolicy, handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if not policy.allows(update):
            logger.warning("Rejected Telegram update from unauthorized identifiers")
            if update.effective_message:
                await update.effective_message.reply_text("⛔ Доступ запрещён.")
            return None
        return await handler(update, context)
    return wrapped


def build_application(settings: Settings, service: HealthBotService) -> Application:
    policy = AccessPolicy(settings.telegram_user_id, settings.telegram_chat_id)
    app = Application.builder().token(settings.telegram_bot_token).build()

    async def reply(update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(text, parse_mode="HTML")

    async def application_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        logger.error(
            "Command handler failed error_type=%s update_type=%s",
            type(error).__name__,
            type(update).__name__,
        )
        if not policy.allows(update):
            return
        message = getattr(update, "effective_message", None)
        if message is None:
            return
        response = (
            error.user_message
            if isinstance(error, SourceUnavailableError)
            else "⚠️ Не удалось выполнить команду. Попробуйте позже."
        )
        await message.reply_text(response)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, "Health Bot\n/today /report /week /month /weight /recovery /training /goals /garmin /food")

    async def setupgarmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, forbidden_garmin_setup_message())

    async def authfat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, "🔒 FatSecret OAuth настраивается локально через переменные окружения. Токены никогда не отправляются в чат.")

    async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        today = datetime.now(settings.timezone).date()
        await reply(update, await service.month(today))
        rows = await service.nutrition.get_month(today.year, today.month, through=today)
        if rows and update.effective_message:
            path = energy_chart(rows)
            with chart_file(path):
                with path.open("rb") as image:
                    await update.effective_message.reply_photo(image)

    async def _chart_command(update: Update, producer: Callable, chart: Callable) -> None:
        text, rows = await producer(datetime.now(settings.timezone).date())
        await reply(update, text)
        if rows and update.effective_message:
            path = chart(rows)
            with chart_file(path):
                with path.open("rb") as image:
                    await update.effective_message.reply_photo(image)

    async def weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _chart_command(update, service.weight, weight_chart)

    async def recovery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _chart_command(update, service.recovery, recovery_chart)

    async def training(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _chart_command(update, service.training, training_chart)

    async def goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, service.goals(context.args or []))

    async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        args = context.args or []
        if args:
            await reply(update, service.set_report_time(args))
            return ConversationHandler.END
        await reply(update, "Введите время ежедневного отчёта в формате ЧЧ:ММ или /cancel.")
        return SET_REPORT_TIME

    async def settime_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        value = (update.effective_message.text or "").strip() if update.effective_message else ""
        response = service.set_report_time([value])
        await reply(update, response)
        return SET_REPORT_TIME if response.startswith("Формат:") else ConversationHandler.END

    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await reply(update, "Настройка времени отменена.")
        return ConversationHandler.END

    async def garmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await service.garmin_day(datetime.now(settings.timezone).date()))

    async def food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await service.food_day(datetime.now(settings.timezone).date()))

    async def legacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        command = (update.effective_message.text or "").split()[0].lstrip("/").split("@")[0] if update.effective_message else ""
        days = 7 if command in {"week"} else 1
        end = datetime.now(settings.timezone).date()
        if command in {"report", "sendreport"}:
            end -= timedelta(days=1)
        await reply(update, await service.period(end, days))

    handlers = {
        "start": start, "help": start, "setupgarmin": setupgarmin, "authfat": authfat,
        "month": month, "weight": weight, "recovery": recovery, "training": training, "goals": goals,
        "today": legacy, "report": legacy, "week": legacy, "sendreport": legacy,
        "garmin": garmin, "food": food,
    }
    for command, handler in handlers.items():
        app.add_handler(CommandHandler(command, _authorized(policy, handler)))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("settime", _authorized(policy, settime))],
            states={
                SET_REPORT_TIME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, _authorized(policy, settime_received))
                ]
            },
            fallbacks=[CommandHandler("cancel", _authorized(policy, cancel))],
        )
    )
    app.add_error_handler(application_error_handler)

    if app.job_queue is not None:
        local_time = time(settings.report_hour, settings.report_minute, tzinfo=settings.timezone)

        async def scheduled_report(context: ContextTypes.DEFAULT_TYPE) -> None:
            end = datetime.now(settings.timezone).date() - timedelta(days=1)
            text = await service.period(end, 1)
            await context.bot.send_message(settings.telegram_chat_id, text, parse_mode="HTML")

        app.job_queue.run_daily(
            scheduled_report,
            local_time,
            chat_id=settings.telegram_chat_id,
            name="daily-report",
        )
    return app
