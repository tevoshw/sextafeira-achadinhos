# config.py (na raiz, ao lado do main_bot.py)
import os

from dotenv import load_dotenv

load_dotenv()

GRUPO_ID = os.getenv("GRUPO_ID")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET")

ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET")
ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID")

AMAZON_CREDENTIAL_ID = os.getenv("AMAZON_CREDENTIAL_ID")
AMAZON_CREDENTIAL_SECRET = os.getenv("AMAZON_CREDENTIAL_SECRET")
AMAZON_TAG = os.getenv("AMAZON_TAG")