#!/usr/bin/env python3
"""
Telegram бот для отображения данных Garmin и FatSecret

Функционал:
- Шаги, калории, сон, пульс из Garmin Connect
- Дневник питания из FatSecret
- OAuth авторизация FatSecret через Telegram
"""

import os
import sys
import logging
from datetime import date, timedelta, datetime, time
from pathlib import Path
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

# Импорт библиотек
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
except ImportError:
    logger.error("python-telegram-bot не установлен. Установите: pip install python-telegram-bot")
    sys.exit(1)

try:
    from garminconnect import Garmin
except ImportError:
    logger.error("garminconnect не установлен. Установите: pip install garminconnect")
    sys.exit(1)

try:
    from fatsecret import Fatsecret
except ImportError:
    logger.error("fatsecret не установлен. Установите: pip install fatsecret")
    sys.exit(1)

# Конфигурация
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
GARMIN_EMAIL = os.getenv('EMAIL', '')
GARMIN_PASSWORD = os.getenv('PASSWORD', '')
GARMINTOKENS = os.getenv('GARMINTOKENS', '~/.garminconnect')

FATSECRET_CONSUMER_KEY = os.getenv('FATSECRET_CONSUMER_KEY', '')
FATSECRET_CONSUMER_SECRET = os.getenv('FATSECRET_CONSUMER_SECRET', '')
FATSECRET_OAUTH_TOKEN = os.getenv('FATSECRET_OAUTH_TOKEN', '')
FATSECRET_OAUTH_TOKEN_SECRET = os.getenv('FATSECRET_OAUTH_TOKEN_SECRET', '')

# Состояния для OAuth авторизации
(OAUTH_WAITING_FOR_PIN,) = range(1)

# Состояния для настройки Garmin
(GARMIN_SETUP_EMAIL, GARMIN_SETUP_PASSWORD, GARMIN_SETUP_MFA) = range(10, 13)

# Состояния для настройки времени отчёта
(SET_REPORT_HOUR, SET_REPORT_MINUTE) = range(20, 22)


