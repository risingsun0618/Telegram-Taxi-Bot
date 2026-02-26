# Telegram Bot Configuration (v2)
# IMPORTANT: Never commit real tokens. Use environment variables for production.

import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "7258222934:AAEO0yXO4D8D2WxO9n-s7ZFP-UbBQqUFX6I")

# Admin user IDs (Telegram numeric IDs)
# Tip: run the bot and use /myid to get your id
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "1022755242,8292153740").split(",") if x.strip().isdigit()]


# Matching
MATCH_RADIUS_KM = float(os.getenv("MATCH_RADIUS_KM", "5.0"))
MATCH_TIME_WINDOW_MINUTES = int(os.getenv("MATCH_TIME_WINDOW_MINUTES", "30"))
REQUEST_TIMEOUT_MINUTES = int(os.getenv("REQUEST_TIMEOUT_MINUTES", "30"))

# Fare
FARE_BASE = float(os.getenv("FARE_BASE", "2.00"))
FARE_PER_KM = float(os.getenv("FARE_PER_KM", "0.30"))
FARE_CURRENCY = os.getenv("FARE_CURRENCY", "$")

# Dynamic features
ENABLE_PRIORITY_MATCHING = os.getenv("ENABLE_PRIORITY_MATCHING", "1") == "1"
PRIORITY_FEE = float(os.getenv("PRIORITY_FEE", "1.50"))

ENABLE_SURGE_PRICING = os.getenv("ENABLE_SURGE_PRICING", "1") == "1"
SURGE_MAX_MULTIPLIER = float(os.getenv("SURGE_MAX_MULTIPLIER", "2.0"))
SURGE_MIN_MULTIPLIER = float(os.getenv("SURGE_MIN_MULTIPLIER", "1.0"))

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "rideshare.db")

# Documents
DOCUMENTS_PATH = os.getenv("DOCUMENTS_PATH", "documents")

# Notifications / reminders
REMINDER_CHECK_INTERVAL_SECONDS = int(os.getenv("REMINDER_CHECK_INTERVAL_SECONDS", "30"))
REMIND_BEFORE_MINUTES = int(os.getenv("REMIND_BEFORE_MINUTES", "20"))
