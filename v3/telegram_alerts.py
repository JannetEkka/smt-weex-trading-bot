import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

BOT_TOKEN = "8204452736:AAGGGwZzZq2tmMFqnuLPNGAbY7jD4zI-gXQ"
CHAT_ID = "6655570461"

def send_telegram_alert(message: str):
    """Send alert to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            logger.info("Telegram alert sent")
            return True
        else:
            logger.error(f"Telegram error: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False
