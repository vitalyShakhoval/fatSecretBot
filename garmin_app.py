#!/usr/bin/env python3
"""
Garmin Connect Statistics Application

This application connects to Garmin Connect API and retrieves
basic user statistics including health metrics, activities, and profile information.

Usage:
    1. Copy .env.example to .env and fill in your credentials
    2. Run: pip install -r requirements.txt
    3. Run: python garmin_app.py
"""

import os
import sys
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import request, parse

# Ensure Unicode output does not crash in Windows cp1251 console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Third-party imports
from dotenv import load_dotenv
from garminconnect import Garmin


class GarminApp:
    """Main application class for Garmin Connect API integration."""
    
    def __init__(self):
        """Initialize the Garmin application."""
        self.client = None
        self.token_path = os.getenv("TOKEN_PATH", "~/.garminconnect")

    @staticmethod
    def get_report_date():
        """Return report date as previous day in ISO format."""
        return (date.today() - timedelta(days=1)).isoformat()
        
    def load_credentials(self):
        """Load credentials from .env file."""
        # Try to load from .env file
        env_path = Path(".env")
        if env_path.exists():
            load_dotenv(env_path)
            print("✓ Loaded credentials from .env file")
        else:
            print("⚠ .env file not found, checking environment variables")
        
        # Get credentials from environment
        self.email = os.getenv("EMAIL")
        self.password = os.getenv("PASSWORD")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not self.email or not self.password:
            print("❌ Error: EMAIL and PASSWORD must be set in .env file")
            print("   Copy .env.example to .env and fill in your credentials")
            sys.exit(1)
            
        print(f"✓ Credentials loaded for: {self.email}")

    def get_today_stats(self):
        """Get daily statistics for current day."""
        print("\n📅 Getting current day statistics for Telegram...")

        today = date.today().isoformat()
        try:
            return self.client.get_stats(today)
        except Exception as e:
            print(f"  ⚠ Error getting current day stats: {e}")
            return None

    def send_telegram_daily_summary(self):
        """Send current day summary to Telegram if credentials are provided."""
        print("\n📨 Sending current day statistics to Telegram...")

        if not self.telegram_bot_token or not self.telegram_chat_id:
            print("  ⚠ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, skipping")
            return False

        stats = self.get_today_stats()
        if not stats:
            print("  ⚠ No current day stats available for Telegram")
            return False

        total_steps = stats.get('totalSteps')
        total_distance_m = stats.get('totalDistanceMeters')
        total_kcal = stats.get('totalKilocalories')
        active_kcal = stats.get('activeKilocalories')
        passive_kcal = (total_kcal - active_kcal) if (total_kcal is not None and active_kcal is not None) else None
        active_seconds = stats.get('activeSeconds')
        floors = stats.get('floorsAscended')
        today = date.today().isoformat()

        message = (
            f"📊 Garmin статистика за сегодня ({today})\n"
            f"Шаги: {int(total_steps):,}".replace(",", " ") + "\n"
            f"Дистанция: {total_distance_m / 1000:.2f} км\n"
            f"Калории (активные): {int(active_kcal):,}".replace(",", " ") + "\n"
            f"Калории (пассивные): {int(passive_kcal):,}".replace(",", " ") + "\n"
            f"Калории (всего): {int(total_kcal):,}".replace(",", " ") + "\n"
            f"Активные минуты: {int(active_seconds // 60)}\n"
            f"Этажи: {int(floors)}"
        )

        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = parse.urlencode({
                "chat_id": self.telegram_chat_id,
                "text": message,
            }).encode("utf-8")

            req = request.Request(url, data=payload, method="POST")
            with request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                response_data = json.loads(body)

            if response_data.get("ok"):
                print("  ✓ Telegram message sent")
                return True

            print(f"  ⚠ Telegram API error: {response_data}")
            return False
        except Exception as e:
            print(f"  ⚠ Error sending Telegram message: {e}")
            return False
        
    def login(self):
        """Login to Garmin Connect."""
        print("\n🔐 Connecting to Garmin Connect...")
        
        try:
            # Initialize Garmin client with MFA callback
            self.client = Garmin(
                self.email,
                self.password,
                prompt_mfa=lambda: input(" MFA code: "),
            )
            
            # Login (tokens will be saved to token_path)
            self.client.login(self.token_path)
            print("✓ Successfully logged in to Garmin Connect")
            
        except Exception as e:
            print(f"❌ Login failed: {e}")
            sys.exit(1)
            
    def get_user_info(self):
        """Get basic user profile information."""
        print("\n👤 Getting user profile...")
        
        try:
            user_info = self.client.get_user_profile()
            
            if user_info:
                print("\n" + "="*50)
                print("USER PROFILE")
                print("="*50)
                
                # Display key user information
                display_name = user_info.get("displayName", "N/A")
                first_name = user_info.get("firstName", "N/A")
                last_name = user_info.get("lastName", "N/A")
                member_since = user_info.get("memberSince", "N/A")
                
                print(f"  Display Name: {display_name}")
                print(f"  First Name:  {first_name}")
                print(f"  Last Name:   {last_name}")
                print(f"  Member Since:{member_since}")
                
                return user_info
            else:
                print("  No user info available")
                return None
                
        except Exception as e:
            print(f"  ⚠ Error getting user info: {e}")
            return None
            
    def get_daily_stats(self):
        """Get daily statistics for previous day."""
        print("\n📊 Getting daily statistics...")
        
        report_date = self.get_report_date()
        
        try:
            # Get stats
            stats = self.client.get_stats(report_date)
            
            if stats:
                print("\n" + "="*50)
                print(f"DAILY STATISTICS - {report_date}")
                print("="*50)
                
                # Overview metrics
                print("\n📈 Overview:")
                total_steps = stats.get('totalSteps')
                total_distance_m = stats.get('totalDistanceMeters')
                total_kcal = stats.get('totalKilocalories')
                active_kcal = stats.get('activeKilocalories')
                passive_kcal = (total_kcal - active_kcal) if (total_kcal is not None and active_kcal is not None) else None
                active_seconds = stats.get('activeSeconds')

                print(f"  Steps:               {int(total_steps):,}" if total_steps is not None else "  Steps:               N/A")
                print(f"  Distance:           {total_distance_m / 1000:.2f} km" if total_distance_m is not None else "  Distance:           N/A")
                print(f"  Active Calories:    {int(active_kcal):,}" if active_kcal is not None else "  Active Calories:    N/A")
                print(f"  Passive Calories:   {int(passive_kcal):,}" if passive_kcal is not None else "  Passive Calories:   N/A")
                print(f"  Total Calories:     {int(total_kcal):,}" if total_kcal is not None else "  Total Calories:     N/A")
                print(f"  Active Minutes:     {int(active_seconds // 60)}" if active_seconds is not None else "  Active Minutes:     N/A")
                
                # Floor climbing
                floors = stats.get('floorsAscended')
                print(f"  Floors:             {int(floors)}" if floors is not None else "  Floors:             N/A")
                
                return stats
            else:
                print("  No daily stats available")
                return None
                
        except Exception as e:
            print(f"  ⚠ Error getting daily stats: {e}")
            return None
            
    def get_heart_rate_data(self):
        """Get heart rate data for previous day."""
        print("\n❤️ Getting heart rate data...")
        
        report_date = self.get_report_date()
        
        try:
            # Get heart rates
            hr_data = self.client.get_heart_rates(report_date)
            
            if hr_data:
                print("\n" + "="*50)
                print("HEART RATE DATA")
                print("="*50)
                
                # Get resting heart rate
                resting_hr = hr_data.get('restingHeartRate', 'N/A')
                print(f"\n  Resting Heart Rate: {resting_hr} bpm" if resting_hr != 'N/A' else "\n  Resting Heart Rate: N/A")

                min_hr = hr_data.get('minHeartRate')
                max_hr = hr_data.get('maxHeartRate')
                print(f"  Min Heart Rate:     {min_hr} bpm" if min_hr is not None else "  Min Heart Rate:     N/A")
                print(f"  Max Heart Rate:     {max_hr} bpm" if max_hr is not None else "  Max Heart Rate:     N/A")
                
                return hr_data
            else:
                print("  No heart rate data available")
                return None
                
        except Exception as e:
            print(f"  ⚠ Error getting heart rate data: {e}")
            return None

    @staticmethod
    def build_sleep_recommendation(total_sleep_seconds, sleep_score, awake_minutes):
        """Build simple sleep recommendation based on duration, score and awakenings."""
        recommendations = []

        # Sleep duration recommendation
        if total_sleep_seconds < 7 * 3600:
            recommendations.append("Лягте сегодня на 30–60 минут раньше (цель: минимум 7 часов сна).")
        elif total_sleep_seconds > 9 * 3600:
            recommendations.append("Сон длительный — старайтесь сохранять стабильный режим подъёма.")
        else:
            recommendations.append("Продолжительность сна в хорошем диапазоне (7–9 часов).")

        # Sleep score recommendation
        if isinstance(sleep_score, (int, float)):
            if sleep_score < 60:
                recommendations.append("Низкий Sleep Score: уменьшите кофеин вечером и сделайте спокойный отход ко сну.")
            elif sleep_score < 80:
                recommendations.append("Sleep Score средний: полезно добавить 15–30 минут расслабления перед сном.")
            else:
                recommendations.append("Sleep Score хороший — текущие привычки сна работают хорошо.")

        # Awake recommendation
        if awake_minutes >= 45:
            recommendations.append("Много пробуждений: проверьте температуру в спальне и ограничьте экран за 1 час до сна.")

        return " ".join(recommendations)

    @staticmethod
    def build_sleep_extra_insight(avg_stress, avg_spo2, lowest_spo2, hrv_status):
        """Build extra insight from advanced sleep fields."""
        insights = []

        if isinstance(avg_stress, (int, float)):
            if avg_stress <= 15:
                insights.append("Ночной стресс низкий — восстановление идёт хорошо.")
            elif avg_stress <= 25:
                insights.append("Ночной стресс умеренный — стоит добавить спокойный вечерний ритуал.")
            else:
                insights.append("Ночной стресс высокий — лучше снизить интенсивность нагрузки вечером.")

        if isinstance(avg_spo2, (int, float)):
            if avg_spo2 >= 95:
                insights.append("Средний SpO₂ в норме.")
            else:
                insights.append("Средний SpO₂ ниже ожидаемого — проверьте качество воздуха и дыхание во сне.")

        if isinstance(lowest_spo2, (int, float)) and lowest_spo2 < 90:
            insights.append("Минимальный SpO₂ опускался ниже 90% — если повторяется, обсудите с врачом.")

        if hrv_status:
            insights.append(f"HRV-статус: {hrv_status}.")

        return " ".join(insights)
            
    def get_sleep_data(self):
        """Get sleep data for last night."""
        print("\n😴 Getting sleep data...")
        
        try:
            # Get sleep data
            sleep_data = self.client.get_sleep_data(self.get_report_date())
            
            if sleep_data and 'dailySleepDTO' in sleep_data:
                sleep = sleep_data['dailySleepDTO']
                sleep_scores = sleep.get('sleepScores', {})
                
                print("\n" + "="*50)
                print("SLEEP DATA")
                print("="*50)
                
                # Sleep duration
                total_sleep_seconds = sleep.get('sleepTimeSeconds', 0)
                deep_seconds = sleep.get('deepSleepSeconds', 0)
                light_seconds = sleep.get('lightSleepSeconds', 0)
                rem_seconds = sleep.get('remSleepSeconds', 0)
                awake_seconds = sleep.get('awakeSleepSeconds', 0)
                
                hours = total_sleep_seconds // 3600
                minutes = (total_sleep_seconds % 3600) // 60

                sleep_score = sleep.get('sleepScore')
                if sleep_score is None:
                    sleep_score = (
                        sleep_scores
                        .get('overall', {})
                        .get('value', 'N/A')
                    )

                avg_heart_rate = sleep.get('avgHeartRate')
                avg_sleep_stress = sleep.get('avgSleepStress')
                avg_respiration = sleep.get('averageRespirationValue')
                avg_spo2 = sleep.get('averageSpO2Value')
                lowest_spo2 = sleep.get('lowestSpO2Value')
                hrv_status = sleep_data.get('hrvStatus')
                overall_quality = sleep_scores.get('overall', {}).get('qualifierKey')
                
                print(f"\n  Sleep Duration:    {hours}h {minutes}m")
                print(f"  Sleep Score:       {sleep_score}")
                print(f"  Sleep Quality:     {overall_quality}" if overall_quality else "  Sleep Quality:     N/A")
                print(f"  Deep Sleep:        {deep_seconds // 60} min")
                print(f"  Light Sleep:       {light_seconds // 60} min")
                print(f"  REM Sleep:         {rem_seconds // 60} min")
                
                # Awake time
                awake = awake_seconds // 60
                print(f"  Awake Time:        {awake} min")
                print(f"  Avg Sleep HR:      {avg_heart_rate} bpm" if avg_heart_rate is not None else "  Avg Sleep HR:      N/A")
                print(f"  Avg Sleep Stress:  {avg_sleep_stress}" if avg_sleep_stress is not None else "  Avg Sleep Stress:  N/A")
                print(f"  Respiration:       {avg_respiration} br/min" if avg_respiration is not None else "  Respiration:       N/A")
                print(f"  SpO2 Avg/Min:      {avg_spo2}% / {lowest_spo2}%" if avg_spo2 is not None and lowest_spo2 is not None else "  SpO2 Avg/Min:      N/A")
                print(f"  HRV Status:        {hrv_status}" if hrv_status else "  HRV Status:        N/A")

                recommendation = self.build_sleep_recommendation(
                    total_sleep_seconds=total_sleep_seconds,
                    sleep_score=sleep_score,
                    awake_minutes=awake,
                )
                print(f"  Recommendation:    {recommendation}")

                extra_insight = self.build_sleep_extra_insight(
                    avg_stress=avg_sleep_stress,
                    avg_spo2=avg_spo2,
                    lowest_spo2=lowest_spo2,
                    hrv_status=hrv_status,
                )
                print(f"  Insight:           {extra_insight}" if extra_insight else "  Insight:           N/A")
                
                return sleep_data
            else:
                print("  No sleep data available")
                return None
                
        except Exception as e:
            print(f"  ⚠ Error getting sleep data: {e}")
            return None
            
    def get_body_composition(self):
        """Get body composition data (weight, BMI, etc.)."""
        print("\n⚖️ Getting body composition...")
        
        try:
            # Get body composition
            body_data = self.client.get_body_composition(self.get_report_date())
            
            if body_data:
                print("\n" + "="*50)
                print("BODY COMPOSITION")
                print("="*50)

                date_weight_list = body_data.get('dateWeightList', [])
                if date_weight_list:
                    latest = date_weight_list[0]
                    weight = latest.get('weight')
                    bmi = latest.get('bmi')
                    body_fat = latest.get('bodyFat')

                    print(f"\n  Weight:            {weight} kg" if weight is not None else "\n  Weight:            N/A")
                    print(f"  BMI:               {bmi}" if bmi is not None else "  BMI:               N/A")
                    print(f"  Body Fat:          {body_fat}%" if body_fat is not None else "  Body Fat:          N/A")
                else:
                    avg = body_data.get('totalAverage', {})
                    weight = avg.get('weight')
                    bmi = avg.get('bmi')
                    body_fat = avg.get('bodyFat')

                    print(f"\n  Weight:            {weight} kg" if weight is not None else "\n  Weight:            N/A")
                    print(f"  BMI:               {bmi}" if bmi is not None else "  BMI:               N/A")
                    print(f"  Body Fat:          {body_fat}%" if body_fat is not None else "  Body Fat:          N/A")

                return body_data
                    
            print("  No body composition data available")
            return None
            
        except Exception as e:
            print(f"  ⚠ Error getting body composition: {e}")
            return None
            
    def get_activities(self):
        """Get recent activities."""
        print("\n🏃 Getting recent activities...")
        
        try:
            # Get activities (last 7 days)
            activities = self.client.get_activities(0, 10)  # First page, 10 items
            
            if activities:
                print("\n" + "="*50)
                print("RECENT ACTIVITIES")
                print("="*50)
                
                for i, activity in enumerate(activities[:5], 1):  # Show top 5
                    activity_name = activity.get('activityName', 'Unknown')
                    start_time = activity.get('startTimeLocal', 'N/A')
                    duration = activity.get('duration', 0)
                    distance = activity.get('distance', 0)
                    
                    # Format duration
                    hours = int(duration // 3600)
                    minutes = int((duration % 3600) // 60)
                    seconds = int(duration % 60)
                    
                    # Format distance
                    distance_km = distance / 1000  # Convert to km
                    
                    print(f"\n  {i}. {activity_name}")
                    print(f"     Date: {start_time[:10] if start_time != 'N/A' else 'N/A'}")
                    print(f"     Duration: {hours}h {minutes}m {seconds}s")
                    print(f"     Distance: {distance_km:.2f} km")
                    
                return activities
            else:
                print("  No recent activities found")
                return None
                
        except Exception as e:
            print(f"  ⚠ Error getting activities: {e}")
            return None

    def get_today_workout(self):
        """Get scheduled workout for today."""
        print("\n🗓️ Getting today's planned workout...")

        try:
            today = date.today()
            today_str = today.isoformat()
            schedule = self.client.get_scheduled_workouts(today.year, today.month)
            items = schedule.get("calendarItems", []) if schedule else []

            workout_items = [
                item for item in items
                if item.get("date") == today_str and item.get("itemType") == "workout"
            ]

            print("\n" + "="*50)
            print("TODAY PLANNED WORKOUT")
            print("="*50)

            if not workout_items:
                print("  No workout planned for today")
                return None

            planned = workout_items[0]
            title = planned.get("title")
            sport = planned.get("sportTypeKey", "N/A")
            workout_id = planned.get("workoutId")

            if title:
                print(f"\n  Title:             {title}")
            else:
                print("\n  Title:             N/A")
            print(f"  Sport:             {sport}")
            print(f"  Date:              {today_str}")
            if workout_id:
                print(f"  Workout ID:        {workout_id}")

            return planned

        except Exception as e:
            print(f"  ⚠ Error getting planned workout: {e}")
            return None
            
    def get_devices(self):
        """Get connected devices."""
        print("\n⌚ Getting connected devices...")
        
        try:
            devices = self.client.get_devices()
            
            if devices:
                print("\n" + "="*50)
                print("CONNECTED DEVICES")
                print("="*50)
                
                for device in devices:
                    device_name = device.get('deviceName', 'Unknown')
                    device_type = device.get('deviceType', 'N/A')
                    battery_level = device.get('batteryLevel', 'N/A')
                    
                    print(f"\n  Device: {device_name}")
                    print(f"  Type:   {device_type}")
                    print(f"  Battery:{battery_level}%" if battery_level != 'N/A' else "  Battery: N/A")
                    
                return devices
            else:
                print("  No connected devices found")
                return None
                
        except Exception as e:
            print(f"  ⚠ Error getting devices: {e}")
            return None
            
    def run(self):
        """Run the main application."""
        print("\n" + "="*50)
        print("  GARMIN CONNECT STATISTICS APP")
        print("="*50)
        
        # Load credentials and login
        self.load_credentials()
        self.login()
        
        # Get various statistics
        self.get_user_info()
        self.get_daily_stats()
        self.get_heart_rate_data()
        self.get_sleep_data()
        self.get_body_composition()
        self.get_today_workout()
        self.get_activities()
        self.get_devices()
        # self.send_telegram_daily_summary()
        
        print("\n" + "="*50)
        print("  ✅ Statistics retrieval complete!")
        print("="*50 + "\n")


def main():
    """Main entry point."""
    app = GarminApp()
    app.run()


if __name__ == "__main__":
    main()