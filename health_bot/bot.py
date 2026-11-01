from __future__ import annotations

import html
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import wraps
from typing import Any, cast

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import TelegramError
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
MENU_ACTIONS = {
    "📊 Сегодня": "today",
    "📋 Вчера": "report",
    "📈 Неделя": "week",
    "📅 Месяц": "month",
    "⚖️ Вес": "weight",
    "💚 Восстановление": "recovery",
    "🏋️ Тренировки": "training",
    "🎯 Цели": "goals",
    "⌚ Garmin": "garmin",
    "🥗 Питание": "food",
}


def _number(value: float | int | str, digits: int = 0) -> str:
    return f"{float(value):,.{digits}f}".replace(",", " ")


def _observation() -> str:
    return f"ℹ️ <i>{OBSERVATION}</i>"


def _change(value: float | None) -> str:
    if value is None:
        return "— нет данных"
    arrow = "↗️" if value > 0 else "↘️" if value < 0 else "➡️"
    return f"{arrow} {value:+.1f}%"


def _days_word(value: int) -> str:
    if value % 10 == 1 and value % 100 != 11:
        return "день"
    if value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
        return "дня"
    return "дней"


def _balance_section(
    consumed: float,
    burned: float,
    nutrition_days: int,
    burn_days: int,
    expected_days: int,
) -> str:
    lines = ["⚖️ <b>Сальдо калорий</b>"]
    if nutrition_days < expected_days or burn_days < expected_days:
        lines.extend(
            (
                "⚠️ Недостаточно данных для сальдо за весь период",
                f"📝 Питание: {nutrition_days}/{expected_days} дней",
                f"🔥 Расход: {burn_days}/{expected_days} дней",
            )
        )
        return "\n".join(lines)
    lines.extend(
        (
            f"🍽 Получено: {_number(consumed)} ккал",
            f"🔥 Потрачено: {_number(burned)} ккал",
        )
    )
    balance = consumed - burned
    if balance > 0:
        lines.append(f"🔺 Профицит: {_number(balance)} ккал")
    elif balance < 0:
        lines.append(f"🔻 Дефицит: {_number(abs(balance))} ккал")
    else:
        lines.append("✅ Баланс: 0 ккал")
    return "\n".join(lines)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    rows = (
        (KeyboardButton("📊 Сегодня"), KeyboardButton("📋 Вчера"), KeyboardButton("📈 Неделя")),
        (KeyboardButton("📅 Месяц"), KeyboardButton("⚖️ Вес")),
        (KeyboardButton("💚 Восстановление"), KeyboardButton("🏋️ Тренировки")),
        (KeyboardButton("🎯 Цели"), KeyboardButton("⌚ Garmin"), KeyboardButton("🥗 Питание")),
    )
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


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
        expected_dates = {start + timedelta(days=offset) for offset in range(days)}
        nutrition_by_day = {row.day: row for row in nutrition if row.day in expected_dates and row.calories > 0}
        health_by_day = {
            row.day: row
            for row in health
            if row.day in expected_dates and row.calories is not None and row.calories > 0
        }
        consumed = sum(float(row.calories) for row in nutrition_by_day.values())
        burned = sum(float(row.calories or 0) for row in health_by_day.values())
        title = (
            f"Сводка за день — {end:%d.%m.%Y}"
            if days == 1
            else f"Сводка за {days} {_days_word(days)}"
        )
        date_line = "" if days == 1 else f"\n🗓 {start:%d.%m}–{end:%d.%m.%Y}"
        return (
            f"📊 <b>{title}</b>{date_line}\n\n"
            "🥗 <b>Питание</b>\n"
            f"🔥 Калории: {_number(ns['total_calories'])} ккал\n"
            f"🥩 Белки: {_number(ns['average_protein'])} г/день\n"
            f"🥑 Жиры: {_number(ns['average_fat'])} г/день\n"
            f"🍞 Углеводы: {_number(ns['average_carbohydrate'])} г/день\n"
            f"📝 Дней с данными: {ns['days_with_data']}/{days}\n\n"
            "🏃 <b>Активность</b>\n"
            f"👟 Шаги: {_number(ts['total_steps'])}\n"
            f"⏱️ Активные минуты: {ts['active_minutes']}\n"
            f"📏 Дистанция: {_number(ts['distance_km'], 1)} км\n\n"
            f"{_balance_section(consumed, burned, len(nutrition_by_day), len(health_by_day), days)}\n\n"
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
            f"🥑 Жиры: {_number(row.fat)} г\n"
            f"🍞 Углеводы: {_number(row.carbohydrate)} г"
        )

    async def month(self, today: date) -> str:
        start = today.replace(day=1)
        nutrition = await self.nutrition.get_month(today.year, today.month, through=today)
        health = await self.garmin.get_range(start, today)
        ns, ts = nutrition_summary(nutrition), training_summary(health)
        expected_dates = {start + timedelta(days=offset) for offset in range(today.day)}
        nutrition_by_day = {row.day: row for row in nutrition if row.day in expected_dates and row.calories > 0}
        health_by_day = {
            row.day: row
            for row in health
            if row.day in expected_dates and row.calories is not None and row.calories > 0
        }
        consumed = sum(float(row.calories) for row in nutrition_by_day.values())
        burned = sum(float(row.calories or 0) for row in health_by_day.values())
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
            f"🥑 Жиры: {_number(ns['average_fat'])} г/день\n"
            f"🍞 Углеводы: {_number(ns['average_carbohydrate'])} г/день\n"
            f"📝 Дней с данными: {ns['days_with_data']}/{today.day}\n\n"
            "🏃 <b>Активность</b>\n"
            f"👟 Шаги: {_number(ts['total_steps'])}\n"
            f"📊 В среднем: {_number(ts['average_steps'])} шагов/день\n"
            f"⏱️ Активные минуты: {ts['active_minutes']}\n"
            f"📏 Дистанция: {_number(ts['distance_km'], 1)} км\n\n"
            f"{_balance_section(consumed, burned, len(nutrition_by_day), len(health_by_day), today.day)}\n\n"
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

    async def reply(update: Update, text: str, **kwargs: Any) -> Any:
        if update.effective_message:
            return await update.effective_message.reply_text(text, parse_mode="HTML", **kwargs)
        return None

    async def loading(update: Update, label: str) -> Any:
        return await reply(update, f"⏳ <b>{label}</b>\nПолучаю данные…")

    async def finish_loading(update: Update, status: Any, text: str) -> None:
        try:
            await status.edit_text(text, parse_mode="HTML")
        except (AttributeError, TelegramError):
            await reply(update, text)

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
        await reply(
            update,
            "👋 <b>Health Bot</b>\n"
            "Ваши данные Garmin и FatSecret в одном месте.\n\n"
            "📊 <b>Отчёты</b>\n"
            "• Сегодня, вчера, неделя и месяц\n"
            "• Вес, восстановление и тренировки\n\n"
            "⌚ <b>Источники</b>\n"
            "• Garmin — активность и восстановление\n"
            "• FatSecret — питание и БЖУ\n\n"
            "Выберите раздел на клавиатуре 👇",
            reply_markup=main_menu_keyboard(),
        )

    async def setupgarmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, forbidden_garmin_setup_message())

    async def authfat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, "🔒 FatSecret OAuth настраивается локально через переменные окружения. Токены никогда не отправляются в чат.")

    async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        today = datetime.now(settings.timezone).date()
        status = await loading(update, "Формирую отчёт за месяц")
        await finish_loading(update, status, await service.month(today))
        rows = await service.nutrition.get_month(today.year, today.month, through=today)
        if rows and update.effective_message:
            path = energy_chart(rows)
            with chart_file(path):
                with path.open("rb") as image:
                    await update.effective_message.reply_photo(image)

    async def _chart_command(update: Update, label: str, producer: Callable, chart: Callable) -> None:
        status = await loading(update, label)
        text, rows = await producer(datetime.now(settings.timezone).date())
        await finish_loading(update, status, text)
        if rows and update.effective_message:
            path = chart(rows)
            with chart_file(path):
                with path.open("rb") as image:
                    await update.effective_message.reply_photo(image)

    async def weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _chart_command(update, "Загружаю динамику веса", service.weight, weight_chart)

    async def recovery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _chart_command(update, "Анализирую восстановление", service.recovery, recovery_chart)

    async def training(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _chart_command(update, "Собираю данные тренировок", service.training, training_chart)

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
        status = await loading(update, "Загружаю Garmin")
        text = await service.garmin_day(datetime.now(settings.timezone).date())
        await finish_loading(update, status, text)

    async def food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        status = await loading(update, "Загружаю питание")
        text = await service.food_day(datetime.now(settings.timezone).date())
        await finish_loading(update, status, text)

    async def send_period(update: Update, command: str) -> None:
        days = 7 if command == "week" else 1
        end = datetime.now(settings.timezone).date()
        if command in {"report", "sendreport"}:
            end -= timedelta(days=1)
        labels = {
            "week": "Формирую отчёт за неделю",
            "report": "Загружаю отчёт за вчера",
            "sendreport": "Загружаю отчёт за вчера",
        }
        status = await loading(update, labels.get(command, "Загружаю данные за сегодня"))
        await finish_loading(update, status, await service.period(end, days))

    async def legacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        command = (
            (update.effective_message.text or "").split()[0].lstrip("/").split("@")[0]
            if update.effective_message
            else ""
        )
        await send_period(update, command)

    handlers = {
        "start": start, "help": start, "setupgarmin": setupgarmin, "authfat": authfat,
        "month": month, "weight": weight, "recovery": recovery, "training": training, "goals": goals,
        "today": legacy, "report": legacy, "week": legacy, "sendreport": legacy,
        "garmin": garmin, "food": food,
    }
    for command, handler in handlers.items():
        app.add_handler(CommandHandler(command, _authorized(policy, handler)))

    async def menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        label = (update.effective_message.text or "") if update.effective_message else ""
        action = MENU_ACTIONS.get(label)
        if action in {"today", "report", "week"}:
            await send_period(update, action)
        elif action:
            await handlers[action](update, context)

    menu_pattern = "^(?:" + "|".join(re.escape(label) for label in MENU_ACTIONS) + ")$"
    app.add_handler(MessageHandler(filters.Regex(menu_pattern), cast(Any, _authorized(policy, menu_action))))
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

        async def scheduled_weekly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
            end = datetime.now(settings.timezone).date()
            text = await service.period(end, 7)
            await context.bot.send_message(settings.telegram_chat_id, text, parse_mode="HTML")

        app.job_queue.run_daily(
            scheduled_report,
            local_time,
            chat_id=settings.telegram_chat_id,
            name="daily-report",
        )
        app.job_queue.run_daily(
            scheduled_weekly_report,
            local_time,
            days=(1,),
            chat_id=settings.telegram_chat_id,
            name="weekly-report",
        )
    return app
