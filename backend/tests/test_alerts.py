"""Tests du canal d'alertes multi-provider (routage, dédup, formatage, robustesse)."""
import pytest

from app import alerts, notifications
from app.alerts import _html_to_whatsapp, send_alert


def _reset_rate_limit() -> None:
    """Évite qu'un test précédent impose l'espacement de 20 s par canal."""
    alerts._last_sent.clear()
    alerts._last_channel_send.clear()


def test_html_to_whatsapp_formatting():
    assert _html_to_whatsapp("<b>VALUE BET</b>\ncote &amp; edge") == "*VALUE BET*\ncote & edge"
    assert _html_to_whatsapp("<i>x</i> <span>y</span>") == "_x_ y"


# ── Routage des trois canaux ──────────────────────────────────────


async def test_each_channel_uses_its_own_chat_id(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "HIST", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_value", "V", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_incidents", "I", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_autopilot", "A", raising=False)

    seen: list[tuple[str, str]] = []

    async def fake_post(token, chat_id, text):
        seen.append((chat_id, text))
        return True

    monkeypatch.setattr(notifications, "_post", fake_post)

    for channel, _expected in (("value", "V"), ("incidents", "I"), ("autopilot", "A")):
        assert await notifications.send_telegram_alert("msg", channel) is True
    assert [c for c, _ in seen] == ["V", "I", "A"]
    assert all(not t.startswith("[") for _, t in seen)


async def test_missing_chat_id_falls_back_to_historic_chat_with_prefix(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "HIST", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_value", "", raising=False)

    seen: list[tuple[str, str]] = []

    async def fake_post(token, chat_id, text):
        seen.append((chat_id, text))
        return True

    monkeypatch.setattr(notifications, "_post", fake_post)

    assert await notifications.send_telegram_alert("msg", "value") is True
    assert seen == [("HIST", "[value] msg")]


async def test_telegram_refusal_falls_back_to_historic_chat(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "HIST", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_incidents", "I", raising=False)

    seen: list[tuple[str, str]] = []

    async def fake_post(token, chat_id, text):
        seen.append((chat_id, text))
        return chat_id == "HIST"

    monkeypatch.setattr(notifications, "_post", fake_post)

    assert await notifications.send_telegram_alert("boom", "incidents") is True
    assert seen == [("I", "boom"), ("HIST", "[incidents] boom")]


async def test_no_fallback_chat_returns_false_and_never_raises(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "", raising=False)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_value", "", raising=False)

    assert await notifications.send_telegram_alert("perdu", "value") is False


async def test_unknown_channel_raises(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T", raising=False)
    with pytest.raises(ValueError):
        await notifications.send_telegram_alert("msg", "ops")


# ── Secours WhatsApp par canal ────────────────────────────────────


def test_autopilot_has_no_whatsapp_fallback(monkeypatch):
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "0600", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "K", raising=False)
    assert alerts._channel_conf("autopilot") == ("", "")
    assert alerts._channel_conf("incidents") == ("0600", "K")


def test_value_uses_recos_whatsapp_then_ops(monkeypatch):
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "0600", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "KOPS", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_phone", "0611", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_apikey", "KREC", raising=False)
    assert alerts._channel_conf("value") == ("0611", "KREC")

    monkeypatch.setattr(alerts.settings, "whatsapp_recos_phone", "", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_apikey", "", raising=False)
    assert alerts._channel_conf("value") == ("0600", "KOPS")


# ── Robustesse de send_alert ──────────────────────────────────────


async def test_send_alert_never_raises(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("réseau mort")
    monkeypatch.setattr(alerts, "_send_whatsapp", boom)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "+33600000000", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "k", raising=False)
    monkeypatch.setattr(alerts.settings, "telegram_bot_token", "", raising=False)
    _reset_rate_limit()
    # ne doit JAMAIS lever, juste retourner False
    assert await send_alert("test-crash-unique-1", channel="incidents") is False


async def test_send_alert_dedup(monkeypatch):
    calls = []

    async def fake_send(phone, key, text):
        calls.append(text)
        return True

    monkeypatch.setattr(alerts, "_send_whatsapp", fake_send)
    monkeypatch.setattr(alerts.settings, "telegram_bot_token", "", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "+33600000000", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "k", raising=False)
    _reset_rate_limit()
    assert await send_alert("msg-dedup-test", channel="incidents") is True
    # même message dans la fenêtre → ignoré sans envoi
    assert await send_alert("msg-dedup-test", channel="incidents") is False
    assert len(calls) == 1


async def test_send_alert_requires_explicit_channel():
    """Plus de canal par défaut : un appel sans canal doit échouer bruyamment."""
    with pytest.raises(TypeError):
        await send_alert("sans canal")


# ── Priorité Telegram / WhatsApp ──────────────────────────────────


async def test_whatsapp_success_detection_ignores_echoed_error_words(monkeypatch):
    """CallMeBot échoe le texte du message : un message contenant 'error' ne
    doit pas être compté en échec si la réponse dit 'Message queued'."""
    class FakeResp:
        status_code = 200
        text = "Message to: +336… Text: job failed TimeoutError… Message queued. You will receive it in a few seconds."

        def raise_for_status(self):
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(alerts.httpx, "AsyncClient", FakeClient)
    assert await alerts._send_whatsapp("+336", "k", "job failed TimeoutError") is True


async def test_whatsapp_rejects_when_not_queued(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "APIKey is invalid"

        def raise_for_status(self):
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(alerts.httpx, "AsyncClient", FakeClient)
    assert await alerts._send_whatsapp("+336", "k", "test") is False


async def test_telegram_is_primary_when_token_set(monkeypatch):
    """Token Telegram présent → Telegram utilisé, WhatsApp PAS appelé."""
    wa_calls = []

    async def fake_wa(phone, key, text):
        wa_calls.append(text)
        return True

    tg_calls = []

    async def fake_tg(msg, channel):
        tg_calls.append((msg, channel))
        return True

    monkeypatch.setattr(alerts, "_send_whatsapp", fake_wa)
    monkeypatch.setattr(alerts.settings, "telegram_bot_token", "tok", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "+336", raising=False)
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "k", raising=False)
    monkeypatch.setattr(notifications, "send_telegram_alert", fake_tg)
    _reset_rate_limit()
    assert await alerts.send_alert("via-telegram-primaire", channel="value") is True
    assert tg_calls == [("via-telegram-primaire", "value")]
    assert wa_calls == []  # WhatsApp jamais tenté
