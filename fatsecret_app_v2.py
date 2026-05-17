#!/usr/bin/env python3
"""
Скрипт для получения дневника питания из FatSecret за сегодня

Для работы требуется:
1. Зарегистрироваться на https://platform.fatsecret.com/api/
2. Получить Consumer Key и Consumer Secret
3. Пройти OAuth авторизацию для доступа к личным данным
"""

import os
import sys

# Настройка UTF-8 для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from datetime import date as date_module
from pathlib import Path
from dotenv import load_dotenv

# Загрузка переменных окружения
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

# Конфигурация из .env
CONSUMER_KEY = os.getenv('FATSECRET_CONSUMER_KEY', '')
CONSUMER_SECRET = os.getenv('FATSECRET_CONSUMER_SECRET', '')
OAUTH_TOKEN = os.getenv('FATSECRET_OAUTH_TOKEN', '')
OAUTH_TOKEN_SECRET = os.getenv('FATSECRET_OAUTH_TOKEN_SECRET', '')


def authenticate_oauth(fs):
    """Выполнение OAuth авторизации для доступа к личным данным"""
    print("\n🔐 Требуется авторизация FatSecret...")
    print("1. Откройте следующий URL в браузере:")
    
    auth_url = fs.get_authorize_url()
    print(f"\n{auth_url}\n")
    
    print("2. Авторизуйтесь и скопируйте PIN код")
    pin = input("Введите PIN: ").strip()
    
    print("\n3. Получаем токены доступа...")
    access_token, access_secret = fs.authenticate(pin)
    
    print(f"\n✅ Авторизация успешна!")
    print(f"   Access Token: {access_token[:20]}...")
    print(f"   Access Secret: {access_secret[:20]}...")
    
    print("\n📝 Добавьте эти значения в .env файл:")
    print(f"FATSECRET_OAUTH_TOKEN={access_token}")
    print(f"FATSECRET_OAUTH_TOKEN_SECRET={access_secret}")
    
    return fs


def get_diary_entries(fs, target_date=None):
    """Получение записей дневника питания"""
    if target_date is None:
        target_date = date_module.today()
    
    print(f"\n📝 Получаем дневник питания за {target_date}...")
    
    try:
        # Используем метод diary.entries_get_v2
        entries = fs.diary.entries_get_v2(date=target_date)
        
        if not entries:
            print("⚠️ Записей питания за этот день не найдено")
            return None
        
        print(f"✅ Найдено {len(entries)} записей")
        return entries
        
    except Exception as e:
        print(f"❌ Ошибка получения данных: {e}")
        return None


def display_entries(entries):
    """Отображение записей дневника питания"""
    if not entries:
        return
    
    # Группировка по приемам пищи
    meals = {}
    totals = {'calories': 0, 'protein': 0, 'carbohydrate': 0, 'fat': 0}
    
    for entry in entries:
        meal = entry.meal
        if meal not in meals:
            meals[meal] = []
        
        food_name = entry.food_name
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
    
    # Отображение
    print("\n" + "=" * 50)
    print("🥗 ДНЕВНИК ПИТАНИЯ - СЕГОДНЯ")
    print("=" * 50)
    
    meal_names = {
        'Breakfast': '🌅 Завтрак',
        'Lunch': '☀️ Обед',
        'Dinner': '🌙 Ужин',
        'Other': '🍿 Перекус'
    }
    
    for meal, foods in meals.items():
        meal_name = meal_names.get(meal, meal)
        print(f"\n{meal_name}:")
        
        meal_cal = 0
        for food in foods:
            print(f"  • {food['name']}")
            print(f"      {int(food['calories'])} ккал | "
                  f"Б: {int(food['protein'])}г | "
                  f"У: {int(food['carbs'])}г | "
                  f"Ж: {int(food['fat'])}г")
            meal_cal += food['calories']
        
        print(f"  ─────────────────────────")
        print(f"  Итого: {int(meal_cal)} ккал")
    
    # Итоги
    print("\n" + "=" * 50)
    print("📊 ВСЕГО ЗА ДЕНЬ:")
    print("=" * 50)
    print(f"  Калории:  {int(totals['calories'])} ккал")
    print(f"  Белки:    {int(totals['protein'])} г")
    print(f"  Углеводы: {int(totals['carbohydrate'])} г")
    print(f"  Жиры:     {int(totals['fat'])} г")


def main():
    """Главная функция"""
    from fatsecret import Fatsecret
    
    print("=" * 50)
    print("  FATSECRET - ДНЕВНИК ПИТАНИЯ")
    print("=" * 50)
    
    # Проверка конфигурации
    if not CONSUMER_KEY or not CONSUMER_SECRET:
        print("❌ Ошибка: FATSECRET_CONSUMER_KEY и FATSECRET_CONSUMER_SECRET")
        print("   не настроены в .env файле")
        sys.exit(1)
    
    # Инициализация FatSecret
    try:
        if OAUTH_TOKEN and OAUTH_TOKEN_SECRET:
            # Используем сохраненные токены
            print("\n🔑 Используем сохраненные OAuth токены...")
            fs = Fatsecret(
                CONSUMER_KEY,
                CONSUMER_SECRET,
                session_token=(OAUTH_TOKEN, OAUTH_TOKEN_SECRET)
            )
        else:
            # Создаем новый клиент (только публичный доступ)
            print("\n⚠️ OAuth токены не настроены. Используется публичный доступ.")
            print("   Для доступа к дневнику питания требуется авторизация.")
            fs = Fatsecret(CONSUMER_KEY, CONSUMER_SECRET)
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        sys.exit(1)
    
    # Проверка авторизации
    try:
        # Пробуем получить профиль для проверки авторизации
        profile = fs.profile.get_v1()
        print("✅ Подключено к FatSecret (авторизовано)")
    except Exception as e:
        # Если не удается получить профиль - пробуем авторизацию
        if "401" in str(e) or "Unauthorized" in str(e):
            print("⚠️ Требуется авторизация. Запускаю OAuth процесс...")
            fs = authenticate_oauth(fs)
        else:
            print(f"⚠️ Не удалось проверить авторизацию: {e}")
    
    # Получение и отображение записей
    entries = get_diary_entries(fs)
    
    if entries:
        display_entries(entries)
    else:
        print("\n📭 Записей питания за сегодня нет")
        print("   Добавьте записи в приложении FatSecret и повторите")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    main()