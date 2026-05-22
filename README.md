# Health Bot - Telegram Health Tracker

Python application for retrieving health statistics from Garmin Connect and FatSecret via Telegram bot.

## Features

### Garmin Connect
- 👟 **Steps** - daily goal and progress
- 📏 **Distance** - distance in km
- 🔥 **Calories** - total and active
- ⏱️ **Active Minutes** - physical activity time
- 🏢 **Floors** - floors climbed
- 💓 **Resting Heart Rate** - heart rate at rest

### FatSecret
- 📝 **Food Diary** - food entries by meal
- 🥗 **Macronutrients** - protein, carbs, fat
- 🔢 **Calories** - daily intake

### Telegram Bot
- 📊 `/today` - data for today
- 📋 `/report` - report for yesterday
- 📈 `/week` - weekly analytics
- 🔐 `/authfat` - FatSecret authorization
- ⚙️ `/setupgarmin` - Garmin setup
- ⏰ Automatic report delivery on schedule

## Requirements

- Python 3.12+
- Telegram bot (get from @BotFather)
- FatSecret account (with API keys)
- Garmin Connect account

## Getting FatSecret API Keys

1. Register at https://platform.fatsecret.com
2. After creation you will get:
   - **Client ID** (Consumer Key)
   - **Client Secret** (Consumer Secret)
3. To get OAuth tokens use `/authfat` command in the bot

Documentation: https://platform.fatsecret.com/api/

## Installation

1. Clone the repository or download files

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create `.env` file from `.env.example`:
```bash
copy .env.example .env
```

4. Edit `.env`:
```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Garmin
EMAIL=your_garmin_email
PASSWORD=your_garmin_password

# FatSecret (get from https://platform.fatsecret.com)
FATSECRET_CLIENT_ID=your_client_id
FATSECRET_CONSUMER_KEY=your_consumer_key
FATSECRET_CONSUMER_SECRET=your_consumer_secret

# Report settings (optional)
REPORT_HOUR=12
REPORT_MINUTE=0
TIMEZONE_OFFSET=3
```

## Usage

### Run Telegram bot:
```bash
python telegram_bot.py
```

### FatSecret Authorization:
1. Send `/authfat` to the bot
2. Follow the link and authorize
3. Get PIN code
4. Send PIN to the bot

### Garmin Setup:
1. Send `/setupgarmin` to the bot
2. Enter email and password
3. Enter MFA code if required

### Automatic Reports:
The bot sends daily report at the specified time. Configure via:
- "⏰ Report time" button in menu
- `/settime` command
- `REPORT_HOUR` and `REPORT_MINUTE` in `.env`

## Project Structure

```
fatSecretBot/
├── telegram_bot.py      # Main Telegram bot
├── requirements.txt    # Dependencies
├── .env.example         # Example config file
└── README.md           # This file
```

## Security

- **Never** publish `.env` file with your real credentials
- `.gitignore` is already configured to exclude `.env`
- Access tokens are stored locally

## License

MIT License