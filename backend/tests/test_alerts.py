"""Tests du canal d'alertes multi-provider (dédup, formatage, robustesse)."""
import asyncio

import pytest

from app import alerts
from app.alerts import _html_to_whatsapp, send_alert


def test_html_to_whatsapp_formatting():
    assert _html_to_whatsapp("<b>VALUE BET</b>\ncote &amp; edge") == "*VALUE BET*\ncote & edge"
    assert _html_to_whatsapp("<i>x</i> <span>y</span>") == "_x_ y"


@pytest.mark.asyncio
async def test_send_alert_never_raises(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("réseau mort")
    monkeypatch.setattr(alerts, "_send_whatsapp", boom)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "+33600000000")
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "k")
    monkeypatch.setattr(alerts.settings, "telegram_bot_token", "", raising=False)
    # ne doit JAMAIS lever, juste retourner False
    assert await send_alert("test-crash-unique-1") is False


@pytest.mark.asyncio
async def test_send_alert_dedup(monkeypatch):
    calls = []
    async def fake_send(phone, key, text):
        calls.append(text)
        return True
    monkeypatch.setattr(alerts, "_send_whatsapp", fake_send)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "+33600000000")
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "k")
    alerts._last_sent.clear()
    alerts._last_channel_send.clear()
    assert await send_alert("msg-dedup-test") is True
    # même message dans la fenêtre → ignoré sans envoi
    assert await send_alert("msg-dedup-test") is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_channels_fall_back_to_ops_config(monkeypatch):
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "+336OPS")
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "kOPS")
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_phone", "")
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_apikey", "")
    assert alerts._channel_conf("recos") == ("+336OPS", "kOPS")
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_phone", "+336RECO")
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_apikey", "kR")
    assert alerts._channel_conf("recos") == ("+336RECO", "kR")
