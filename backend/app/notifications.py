"""Telegram notification helpers."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_CHAT_ID = "-5239915762"


async def send_telegram_alert(message: str) -> bool:
    """Send a message to the Ev0 Telegram group. Returns True on success."""
    token = getattr(settings, "telegram_bot_token", None)
    if not token:
        logger.debug("TELEGRAM_BOT_TOKEN not set — skipping notification")
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False
