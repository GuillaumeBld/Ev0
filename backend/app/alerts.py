"""Canal d'alertes multi-provider — WhatsApp (CallMeBot) avec fallback Telegram.

Deux canaux logiques :
- "ops"   : incidents (crash de jobs, erreurs), résumé santé quotidien
- "recos" : recommandations / value bets en direct

Garde-fous (leçon de l'incident Telegram passé) :
- jamais d'exception propagée à l'appelant
- déduplication du même message pendant 15 min
- intervalle minimum entre deux envois d'un même canal (CallMeBot ~1 msg/15 s)
- `alert_bg()` = fire-and-forget : n'ajoute aucune latence au chemin appelant
"""
from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import logging
import re
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_DEDUP_WINDOW_S = 900   # même message ignoré pendant 15 min
_MIN_INTERVAL_S = 20    # espacement minimum par canal (rate limit CallMeBot)

_last_sent: dict[str, float] = {}
_last_channel_send: dict[str, float] = {}
_lock = asyncio.Lock()


def _channel_conf(channel: str) -> tuple[str, str]:
    if channel == "recos":
        phone = settings.whatsapp_recos_phone or settings.whatsapp_ops_phone
        key = settings.whatsapp_recos_apikey or settings.whatsapp_ops_apikey
    else:
        phone, key = settings.whatsapp_ops_phone, settings.whatsapp_ops_apikey
    return phone or "", key or ""


def _html_to_whatsapp(text: str) -> str:
    """Convertit le HTML Telegram existant en formatage WhatsApp."""
    text = re.sub(r"</?b>", "*", text)
    text = re.sub(r"</?i>", "_", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text)


async def _send_whatsapp(phone: str, apikey: str, text: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.callmebot.com/whatsapp.php",
                params={"phone": phone, "apikey": apikey, "text": _html_to_whatsapp(text)},
                timeout=15,
            )
            r.raise_for_status()
            # CallMeBot renvoie HTTP 200 même en échec. Le corps ÉCHOE le texte
            # du message (donc interdit d'y chercher "error" : un message
            # contenant "TimeoutError" déclencherait un faux négatif). Le seul
            # marqueur fiable est la phrase de succès "Message queued".
            if "queued" not in r.text.lower():
                logger.warning("WhatsApp alert rejeté par CallMeBot: %.200s", r.text)
                return False
            return True
    except Exception as exc:
        logger.warning("WhatsApp alert failed: %s", exc)
        return False


async def send_alert(message: str, channel: str = "ops") -> bool:
    """Envoie une alerte sur le canal donné. Ne lève jamais d'exception."""
    try:
        h = hashlib.sha1(f"{channel}:{message}".encode()).hexdigest()
        now = time.monotonic()
        async with _lock:
            if now - _last_sent.get(h, float("-inf")) < _DEDUP_WINDOW_S:
                return False
            wait = max(0.0, _MIN_INTERVAL_S - (now - _last_channel_send.get(channel, float("-inf"))))
            _last_sent[h] = now + wait
            _last_channel_send[channel] = now + wait
        if wait > 0:
            await asyncio.sleep(wait)

        # Telegram en primaire (illimité) ; WhatsApp en fallback seulement.
        # CallMeBot gratuit a un quota qui s'épuise → on ne le tente qu'en
        # dernier recours, sans polluer les logs à chaque alerte.
        sent = False
        if settings.telegram_bot_token:
            from app.notifications import send_telegram_alert

            sent = await send_telegram_alert(message)
        if not sent:
            phone, apikey = _channel_conf(channel)
            if phone and apikey:
                sent = await _send_whatsapp(phone, apikey, message)
        return sent
    except Exception as exc:  # ceinture + bretelles : une alerte ne casse rien
        logger.warning("send_alert failed: %s", exc)
        return False


def alert_bg(message: str, channel: str = "ops") -> None:
    """Fire-and-forget : planifie l'envoi sans bloquer ni jamais lever."""
    try:
        asyncio.get_running_loop().create_task(send_alert(message, channel))
    except RuntimeError:
        logger.warning("alert_bg: pas d'event loop — alerte perdue: %.80s", message)


class ErrorAlertHandler(logging.Handler):
    """Handler de logs : tout record >= ERROR du worker part en alerte ops.

    Indispensable car les jobs attrapent leurs exceptions (logger.exception)
    — un listener APScheduler seul ne verrait rien. La dédup de send_alert
    protège contre le spam des crashloops.
    """

    _IGNORED = ("app.alerts", "app.notifications")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(self._IGNORED):
                return
            msg = record.getMessage()
            alert_bg(f"🔴 [Ev0 worker] {record.name}\n{msg[:500]}", channel="ops")
        except Exception:  # jamais de récursion / crash depuis le handler
            pass