class HealthBot:
    """Класс бота для получения данных о здоровье."""
    
    def __init__(self):
        self.garmin_client = None
        self.fatsecret_client = None
        self.garmin_logged_in = False
        self.fatsecret_logged_in = False
        self.fatsecret_fs = None  # FatSecret instance for OAuth
    
    async def init_garmin(self):
        """Инициализация подключения к Garmin."""
        if not GARMIN_EMAIL or not GARMIN_PASSWORD:
            return False, "Garmin: EMAIL и PASSWORD не настроены в .env"
        
        try:
            logger.info("Подключение к Garmin Connect...")
            self.garmin_client = Garmin(
                GARMIN_EMAIL, 
                GARMIN_PASSWORD,
                prompt_mfa=lambda: None  # Отключаем автоматический запрос - обработаем вручную
            )
            self.garmin_client.login(GARMINTOKENS)
            self.garmin_logged_in = True
            logger.info("Garmin: Успешно подключено")
            return True, "Подключено к Garmin Connect"
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Garmin ошибка: {error_msg}")
            
            # Проверяем на ошибку MFA
            if "MFA" in error_msg or "Two-Factor" in error_msg or "mfa" in error_msg.lower():
                return False, "⚠️ Требуется MFA код!\n\nДля авторизации Garmin:\n1. Выйдите из текущего бота\n2. Запустите garmin_app.py в консоли\n3. Введите MFA код\n4. После этого бот будет работать"
            
            # Проверяем на ошибку социального профиля
            if "social profile" in error_msg.lower() or "Failed to retrieve" in error_msg:
                return False, "⚠️ Ошибка авторизации Garmin!\n\nВозможные причины:\n1. Неверный EMAIL или PASSWORD\n2. Требуется ручной вход\n3. Сессия истекла\n\nПопробуйте: /garmin_fix для диагностики"
            
            return False, f"Ошибка подключения к Garmin: {error_msg[:100]}"
    
    async def init_fatsecret(self):
        """Инициализация подключения к FatSecret."""
        if not FATSECRET_CONSUMER_KEY or not FATSECRET_CONSUMER_SECRET:
            return False, "FatSecret: ключи не настроены"
        
        try:
            logger.info("Подключение к FatSecret...")
            
            # Проверяем, есть ли токены
            if FATSECRET_OAUTH_TOKEN and FATSECRET_OAUTH_TOKEN_SECRET:
                self.fatsecret_client = Fatsecret(
                    FATSECRET_CONSUMER_KEY,
                    FATSECRET_CONSUMER_SECRET,
                    session_token=(FATSECRET_OAUTH_TOKEN, FATSECRET_OAUTH_TOKEN_SECRET)
                )
            else:
                self.fatsecret_client = Fatsecret(
                    FATSECRET_CONSUMER_KEY,
                    FATSECRET_CONSUMER_SECRET
                )
            
            self.fatsecret_logged_in = True
            logger.info("FatSecret: Успешно подключено")
            return True, "Подключено к FatSecret"
        except Exception as e:
            logger.error(f"FatSecret ошибка: {e}")
            return False, f"Ошибка подключения к FatSecret: {str(e)[:100]}"
    
    def start_fatsecret_oauth(self):
        """Начало OAuth авторизации FatSecret."""
        try:
            self.fatsecret_fs = Fatsecret(
                FATSECRET_CONSUMER_KEY,
                FATSECRET_CONSUMER_SECRET
            )
            auth_url = self.fatsecret_fs.get_authorize_url()
            return auth_url
        except Exception as e:
            logger.error(f"FatSecret OAuth error: {e}")
            return None
    
    def complete_fatsecret_oauth(self, pin: str) -> tuple:
        """Завершение OAuth авторизации."""
        try:
            access_token, access_secret = self.fatsecret_fs.authenticate(pin)
            return True, access_token, access_secret
        except Exception as e:
            logger.error(f"FatSecret OAuth complete error: {e}")
            return False, None, None
    
    def save_oauth_tokens(self, token: str, secret: str):
        """Сохранение OAuth токенов в .env файл."""
        try:
            env_file = Path(__file__).parent / '.env'
            content = env_file.read_text(encoding='utf-8')
            
            lines = content.split('\n')
            new_lines = []
            token_updated = False
            secret_updated = False
            
            for line in lines:
                if line.startswith('FATSECRET_OAUTH_TOKEN='):
                    new_lines.append(f'FATSECRET_OAUTH_TOKEN={token}')
                    token_updated = True
                elif line.startswith('FATSECRET_OAUTH_TOKEN_SECRET='):
                    new_lines.append(f'FATSECRET_OAUTH_TOKEN_SECRET={secret}')
                    secret_updated = True
                else:
                    new_lines.append(line)
            
            if not token_updated:
                new_lines.append(f'FATSECRET_OAUTH_TOKEN={token}')
            if not secret_updated:
                new_lines.append(f'FATSECRET_OAUTH_TOKEN_SECRET={secret}')
            
            env_file.write_text('\n'.join(new_lines), encoding='utf-8')
            logger.info("OAuth tokens saved to .env")
            return True
        except Exception as e:
            logger.error(f"Error saving tokens: {e}")
            return False
    
    async def get_garmin_today(self):
        """Получение данных Garmin за сегодня."""
        if not self.garmin_logged_in or not self.garmin_client:
            return "❌ Garmin не подключен"
        
        try:
            today = date.today().isoformat()
            
            # Получаем статистику
            stats = self.garmin_client.get_stats(today)
            if not stats:
                return "❌ Нет данных о активности за сегодня"
            
            # Основные метрики
            steps = stats.get('totalSteps', 0)
            distance = stats.get('totalDistanceMeters', 0) / 1000
            calories = stats.get('totalKilocalories', 0)
            active_calories = stats.get('activeKilocalories', 0)
            active_minutes = (stats.get('activeSeconds', 0) // 60)
            floors = stats.get('floorsAscended', 0)
            
            # Формируем сообщение
            message = "📊 <b>Garmin - сегодня</b>\n\n"
            message += f"👟 Шаги: {steps:,}\n".replace(",", " ")
            message += f"📏 Дистанция: {distance:.2f} км\n"
            message += f"🔥 Калории (всего): {int(calories):,}\n".replace(",", " ")
            message += f"🔥 Калории (активные): {int(active_calories):,}\n".replace(",", " ")
            message += f"⏱️ Активные минуты: {active_minutes}\n"
            message += f"🏢 Этажи: {floors}\n"
            
            # Пульс
            try:
                hr_data = self.garmin_client.get_heart_rates(today)
                if hr_data:
                    resting_hr = hr_data.get('restingHeartRate')
                    if resting_hr:
                        message += f"💓 Пульс в покое: {resting_hr} уд/мин\n"
            except:
                pass
            
            return message
            
        except Exception as e:
            logger.error(f"Garmin данные ошибка: {e}")
            return f"❌ Ошибка получения данных Garmin: {str(e)[:100]}"
    
    async def get_fatsecret_today(self):
        """Получение данных питания из FatSecret за сегодня."""
        if not self.fatsecret_logged_in or not self.fatsecret_client:
            return "❌ FatSecret не подключен\nИспользуйте /authfat для авторизации"
        
        try:
            from datetime import date as date_module
            today = date_module.today()
            
            # Получаем записи дневника
            entries = self.fatsecret_client.diary.entries_get_v2(date=today)
            
            if not entries:
                return "📝 <b>FatSecret - сегодня</b>\n\nЗаписей питания за сегодня нет"
            
            # Группируем по приемам пищи
            meals = {}
            totals = {'calories': 0, 'protein': 0, 'carbohydrate': 0, 'fat': 0}
            
            for entry in entries:
                meal = entry.meal
                if meal not in meals:
                    meals[meal] = []
                
                food_name = entry.food_entry_name
                calories = float(entry.calories) if entry.calories else 0
                protein = float(entry.protein) if entry.protein else 0
                carbs = float(entry.carbohydrate) if entry.carbohydrate else 0
                fat = float(entry.fat) if entry.fat else 0
                
                meals[meal].append({
                    'name': food_name,
                    'calories': calories,
                    'protein': protein,
                    'carbs': carbs,
                    'fat': fat
                })
                
                totals['calories'] += calories
                totals['protein'] += protein
                totals['carbohydrate'] += carbs
                totals['fat'] += fat
            
            # Формируем сообщение
            message = "🥗 <b>FatSecret - сегодня</b>\n\n"
            
            meal_names = {
                'Breakfast': '🌅 Завтрак',
                'Lunch': '☀️ Обед',
                'Dinner': '🌙 Ужин',
                'Other': '🍿 Перекус'
            }
            
            for meal, foods in meals.items():
                meal_name = meal_names.get(meal, meal)
                message += f"\n{meal_name}:\n"
                
                meal_cal = 0
                for food in foods:
                    message += f"  • {food['name']} ({int(food['calories'])} ккал)\n"
                    meal_cal += food['calories']
                
                message += f"  <i>Итого: {int(meal_cal)} ккал</i>\n"
            
            # Итоги
            message += f"\n📊 <b>Всего за день:</b>\n"
            message += f"  Калории: {int(totals['calories'])} ккал\n"
            message += f"  Белки: {int(totals['protein'])}г\n"
            message += f"  Углеводы: {int(totals['carbohydrate'])}г\n"
            message += f"  Жиры: {int(totals['fat'])}г\n"
            
            return message
            
        except Exception as e:
            logger.error(f"FatSecret данные ошибка: {e}")
            return f"❌ Ошибка получения данных FatSecret: {str(e)[:100]}"
    
    async def get_full_today(self):
        """Полная сводка за сегодня."""
        garmin_data = await self.get_garmin_today()
        fatsecret_data = await self.get_fatsecret_today()
        
        return f"{garmin_data}\n\n{fatsecret_data}"
    
    async def get_garmin_yesterday(self):
        """Получение данных Garmin за вчера."""
        if not self.garmin_logged_in or not self.garmin_client:
            return None
        
        try:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            
            # Получаем статистику
            stats = self.garmin_client.get_stats(yesterday)
            if not stats:
                return None
            
            return {
                'steps': stats.get('totalSteps', 0),
                'distance': stats.get('totalDistanceMeters', 0) / 1000,
                'calories': stats.get('totalKilocalories', 0),
                'active_calories': stats.get('activeKilocalories', 0),
                'active_minutes': (stats.get('activeSeconds', 0) // 60),
                'floors': stats.get('floorsAscended', 0),
                'date': yesterday
            }
            
        except Exception as e:
            logger.error(f"Garmin yesterday ошибка: {e}")
            return None
    
    async def get_fatsecret_yesterday(self):
        """Получение данных питания из FatSecret за вчера."""
        if not self.fatsecret_logged_in or not self.fatsecret_client:
            return None
        
        try:
            from datetime import date as date_module
            yesterday = date_module.today() - timedelta(days=1)
            
            # Получаем записи дневника
            entries = self.fatsecret_client.diary.entries_get_v2(date=yesterday)
            
            if not entries:
                return None
            
            # Считаем итоги
            totals = {'calories': 0, 'protein': 0, 'carbohydrate': 0, 'fat': 0}
            
            for entry in entries:
                totals['calories'] += float(entry.calories) if entry.calories else 0
                totals['protein'] += float(entry.protein) if entry.protein else 0
                totals['carbohydrate'] += float(entry.carbohydrate) if entry.carbohydrate else 0
                totals['fat'] += float(entry.fat) if entry.fat else 0
            
            return {
                'calories': totals['calories'],
                'protein': totals['protein'],
                'carbohydrate': totals['carbohydrate'],
                'fat': totals['fat'],
                'date': yesterday.isoformat()
            }
            
        except Exception as e:
            logger.error(f"FatSecret yesterday ошибка: {e}")
            return None
    
    async def get_daily_report(self):
        """Получение ежедневного отчёта за вчера."""
        garmin = await self.get_garmin_yesterday()
        fatsecret = await self.get_fatsecret_yesterday()
        
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        
        message = f"📋 <b>Отчёт за {yesterday}</b>\n\n"
        
        # Данные питания (FatSecret)
        if fatsecret:
            message += "🥗 <b>Питание (получено):</b>\n"
            message += f"  Калории: {int(fatsecret['calories'])} ккал\n"
            message += f"  Белки: {int(fatsecret['protein'])}г\n"
            message += f"  Углеводы: {int(fatsecret['carbohydrate'])}г\n"
            message += f"  Жиры: {int(fatsecret['fat'])}г\n"
        else:
            message += "🥗 <b>Питание:</b>\n  Нет данных\n"
        
        message += "\n"
        
        # Данные активности (Garmin)
        if garmin:
            total_calories = garmin.get('calories', 0)
            active_calories = garmin.get('active_calories', 0)
            message += "🏃 <b>Активность (затрачено):</b>\n"
            message += f"  Калории (всего): {int(total_calories)} ккал\n"
            message += f"  Калории (активные): {int(active_calories)} ккал\n"
            message += f"  Шаги: {garmin['steps']:,}\n".replace(",", " ")
            message += f"  Активные минуты: {garmin['active_minutes']}\n"
            message += f"  Дистанция: {garmin['distance']:.2f} км\n"
        else:
            message += "🏃 <b>Активность:</b>\n  Нет данных\n"
        
        message += "\n"
        
        # Баланс калорий
        if fatsecret and garmin:
            consumed = fatsecret['calories']
            burned = garmin.get('calories', garmin.get('active_calories', 0))
            balance = consumed - burned
            
            message += "📊 <b>Баланс калорий:</b>\n"
            message += f"  Получено: {int(consumed)} ккал\n"
            message += f"  Сожжено: {int(burned)} ккал\n"
            
            if balance > 0:
                message += f"  🔺 Избыток: {int(balance)} ккал\n"
            elif balance < 0:
                message += f"  🔻 Дефицит: {int(abs(balance))} ккал\n"
            else:
                message += "  ⚖️ Баланс: 0 ккал\n"
        
        return message
    
    async def get_garmin_week(self):
        """Получение данных Garmin за последние 7 дней."""
        if not self.garmin_logged_in or not self.garmin_client:
            return []
        
        try:
            week_data = []
            for i in range(6, -1, -1):
                day = (date.today() - timedelta(days=i)).isoformat()
                try:
                    stats = self.garmin_client.get_stats(day)
                    
                    if stats:
                        week_data.append({
                            'date': day,
                            'steps': stats.get('totalSteps', 0),
                            'calories': stats.get('totalKilocalories', 0),
                            'active_calories': stats.get('activeKilocalories', 0),
                            'active_minutes': (stats.get('activeSeconds', 0) // 60),
                            'distance': stats.get('totalDistanceMeters', 0) / 1000,
                        })
                except Exception as e:
                    logger.error(f"Ошибка получения данных за {day}: {e}")
                    pass
            return week_data
        except Exception as e:
            logger.error(f"Garmin week ошибка: {e}")
            return []
    
    async def get_fatsecret_week(self):
        """Получение данных питания из FatSecret за последние 7 дней."""
        if not self.fatsecret_logged_in or not self.fatsecret_client:
            return []
        
        try:
            from datetime import date as date_module
            week_data = []
            
            for i in range(6, -1, -1):
                day = date_module.today() - timedelta(days=i)
                try:
                    entries = self.fatsecret_client.diary.entries_get_v2(date=day)
                    
                    totals = {'calories': 0, 'protein': 0, 'carbohydrate': 0, 'fat': 0}
                    if entries:
                        for entry in entries:
                            totals['calories'] += float(entry.calories) if entry.calories else 0
                            totals['protein'] += float(entry.protein) if entry.protein else 0
                            totals['carbohydrate'] += float(entry.carbohydrate) if entry.carbohydrate else 0
                            totals['fat'] += float(entry.fat) if entry.fat else 0
                    
                    week_data.append({
                        'date': day.isoformat(),
                        'calories': totals['calories'],
                        'protein': totals['protein'],
                        'carbohydrate': totals['carbohydrate'],
                        'fat': totals['fat'],
                    })
                except:
                    pass
            
            return week_data
        except Exception as e:
            logger.error(f"FatSecret week ошибка: {e}")
            return []
    
    async def get_weekly_report(self):
        """Получение еженедельного отчёта."""
        garmin_week = await self.get_garmin_week()
        fatsecret_week = await self.get_fatsecret_week()
        
        message = "📊 <b>Аналитика за неделю</b>\n\n"
        
        # Статистика питания
        if fatsecret_week:
            total_cal = sum(d['calories'] for d in fatsecret_week)
            total_protein = sum(d['protein'] for d in fatsecret_week)
            total_carbs = sum(d['carbohydrate'] for d in fatsecret_week)
            total_fat = sum(d['fat'] for d in fatsecret_week)
            days_with_data = len([d for d in fatsecret_week if d['calories'] > 0])
            
            if days_with_data > 0:
                message += "🥗 <b>Питание (среднее в день):</b>\n"
                message += f"  Калории: {int(total_cal / days_with_data)} ккал\n"
                message += f"  Белки: {int(total_protein / days_with_data)}г\n"
                message += f"  Углеводы: {int(total_carbs / days_with_data)}г\n"
                message += f"  Жиры: {int(total_fat / days_with_data)}г\n"
                message += f"  Дней с данными: {days_with_data}/7\n"
            else:
                message += "🥗 <b>Питание:</b>\n  Нет данных\n"
        else:
            message += "🥗 <b>Питание:</b>\n  Нет данных\n"
        
        message += "\n"
        
        # Статистика активности
        if garmin_week:
            total_steps = sum(d['steps'] for d in garmin_week)
            total_cal = sum(d['calories'] for d in garmin_week)
            total_active_cal = sum(d['active_calories'] for d in garmin_week)
            total_minutes = sum(d['active_minutes'] for d in garmin_week)
            total_distance = sum(d['distance'] for d in garmin_week)
            
            message += "🏃 <b>Активность (среднее в день):</b>\n"
            message += f"  Шаги: {int(total_steps / 7):,}\n".replace(",", " ")
            message += f"  Калории (всего): {int(total_cal / 7):,}\n".replace(",", " ")
            message += f"  Калории (активные): {int(total_active_cal / 7):,}\n".replace(",", " ")
            message += f"  Активные минуты: {int(total_minutes / 7)}\n"
            message += f"  Дистанция: {total_distance / 7:.2f} км\n"
            
            # Лучший день
            best_day = max(garmin_week, key=lambda x: x['steps'])
            message += f"\n🏆 <b>Лучший день:</b> {best_day['date'][-5:]}\n"
            message += f"  Шаги: {best_day['steps']:,}\n".replace(",", " ")
        else:
            message += "🏃 <b>Активность:</b>\n  Нет данных\n"
        
        message += "\n"
        
        # Общий баланс за неделю
        if fatsecret_week and garmin_week:
            total_consumed = sum(d['calories'] for d in fatsecret_week)
            total_burned = sum(d['calories'] for d in garmin_week)
            week_balance = total_consumed - total_burned
            
            message += "📈 <b>Баланс за неделю:</b>\n"
            message += f"  Получено: {int(total_consumed):,} ккал\n".replace(",", " ")
            message += f"  Сожжено: {int(total_burned):,} ккал\n".replace(",", " ")
            
            if week_balance > 0:
                message += f"  🔺 Избыток: {int(week_balance):,} ккал\n".replace(",", " ")
            elif week_balance < 0:
                message += f"  🔻 Дефицит: {int(abs(week_balance)):,} ккал\n".replace(",", " ")
            else:
                message += "  ⚖️ Баланс: 0 ккал\n"
        
        return message


# Инициализация бота
bot = HealthBot()


def get_main_menu_keyboard():
    """Создание главного меню с кнопками"""
    from telegram import KeyboardButton, ReplyKeyboardMarkup
    
    # Кнопки отчётов
    report_buttons = [
        KeyboardButton("📊 Сегодня"),
        KeyboardButton("📋 Вчера"),
        KeyboardButton("📈 Неделя"),
    ]
    
    # Кнопка настроек
    settings_button = [KeyboardButton("⚙️ Настройка")]
    
    keyboard = [
        report_buttons,
        settings_button,
    ]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_settings_keyboard(current_hour: int = 12, current_minute: int = 0, timezone_offset: int = 0):
    """Создание меню настроек"""
    from telegram import KeyboardButton, ReplyKeyboardMarkup
    
    # Форматируем текущее время
    time_str = f"{current_hour:02d}:{current_minute:02d}"
    
    # Форматируем часовой пояс
    if timezone_offset >= 0:
        tz_str = f"UTC+{timezone_offset}"
    else:
        tz_str = f"UTC{timezone_offset}"
    
    settings_buttons = [
        KeyboardButton("🔐 FatSecret Auth"),
        KeyboardButton("⚙️ Garmin Setup"),
    ]
    
    time_buttons = [
        KeyboardButton(f"⏰ Время отчёта: {time_str}"),
        KeyboardButton(f"🌍 Часовой пояс: {tz_str}"),
    ]
    
    back_button = [KeyboardButton("🔙 Назад")]
    
    keyboard = [
        settings_buttons,
        time_buttons,
        back_button,
    ]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


# Глобальная переменная для хранения времени отчёта (UTC)
REPORT_HOUR = int(os.getenv('REPORT_HOUR', '12'))
REPORT_MINUTE = int(os.getenv('REPORT_MINUTE', '0'))

# Сдвиг часового пояса пользователя относительно UTC (в часах)
# Например, для Москвы (UTC+3) -> TIMEZONE_OFFSET = 3
TIMEZONE_OFFSET = int(os.getenv('TIMEZONE_OFFSET', '0'))

# Глобальная переменная для job queue и scheduled job
job_queue = None
daily_report_job = None
target_chat_id = None  # Глобальная переменная для chat_id


async def send_daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Отправка ежедневного отчёта по расписанию"""
    logger.info("Запуск автоматического отчёта за вчера...")
    
    # Используем chat_id из контекста задачи или из .env
    chat_id = context.job.chat_id if context.job.chat_id else TELEGRAM_CHAT_ID
    logger.info(f"Отправка в chat_id: {chat_id}")
    
    # Инициализируем подключения если нужно
    if not bot.garmin_logged_in:
        success, msg = await bot.init_garmin()
        if not success:
            logger.error(f"Garmin: {msg}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Garmin: {msg}"
            )
            return
    
    if not bot.fatsecret_logged_in:
        success, msg = await bot.init_fatsecret()
        if not success:
            logger.error(f"FatSecret: {msg}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ FatSecret: {msg}"
            )
            return
    
    # Получаем отчёт
    report = await bot.get_daily_report()
    await context.bot.send_message(
        chat_id=chat_id,
        text=report,
        parse_mode='HTML'
    )
    logger.info("Автоматический отчёт отправлен")


def schedule_daily_report(jq, chat_id):
    """Создание/обновление расписания ежедневного отчёта"""
    global daily_report_job
    
    # Удаляем старую задачу если есть
    if daily_report_job is not None:
        try:
            daily_report_job.remove()
            logger.info("Удалена старая задача отчёта")
        except:
            pass
    
    # Вычисляем время в UTC
    utc_hour = (REPORT_HOUR - TIMEZONE_OFFSET) % 24
    utc_minute = REPORT_MINUTE
    
    # Создаём новую задачу
    daily_report_job = jq.run_daily(
        send_daily_report_job, 
        time=time(hour=utc_hour, minute=utc_minute), 
        chat_id=chat_id
    )
    
    tz_display = f"UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}"
    logger.info(f"Расписание обновлено: {REPORT_HOUR:02d}:{REPORT_MINUTE:02d} ({tz_display}) -> UTC: {utc_hour:02d}:{utc_minute:02d}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "👋 <b>Health Bot</b> - ваш помощник для отслеживания здоровья\n\n"
        "📊 <b>Отчёты:</b>\n"
        "• /today - Данные за сегодня\n"
        "• /report - Отчёт за вчера\n"
        "• /week - Аналитика за неделю\n\n"
        "🔐 <b>Настройка:</b>\n"
        "• /authfat - Авторизация FatSecret\n"
        "• /setupgarmin - Настройка Garmin",
        parse_mode='HTML',
        reply_markup=get_main_menu_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    await update.message.reply_text(
        "📖 <b>Справка</b>\n\n"
        "Этот бот показывает данные из:\n"
        "• <b>Garmin Connect</b> - шаги, калории, пульс\n"
        "• <b>FatSecret</b> - дневник питания\n\n"
        "Команды:\n"
        "/today - Сводка за сегодня\n"
        "/report - Отчёт за вчера\n"
        "/week - Аналитика за неделю\n"
        "/sendreport - Отправить отчёт сейчас\n"
        "/authfat - Авторизация FatSecret\n"
        "/setupgarmin - Настройка Garmin\n"
        "/help - Эта справка",
        parse_mode='HTML'
    )


async def authfat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /authfat - начало OAuth авторизации"""
    await update.message.reply_text("⏳ Начинаю авторизацию FatSecret...")
    
    # Начинаем OAuth процесс
    auth_url = bot.start_fatsecret_oauth()
    
    if auth_url:
        keyboard = [
            [InlineKeyboardButton("Открыть ссылку", url=auth_url)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "1. Нажмите на кнопку ниже и авторизуйтесь в FatSecret\n"
            "2. После авторизации получите PIN код\n"
            "3. Отправьте PIN код в этот чат",
            reply_markup=reply_markup
        )
        
        # Сообщение с PIN
        await update.message.reply_text(
            "🔐 <b>После авторизации отправьте PIN код сюда</b>",
            parse_mode='HTML'
        )
        
        return OAUTH_WAITING_FOR_PIN
    else:
        await update.message.reply_text("❌ Ошибка создания URL авторизации")
        return ConversationHandler.END
    
    return ConversationHandler.END


async def oauth_pin_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик получения PIN кода от пользователя"""
    pin = update.message.text.strip()
    
    await update.message.reply_text("⏳ Проверяю PIN код...")
    
    success, token, secret = bot.complete_fatsecret_oauth(pin)
    
    if success and token and secret:
        # Сохраняем токены
        if bot.save_oauth_tokens(token, secret):
            await update.message.reply_text("✅ Авторизация успешна! Токены сохранены.")
            
            # Переинициализируем FatSecret с новыми токенами
            global FATSECRET_OAUTH_TOKEN, FATSECRET_OAUTH_TOKEN_SECRET
            FATSECRET_OAUTH_TOKEN = token
            FATSECRET_OAUTH_TOKEN_SECRET = secret
            
            success, msg = await bot.init_fatsecret()
            if success:
                await update.message.reply_text(f"✅ {msg}")
            else:
                await update.message.reply_text(f"⚠️ {msg}")
        else:
            await update.message.reply_text("⚠️ Не удалось сохранить токены. Добавьте вручную:")
            await update.message.reply_text(f"Token: {token}\nSecret: {secret}")
    else:
        await update.message.reply_text("❌ Неверный PIN код. Попробуйте /authfat снова")
    
    return ConversationHandler.END


async def cancel_oauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена OAuth авторизации"""
    await update.message.reply_text("❌ Авторизация отменена")
    return ConversationHandler.END


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /today - полная сводка"""
    await update.message.reply_text("⏳ Загружаю данные...")
    
    # Инициализируем подключения если нужно
    if not bot.garmin_logged_in:
        success, msg = await bot.init_garmin()
        if not success:
            await update.message.reply_text(f"⚠️ {msg}")
    
    if not bot.fatsecret_logged_in:
        success, msg = await bot.init_fatsecret()
        if not success:
            await update.message.reply_text(f"⚠️ {msg}")
    
    # Получаем данные
    data = await bot.get_full_today()
    await update.message.reply_text(data, parse_mode='HTML')


async def garmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /garmin - только данные Garmin"""
    await update.message.reply_text("⏳ Загружаю данные Garmin...")
    
    if not bot.garmin_logged_in:
        success, msg = await bot.init_garmin()
        if not success:
            await update.message.reply_text(f"⚠️ {msg}")
            return
    
    data = await bot.get_garmin_today()
    await update.message.reply_text(data, parse_mode='HTML')


async def food_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /food - только дневник питания"""
    await update.message.reply_text("⏳ Загружаю данные питания...")
    
    if not bot.fatsecret_logged_in:
        success, msg = await bot.init_fatsecret()
        if not success:
            await update.message.reply_text(f"⚠️ {msg}")
            return
    
    data = await bot.get_fatsecret_today()
    await update.message.reply_text(data, parse_mode='HTML')


# Garmin настройка через Telegram
garmin_setup_data = {}  # Временное хранилище для данных настройки

async def setupgarmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /setupgarmin - настройка Garmin через Telegram"""
    await update.message.reply_text(
        "⚙️ <b>Настройка Garmin</b>\n\n"
        "Введите ваш EMAIL от Garmin Connect:",
        parse_mode='HTML'
    )
    return GARMIN_SETUP_EMAIL


async def garmin_setup_email_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение email от пользователя"""
    email = update.message.text.strip()
    garmin_setup_data['email'] = email
    
    await update.message.reply_text(
        f"✅ Email сохранён: {email}\n\n"
        "Введите ваш PASSWORD от Garmin Connect:"
    )
    return GARMIN_SETUP_PASSWORD


async def garmin_setup_password_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение пароля от пользователя и попытка входа"""
    password = update.message.text.strip()
    garmin_setup_data['password'] = password
    
    await update.message.reply_text("⏳ Пробую подключиться к Garmin...")
    
    try:
        # Пробуем подключиться
        client = Garmin(
            garmin_setup_data['email'],
            password,
            prompt_mfa=None
        )
        client.login(GARMINTOKENS)
        
        # Успех! Сохраняем в .env
        env_file = Path(__file__).parent / '.env'
        content = env_file.read_text(encoding='utf-8')
        
        lines = content.split('\n')
        new_lines = []
        email_updated = False
        password_updated = False
        
        for line in lines:
            if line.startswith('EMAIL='):
                new_lines.append(f"EMAIL={garmin_setup_data['email']}")
                email_updated = True
            elif line.startswith('PASSWORD='):
                new_lines.append(f"PASSWORD={password}")
                password_updated = True
            else:
                new_lines.append(line)
        
        if not email_updated:
            new_lines.append(f"EMAIL={garmin_setup_data['email']}")
        if not password_updated:
            new_lines.append(f"PASSWORD={password}")
        
        env_file.write_text('\n'.join(new_lines), encoding='utf-8')
        
        # Обновляем глобальные переменные
        global GARMIN_EMAIL, GARMIN_PASSWORD
        GARMIN_EMAIL = garmin_setup_data['email']
        GARMIN_PASSWORD = password
        
        # Инициализируем бота
        bot.garmin_client = client
        bot.garmin_logged_in = True
        
        await update.message.reply_text(
            "✅ <b>Garmin настроен успешно!</b>\n\n"
            "Данные сохранены в .env\n"
            "Теперь используйте /garmin для получения данных",
            parse_mode='HTML'
        )
        
        # Очищаем временные данные
        garmin_setup_data.clear()
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Garmin setup error: {error_msg}")
        
        # Проверяем на MFA
        if "MFA" in error_msg or "Two-Factor" in error_msg or "mfa" in error_msg.lower():
            await update.message.reply_text(
                "🔐 <b>Требуется MFA код!</b>\n\n"
                "Введите код двухфакторной аутентификации:",
                parse_mode='HTML'
            )
            return GARMIN_SETUP_MFA
        
        await update.message.reply_text(
            f"❌ Ошибка подключения: {error_msg[:100]}\n\n"
            "Попробуйте /setupgarmin снова"
        )
    
    return ConversationHandler.END


async def garmin_setup_mfa_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение MFA кода и завершение авторизации"""
    mfa_code = update.message.text.strip()
    
    await update.message.reply_text("⏳ Проверяю MFA код...")
    
    try:
        # Создаём клиент с MFA
        client = Garmin(
            garmin_setup_data['email'],
            garmin_setup_data['password'],
            prompt_mfa=lambda: mfa_code
        )
        client.login(GARMINTOKENS)
        
        # Сохраняем в .env
        env_file = Path(__file__).parent / '.env'
        content = env_file.read_text(encoding='utf-8')
        
        lines = content.split('\n')
        new_lines = []
        
        for line in lines:
            if line.startswith('EMAIL='):
                new_lines.append(f"EMAIL={garmin_setup_data['email']}")
            elif line.startswith('PASSWORD='):
                new_lines.append(f"PASSWORD={garmin_setup_data['password']}")
            else:
                new_lines.append(line)
        
        if f"EMAIL={garmin_setup_data['email']}" not in '\n'.join(lines):
            new_lines.append(f"EMAIL={garmin_setup_data['email']}")
        if f"PASSWORD={garmin_setup_data['password']}" not in '\n'.join(lines):
            new_lines.append(f"PASSWORD={garmin_setup_data['password']}")
        
        env_file.write_text('\n'.join(new_lines), encoding='utf-8')
        
        # Обновляем глобальные переменные
        global GARMIN_EMAIL, GARMIN_PASSWORD
        GARMIN_EMAIL = garmin_setup_data['email']
        GARMIN_PASSWORD = garmin_setup_data['password']
        
        # Инициализируем бота
        bot.garmin_client = client
        bot.garmin_logged_in = True
        
        await update.message.reply_text(
            "✅ <b>Garmin настроен успешно с MFA!</b>",
            parse_mode='HTML'
        )
        
        garmin_setup_data.clear()
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка: {str(e)[:100]}\n\n"
            "Попробуйте /setupgarmin снова"
        )
    
    return ConversationHandler.END


async def cancel_garmin_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена настройки Garmin"""
    garmin_setup_data.clear()
    await update.message.reply_text("❌ Настройка Garmin отменена")
    return ConversationHandler.END


# Временное хранилище для настройки времени
report_time_data = {'hour': None}


async def set_report_time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало настройки времени отчёта"""
    await update.message.reply_text(
        f"⏰ <b>Настройка времени отчёта</b>\n\n"
        f"Текущее время: {REPORT_HOUR:02d}:{REPORT_MINUTE:02d}\n\n"
        "Введите время в формате ЧЧ:ММ (например 12:00 или 08:30):",
        parse_mode='HTML'
    )
    return SET_REPORT_HOUR


async def report_time_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение времени и сохранение"""
    time_text = update.message.text.strip()
    
    # Пробуем разобрать время в формате ЧЧ:ММ
    try:
        if ':' in time_text:
            parts = time_text.split(':')
            hour = int(parts[0])
            minute = int(parts[1])
        else:
            # Если введено просто число - считаем как часы
            hour = int(time_text)
            minute = 0
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            global REPORT_HOUR, REPORT_MINUTE
            REPORT_HOUR = hour
            REPORT_MINUTE = minute
            
            # Сохраняем в .env
            env_file = Path(__file__).parent / '.env'
            content = env_file.read_text(encoding='utf-8')
            
            lines = content.split('\n')
            new_lines = []
            hour_updated = False
            minute_updated = False
            
            for line in lines:
                if line.startswith('REPORT_HOUR='):
                    new_lines.append(f'REPORT_HOUR={REPORT_HOUR}')
                    hour_updated = True
                elif line.startswith('REPORT_MINUTE='):
                    new_lines.append(f'REPORT_MINUTE={REPORT_MINUTE}')
                    minute_updated = True
                else:
                    new_lines.append(line)
            
            if not hour_updated:
                new_lines.append(f'REPORT_HOUR={REPORT_HOUR}')
            if not minute_updated:
                new_lines.append(f'REPORT_MINUTE={REPORT_MINUTE}')
            
            env_file.write_text('\n'.join(new_lines), encoding='utf-8')
            
            await update.message.reply_text(
                f"✅ <b>Время отчёта сохранено!</b>\n\n"
                f"Отчёт будет отправляться в {REPORT_HOUR:02d}:{REPORT_MINUTE:02d}",
                parse_mode='HTML'
            )
            
            # Показываем меню настроек
            await update.message.reply_text(
                "⚙️ <b>Настройка</b>",
                parse_mode='HTML',
                reply_markup=get_settings_keyboard(REPORT_HOUR, REPORT_MINUTE)
            )
            
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                "❌ Неверное время. Часы 0-23, минуты 0-59.\n"
                "Введите время в формате ЧЧ:ММ (например 12:00):"
            )
            return SET_REPORT_HOUR
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Неверный формат. Введите время в формате ЧЧ:ММ\n"
            "Например: 12:00 или 08:30"
        )
        return SET_REPORT_HOUR


async def cancel_report_time_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена настройки времени"""
    report_time_data.clear()
    await update.message.reply_text("❌ Настройка времени отменена")
    return ConversationHandler.END


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Error: {context.error}")
    
    # Проверяем на конфликт poller
    if "409" in str(context.error) or "Conflict" in str(context.error):
        logger.error("Обнаружен конфликт: бот уже запущен. Остановите другой экземпляр бота.")
        if update and update.message:
            await update.message.reply_text("⚠️ Бот уже запущен. Остановите другой экземпляр бота.")
        return
    
    # Проверяем, есть ли message для ответа
    if update and update.message:
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
    else:
        logger.error("Нет доступного message для ответа")


def main():
    """Запуск бота."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не настроен в .env")
        sys.exit(1)
    
    logger.info("Запуск Telegram бота...")
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Conversation handler для OAuth
    oauth_handler = ConversationHandler(
        entry_points=[CommandHandler("authfat", authfat_command)],
        states={
            OAUTH_WAITING_FOR_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, oauth_pin_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_oauth)],
    )
    
    # Conversation handler для настройки Garmin
    garmin_setup_handler = ConversationHandler(
        entry_points=[CommandHandler("setupgarmin", setupgarmin_command)],
        states={
            GARMIN_SETUP_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^/"), garmin_setup_email_received)],
            GARMIN_SETUP_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^/"), garmin_setup_password_received)],
            GARMIN_SETUP_MFA: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^/"), garmin_setup_mfa_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_garmin_setup)],
    )
    
    # Conversation handler для настройки времени отчёта
    report_time_handler = ConversationHandler(
        entry_points=[CommandHandler("settime", set_report_time_command)],
        states={
            SET_REPORT_HOUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_time_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_report_time_setup)],
    )
    
    # Обработчик команды /report
    async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /report - ежедневный отчёт за вчера"""
        await update.message.reply_text("⏳ Загружаю отчёт за вчера...")
        
        # Инициализируем подключения если нужно
        if not bot.garmin_logged_in:
            success, msg = await bot.init_garmin()
            if not success:
                await update.message.reply_text(f"⚠️ Garmin: {msg}")
        
        if not bot.fatsecret_logged_in:
            success, msg = await bot.init_fatsecret()
            if not success:
                await update.message.reply_text(f"⚠️ FatSecret: {msg}")
        
        # Получаем отчёт
        data = await bot.get_daily_report()
        await update.message.reply_text(data, parse_mode='HTML')
    
    # Обработчик команды /week
    async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /week - еженедельная аналитика"""
        await update.message.reply_text("⏳ Загружаю аналитику за неделю...")
        
        # Инициализируем подключения если нужно
        if not bot.garmin_logged_in:
            success, msg = await bot.init_garmin()
            if not success:
                await update.message.reply_text(f"⚠️ Garmin: {msg}")
        
        if not bot.fatsecret_logged_in:
            success, msg = await bot.init_fatsecret()
            if not success:
                await update.message.reply_text(f"⚠️ FatSecret: {msg}")
        
        # Получаем отчёт
        data = await bot.get_weekly_report()
        await update.message.reply_text(data, parse_mode='HTML')
    
    # Обработчики кнопок
    async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        text = update.message.text
        
        # Проверяем, находимся ли мы в процессе настройки Garmin
        garmin_state = context.user_data.get('garmin_setup_state')
        if garmin_state == GARMIN_SETUP_EMAIL:
            # Ожидаем ввод email
            context.user_data.pop('garmin_setup_state', None)
            await garmin_setup_email_received(update, context)
            # Устанавливаем следующее состояние
            context.user_data['garmin_setup_state'] = GARMIN_SETUP_PASSWORD
            return
        elif garmin_state == GARMIN_SETUP_PASSWORD:
            # Ожидаем ввод пароля
            context.user_data.pop('garmin_setup_state', None)
            result = await garmin_setup_password_received(update, context)
            # Если требуется MFA, устанавливаем соответствующее состояние
            if result == GARMIN_SETUP_MFA:
                context.user_data['garmin_setup_state'] = GARMIN_SETUP_MFA
            return
        elif garmin_state == GARMIN_SETUP_MFA:
            # Ожидаем ввод MFA кода
            context.user_data.pop('garmin_setup_state', None)
            await garmin_setup_mfa_received(update, context)
            return
        
        # Проверяем, ожидаем ли мы ввод PIN кода FatSecret
        if context.user_data.get('fatsecret_oauth_state') == OAUTH_WAITING_FOR_PIN:
            context.user_data.pop('fatsecret_oauth_state', None)
            await oauth_pin_received(update, context)
            return
        
        # Проверяем, ожидаем ли мы ввод времени
        if context.user_data.get('waiting_for_time'):
            # Очищаем состояние
            context.user_data.pop('waiting_for_time', None)
            
            # Обрабатываем ввод времени
            time_text = text.strip()
            try:
                if ':' in time_text:
                    parts = time_text.split(':')
                    hour = int(parts[0])
                    minute = int(parts[1])
                else:
                    hour = int(time_text)
                    minute = 0
                
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    global REPORT_HOUR, REPORT_MINUTE
                    REPORT_HOUR = hour
                    REPORT_MINUTE = minute
                    
                    # Сохраняем в .env
                    env_file = Path(__file__).parent / '.env'
                    content = env_file.read_text(encoding='utf-8')
                    
                    lines = content.split('\n')
                    new_lines = []
                    hour_updated = False
                    minute_updated = False
                    
                    for line in lines:
                        if line.startswith('REPORT_HOUR='):
                            new_lines.append(f'REPORT_HOUR={REPORT_HOUR}')
                            hour_updated = True
                        elif line.startswith('REPORT_MINUTE='):
                            new_lines.append(f'REPORT_MINUTE={REPORT_MINUTE}')
                            minute_updated = True
                        else:
                            new_lines.append(line)
                    
                    if not hour_updated:
                        new_lines.append(f'REPORT_HOUR={REPORT_HOUR}')
                    if not minute_updated:
                        new_lines.append(f'REPORT_MINUTE={REPORT_MINUTE}')
                    
                    env_file.write_text('\n'.join(new_lines), encoding='utf-8')
                    
                    # Обновляем расписание
                    try:
                        jq = context.application.job_queue
                        if jq:
                            schedule_daily_report(jq, target_chat_id)
                    except Exception as e:
                        logger.error(f"Ошибка обновления расписания: {e}")
                    
                    await update.message.reply_text(
                        f"✅ <b>Время отчёта сохранено!</b>\n\n"
                        f"Отчёт будет отправляться в {REPORT_HOUR:02d}:{REPORT_MINUTE:02d}",
                        parse_mode='HTML'
                    )
                    
                    # Показываем меню настроек
                    await update.message.reply_text(
                        "⚙️ <b>Настройка</b>",
                        parse_mode='HTML',
                        reply_markup=get_settings_keyboard(REPORT_HOUR, REPORT_MINUTE)
                    )
                    return
                else:
                    await update.message.reply_text(
                        "❌ Неверное время. Часы 0-23, минуты 0-59.\n"
                        "Введите время в формате ЧЧ:ММ (например 12:00):"
                    )
                    return
            except (ValueError, IndexError):
                await update.message.reply_text(
                    "❌ Неверный формат. Введите время в формате ЧЧ:ММ\n"
                    "Например: 12:00 или 08:30"
                )
                return
        
        # Проверяем, ожидаем ли мы ввод часового пояса
        if context.user_data.get('waiting_for_timezone'):
            # Очищаем состояние
            context.user_data.pop('waiting_for_timezone', None)
            
            # Обрабатываем ввод часового пояса
            tz_text = text.strip()
            try:
                tz_offset = int(tz_text)
                
                if -12 <= tz_offset <= 14:
                    global TIMEZONE_OFFSET
                    TIMEZONE_OFFSET = tz_offset
                    
                    # Сохраняем в .env
                    env_file = Path(__file__).parent / '.env'
                    content = env_file.read_text(encoding='utf-8')
                    
                    lines = content.split('\n')
                    new_lines = []
                    tz_updated = False
                    
                    for line in lines:
                        if line.startswith('TIMEZONE_OFFSET='):
                            new_lines.append(f'TIMEZONE_OFFSET={TIMEZONE_OFFSET}')
                            tz_updated = True
                        else:
                            new_lines.append(line)
                    
                    if not tz_updated:
                        new_lines.append(f'TIMEZONE_OFFSET={TIMEZONE_OFFSET}')
                    
                    env_file.write_text('\n'.join(new_lines), encoding='utf-8')
                    
                    # Обновляем расписание
                    try:
                        jq = context.application.job_queue
                        if jq:
                            schedule_daily_report(jq, target_chat_id)
                    except Exception as e:
                        logger.error(f"Ошибка обновления расписания: {e}")
                    
                    tz_display = f"UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}"
                    await update.message.reply_text(
                        f"✅ <b>Часовой пояс сохранён!</b>\n\n"
                        f"Ваш часовой пояс: {tz_display}",
                        parse_mode='HTML'
                    )
                    
                    # Показываем меню настроек
                    await update.message.reply_text(
                        "⚙️ <b>Настройка</b>",
                        parse_mode='HTML',
                        reply_markup=get_settings_keyboard(REPORT_HOUR, REPORT_MINUTE, TIMEZONE_OFFSET)
                    )
                    return
                else:
                    await update.message.reply_text(
                        "❌ Неверный часовой пояс. Диапазон: -12 до +14\n"
                        "Например: 3 (для Москвы UTC+3)"
                    )
                    return
            except ValueError:
                await update.message.reply_text(
                    "❌ Неверный формат. Введите число (сдвиг от UTC):\n"
                    "Например: 3 (для Москвы UTC+3)"
                )
                return
        
        # Обработка кнопок меню
        if text == "📊 Сегодня":
            await today_command(update, context)
        elif text == "📋 Вчера":
            await report_command(update, context)
        elif text == "📈 Неделя":
            await week_command(update, context)
        elif text == "⚙️ Настройка":
            # Показываем меню настроек
            await update.message.reply_text(
                "⚙️ <b>Настройка</b>\n\n"
                "Выберите действие:",
                parse_mode='HTML',
                reply_markup=get_settings_keyboard(REPORT_HOUR, REPORT_MINUTE)
            )
        elif text == "🔐 FatSecret Auth":
            # Запускаем OAuth авторизацию FatSecret
            context.user_data['fatsecret_oauth_state'] = OAUTH_WAITING_FOR_PIN
            await authfat_command(update, context)
        elif text == "⚙️ Garmin Setup":
            # Запускаем настройку Garmin через ConversationHandler
            context.user_data['garmin_setup_state'] = GARMIN_SETUP_EMAIL
            await setupgarmin_command(update, context)
        elif text.startswith("⏰ Время отчёта:"):
            # Переход к настройке времени
            await update.message.reply_text(
                f"⏰ <b>Настройка времени отчёта</b>\n\n"
                f"Текущее время: {REPORT_HOUR:02d}:{REPORT_MINUTE:02d}\n\n"
                "Введите время в формате ЧЧ:ММ (например 12:00 или 08:30):",
                parse_mode='HTML'
            )
            # Устанавливаем состояние ожидания времени
            context.user_data['waiting_for_time'] = True
            return
        elif text.startswith("🌍 Часовой пояс:"):
            # Переход к настройке часового пояса
            await update.message.reply_text(
                f"🌍 <b>Настройка часового пояса</b>\n\n"
                f"Текущий часовой пояс: UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}\n\n"
                "Введите ваш часовой пояс (сдвиг от UTC):\n"
                "Например: 3 (для Москвы UTC+3)\n"
                "         -5 (для Нью-Йорка UTC-5)",
                parse_mode='HTML'
            )
            # Устанавливаем состояние ожидания часового пояса
            context.user_data['waiting_for_timezone'] = True
            return
        elif text == "🔙 Назад":
            # Возврат в главное меню
            await update.message.reply_text(
                "👋 <b>Главное меню</b>",
                parse_mode='HTML',
                reply_markup=get_main_menu_keyboard()
            )
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("garmin", garmin_command))
    application.add_handler(CommandHandler("food", food_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CommandHandler("sendreport", report_command))
    # Обработчик кнопок (должен быть после команд)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    # setupgarmin добавлен через ConversationHandler
    application.add_handler(oauth_handler)
    application.add_handler(garmin_setup_handler)
    application.add_handler(report_time_handler)
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Настройка JobQueue для автоматической отправки отчётов
    job_queue = application.job_queue
    
    async def send_daily_report_job(context: ContextTypes.DEFAULT_TYPE):
        """Отправка ежедневного отчёта по расписанию"""
        logger.info("Запуск автоматического отчёта за вчера...")
        
        # Используем chat_id из контекста задачи или из .env
        chat_id = context.job.chat_id if context.job.chat_id else TELEGRAM_CHAT_ID
        logger.info(f"Отправка в chat_id: {chat_id}")
        
        # Инициализируем подключения если нужно
        if not bot.garmin_logged_in:
            success, msg = await bot.init_garmin()
            if not success:
                logger.error(f"Garmin: {msg}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Garmin: {msg}"
                )
                return
        
        if not bot.fatsecret_logged_in:
            success, msg = await bot.init_fatsecret()
            if not success:
                logger.error(f"FatSecret: {msg}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ FatSecret: {msg}"
                )
                return
        
        # Получаем отчёт
        report = await bot.get_daily_report()
        await context.bot.send_message(
            chat_id=chat_id,
            text=report,
            parse_mode='HTML'
        )
        logger.info("Автоматический отчёт отправлен")
    
    # Вычисляем время отправки с учётом часового пояса пользователя
    # REPORT_HOUR - это время в часовом поясе пользователя
    # Переводим в UTC: UTC_time = user_time - timezone_offset
    utc_hour = (REPORT_HOUR - TIMEZONE_OFFSET) % 24
    utc_minute = REPORT_MINUTE
    
    # Используем chat_id из .env для scheduled задач
    target_chat_id = TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else None
    
    # Планируем ежедневную отправку с учётом часового пояса
    job_queue.run_daily(send_daily_report_job, time=time(hour=utc_hour, minute=utc_minute), chat_id=target_chat_id)
    
    # Для тестирования - отправка через 30 секунд после запуска
    # job_queue.run_once(send_daily_report_job, when=30, name="test_daily_report", chat_id=target_chat_id)
    
    tz_display = f"UTC{'+' if TIMEZONE_OFFSET >= 0 else ''}{TIMEZONE_OFFSET}"
    logger.info(f"Настроено автоматическое расписание отчётов в {REPORT_HOUR:02d}:{REPORT_MINUTE:02d} ({tz_display})")
    logger.info(f"Время в UTC: {utc_hour:02d}:{utc_minute:02d}")
    
    # Обработчик команды /sendreport для ручной отправки
    async def sendreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /sendreport - ручная отправка отчёта"""
        await update.message.reply_text("⏳ Отправляю отчёт...")
        
        # Инициализируем подключения если нужно
        if not bot.garmin_logged_in:
            success, msg = await bot.init_garmin()
            if not success:
                await update.message.reply_text(f"⚠️ Garmin: {msg}")
                return
        
        if not bot.fatsecret_logged_in:
            success, msg = await bot.init_fatsecret()
            if not success:
                await update.message.reply_text(f"⚠️ FatSecret: {msg}")
                return
        
        # Получаем отчёт
        report = await bot.get_daily_report()
        
        # Отправляем отчёт
        await update.message.reply_text(report, parse_mode='HTML')
        logger.info("Отчёт отправлен по запросу пользователя")
    
    # Добавляем обработчик команды /sendreport
    application.add_handler(CommandHandler("sendreport", sendreport_command))

    # Запускаем бота
    logger.info("Бот запущен. Ожидание команд...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()