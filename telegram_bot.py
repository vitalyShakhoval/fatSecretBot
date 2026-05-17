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
from datetime import date, timedelta
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


# Инициализация бота
bot = HealthBot()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "👋 Привет! Я бот для отображения данных о здоровье.\n\n"
        "Команды:\n"
        "/start - Показать это сообщение\n"
        "/today - Данные за сегодня\n"
        "/garmin - Только Garmin\n"
        "/food - Только питание\n"
        "/authfat - Авторизация FatSecret\n"
        "/help - Помощь"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    await update.message.reply_text(
        "📖 <b>Справка</b>\n\n"
        "Этот бот показывает данные из:\n"
        "• <b>Garmin Connect</b> - шаги, калории, сон, пульс\n"
        "• <b>FatSecret</b> - дневник питания\n\n"
        "Команды:\n"
        "/today - Сводка за сегодня\n"
        "/garmin - Данные Garmin\n"
        "/food - Дневник питания\n"
        "/authfat - Авторизация FatSecret\n"
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
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("garmin", garmin_command))
    application.add_handler(CommandHandler("food", food_command))
    # setupgarmin добавлен через ConversationHandler
    application.add_handler(oauth_handler)
    application.add_handler(garmin_setup_handler)
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Бот запущен. Ожидание команд...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()