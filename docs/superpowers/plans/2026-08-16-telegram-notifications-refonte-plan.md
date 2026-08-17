# Refonte des notifications Telegram — plan d'implémentation

> **Pour les workers agentiques :** SUB-SKILL requise — `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Les étapes utilisent des cases `- [ ]`.

**Goal :** faire vibrer le téléphone uniquement sur une opportunité de marché ou une panne, en répartissant les notifications sur trois groupes Telegram distincts.

**Architecture :** un dictionnaire canal → `chat_id` dans `notifications.py` remplace le `chat_id` unique ; `send_alert` exige désormais un canal explicite ; les alertes value passent d'un déclenchement calendaire à un déclenchement de marché (« nouveau plus haut » mémorisé dans `recommendations.alerted_odds`) ; les rafales sont groupées en un message par cycle.

**Tech Stack :** Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, APScheduler, httpx, pytest + pytest-asyncio (`asyncio_mode = "auto"`).

## Global Constraints

- **Dépendance de branche :** ne démarrer qu'une fois `feat/transfermarkt-squad-sync` mergée dans `main`. Brancher depuis `main` à jour. La migration 049 lui appartient ; la nôtre est la **050**, `down_revision = "049"`.
- Toutes les commandes de test se lancent depuis `backend/` : `cd backend && uv run pytest …`.
- `asyncio_mode = "auto"` : les tests `async def` n'ont pas besoin de `@pytest.mark.asyncio`.
- **Aucun échec silencieux.** Toute alerte qui ne peut pas atteindre son canal part sur le chat historique avec un log `WARNING`.
- Les trois canaux sont exactement `"value"`, `"incidents"`, `"autopilot"`. Aucun autre nom, aucun canal par défaut.
- Les messages Telegram partent en `parse_mode="HTML"` : n'insérer que `<b>`/`<i>`, et échapper `&` en `&amp;`.
- Lignes de code citées : relevées sur `main` au 2026-08-16, **avant** le merge Transfermarkt qui modifie `worker.py`. Les retrouver par recherche de motif, pas par numéro.

## Structure des fichiers

| Fichier | Responsabilité | Tâches |
|---|---|---|
| `backend/app/config.py` | trois nouveaux `chat_id` | 1 |
| `backend/app/notifications.py` | transport Telegram + mise en forme des messages | 1, 4, 6 |
| `backend/app/alerts.py` | routage, dédup, rythme, secours WhatsApp, plafond erreurs | 1, 2 |
| `backend/app/worker.py` | points d'appel des jobs | 1, 5, 6, 7 |
| `backend/app/services/recommendation_service.py` | détection des mouvements de cote | 4 |
| `backend/app/models/recommendations.py` | colonne `alerted_odds` | 3 |
| `backend/alembic/versions/050_recommendation_alerted_odds.py` | migration | 3 |
| `backend/tests/test_alerts.py` | routage, secours, plafond | 1, 2 |
| `backend/tests/test_notifications_format.py` | mise en forme des messages groupés | 4, 6 |
| `backend/tests/services/test_value_movements.py` | règle du nouveau plus haut | 4 |
| `backend/tests/test_worker_health_report.py` | bascule santé verte/rouge | 7 |

---

### Task 1 : Trois canaux et routage complet

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/notifications.py`
- Modify: `backend/app/alerts.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/services/recommendation_service.py`
- Test: `backend/tests/test_alerts.py`

**Interfaces:**
- Consumes: rien (première tâche).
- Produces :
  - `notifications.CHANNELS: tuple[str, str, str]` = `("value", "incidents", "autopilot")`
  - `async notifications.send_telegram_alert(message: str, channel: str) -> bool`
  - `async alerts.send_alert(message: str, channel: str) -> bool` — `channel` obligatoire
  - `alerts.alert_bg(message: str, channel: str) -> None` — `channel` obligatoire

**Contexte.** Aujourd'hui `send_telegram_alert(message)` poste sur `settings.telegram_chat_id` et ignore le canal ; `send_alert(message, channel="ops")` a un défaut implicite. Cette tâche supprime les deux, et recâble les onze points d'appel d'un coup — sinon le dépôt est cassé entre deux tâches.

- [ ] **Étape 1 : écrire les tests qui échouent**

Ajouter dans `backend/tests/test_alerts.py` :

```python
import pytest

from app import alerts, notifications


async def test_each_channel_uses_its_own_chat_id(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "HIST")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_value", "V")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_incidents", "I")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_autopilot", "A")

    seen: list[tuple[str, str]] = []

    async def fake_post(token, chat_id, text):
        seen.append((chat_id, text))
        return True

    monkeypatch.setattr(notifications, "_post", fake_post)

    for channel, expected in (("value", "V"), ("incidents", "I"), ("autopilot", "A")):
        assert await notifications.send_telegram_alert("msg", channel) is True
    assert [c for c, _ in seen] == ["V", "I", "A"]
    assert all(not t.startswith("[") for _, t in seen)


async def test_missing_chat_id_falls_back_to_historic_chat_with_prefix(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "HIST")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_value", "")

    seen: list[tuple[str, str]] = []

    async def fake_post(token, chat_id, text):
        seen.append((chat_id, text))
        return True

    monkeypatch.setattr(notifications, "_post", fake_post)

    assert await notifications.send_telegram_alert("msg", "value") is True
    assert seen == [("HIST", "[value] msg")]


async def test_telegram_refusal_falls_back_to_historic_chat(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "HIST")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id_incidents", "I")

    seen: list[tuple[str, str]] = []

    async def fake_post(token, chat_id, text):
        seen.append((chat_id, text))
        return chat_id == "HIST"

    monkeypatch.setattr(notifications, "_post", fake_post)

    assert await notifications.send_telegram_alert("boom", "incidents") is True
    assert seen == [("I", "boom"), ("HIST", "[incidents] boom")]


async def test_unknown_channel_raises(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "T")
    with pytest.raises(ValueError):
        await notifications.send_telegram_alert("msg", "ops")


async def test_autopilot_has_no_whatsapp_fallback(monkeypatch):
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "0600")
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "K")
    assert alerts._channel_conf("autopilot") == ("", "")
    assert alerts._channel_conf("incidents") == ("0600", "K")


async def test_value_uses_recos_whatsapp_then_ops(monkeypatch):
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_phone", "0600")
    monkeypatch.setattr(alerts.settings, "whatsapp_ops_apikey", "KOPS")
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_phone", "0611")
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_apikey", "KREC")
    assert alerts._channel_conf("value") == ("0611", "KREC")

    monkeypatch.setattr(alerts.settings, "whatsapp_recos_phone", "")
    monkeypatch.setattr(alerts.settings, "whatsapp_recos_apikey", "")
    assert alerts._channel_conf("value") == ("0600", "KOPS")
```

Supprimer le test devenu faux `test_channels_fall_back_to_ops_config` (il valide l'ancienne notion de canal `recos`/`ops`), et adapter `test_telegram_is_primary_when_token_set`, `test_send_alert_never_raises`, `test_send_alert_dedup` en leur passant un canal explicite (`channel="incidents"`).

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/test_alerts.py -v
```
Attendu : ÉCHEC (`AttributeError: telegram_chat_id_value`, `_post` inexistant).

- [ ] **Étape 3 : ajouter les trois réglages**

Dans `backend/app/config.py`, sous `telegram_chat_id` :

```python
    # Un groupe Telegram par canal. Vide => repli sur telegram_chat_id (chat historique).
    telegram_chat_id_value: str = ""
    telegram_chat_id_incidents: str = ""
    telegram_chat_id_autopilot: str = ""
```

- [ ] **Étape 4 : réécrire le transport Telegram**

Dans `backend/app/notifications.py`, remplacer la constante `TELEGRAM_CHAT_ID` et la fonction `send_telegram_alert` par :

```python
CHANNELS = ("value", "incidents", "autopilot")


def _chat_id_for(channel: str) -> str:
    return {
        "value": settings.telegram_chat_id_value,
        "incidents": settings.telegram_chat_id_incidents,
        "autopilot": settings.telegram_chat_id_autopilot,
    }[channel]


async def _post(token: str, chat_id: str, text: str) -> bool:
    """Un envoi Telegram. Ne lève jamais ; retourne True si accepté."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.warning("Telegram sendMessage échoué (chat_id=%s): %s", chat_id, exc)
        return False


async def send_telegram_alert(message: str, channel: str) -> bool:
    """Envoie sur le groupe du canal. Repli sur le chat historique, jamais en silence."""
    if channel not in CHANNELS:
        raise ValueError(f"canal Telegram inconnu: {channel!r}")

    token = getattr(settings, "telegram_bot_token", None)
    if not token:
        logger.debug("TELEGRAM_BOT_TOKEN absent — notification ignorée")
        return False

    chat_id = _chat_id_for(channel)
    if chat_id:
        if await _post(token, chat_id, message):
            return True
        logger.warning(
            "Canal '%s' injoignable (chat_id=%s) — repli sur le chat historique", channel, chat_id
        )
    else:
        logger.warning(
            "Canal '%s' non configuré (TELEGRAM_CHAT_ID_%s vide) — repli sur le chat historique",
            channel, channel.upper(),
        )

    fallback = settings.telegram_chat_id
    if not fallback:
        logger.error("Aucun chat de repli configuré — alerte '%s' PERDUE", channel)
        return False
    return await _post(token, fallback, f"[{channel}] {message}")
```

- [ ] **Étape 5 : adapter le routage dans `alerts.py`**

Remplacer `_channel_conf` :

```python
def _channel_conf(channel: str) -> tuple[str, str]:
    """Coordonnées WhatsApp de secours du canal.

    `autopilot` n'en a volontairement aucune : une panne Telegram ne doit pas
    déverser le flot autopilot sur WhatsApp.
    """
    if channel == "autopilot":
        return "", ""
    if channel == "value":
        phone = settings.whatsapp_recos_phone or settings.whatsapp_ops_phone
        key = settings.whatsapp_recos_apikey or settings.whatsapp_ops_apikey
        return phone or "", key or ""
    return settings.whatsapp_ops_phone or "", settings.whatsapp_ops_apikey or ""
```

Retirer les valeurs par défaut de `send_alert` et `alert_bg` (`channel: str` sans `= "ops"`), et propager le canal à Telegram :

```python
            sent = await send_telegram_alert(message, channel)
```

Mettre à jour le docstring de tête du module : les deux canaux `ops`/`recos` deviennent les trois `value`/`incidents`/`autopilot`.

- [ ] **Étape 6 : recâbler les onze points d'appel**

Chaque site est retrouvé par recherche de motif, pas par numéro de ligne.

| Motif à chercher | Fichier | Canal à poser |
|---|---|---|
| `🎯 <b>VALUE BET</b>` | `services/recommendation_service.py` | `"value"` |
| `⏳ <b>[Ev0] {len(rows)} reco(s) expirent` | `worker.py` | `"value"` *(supprimé en Task 5)* |
| `notify_autopilot_position` → `send_alert(msg, channel=` | `notifications.py` | `"autopilot"` |
| `notify_autopilot_fine_tune` → `send_alert(msg, channel=` | `notifications.py` | `"autopilot"` |
| `notify_autopilot_settle` → `send_alert(msg, channel=` | `notifications.py` | `"autopilot"` |
| `✅ <b>[Ev0] Settlement automatique</b>` | `worker.py` | `"autopilot"` |
| `⏱️ <b>[Ev0] Auto-finish fixtures</b>` | `worker.py` | `"autopilot"` |
| `🚨 <b>[Ev0] Settlement bloqué</b>` | `worker.py` | `"incidents"` |
| `🩺 <b>[Ev0] Santé quotidienne</b>` | `worker.py` | `"incidents"` *(conditionnel en Task 7)* |
| `🔴 [Ev0 worker] Job '` | `worker.py` | `"incidents"` |
| `🔴 [Ev0 worker] {record.name}` | `alerts.py` | `"incidents"` |

Les deux appels `send_alert(...)` de `worker.py` qui n'ont **aucun** argument `channel` (settlement automatique, auto-finish, settlement bloqué) doivent en recevoir un — sans quoi Python lèvera `TypeError` au démarrage du job.

- [ ] **Étape 7 : vérifier qu'aucun ancien canal ne subsiste**

```bash
cd backend && grep -rn --include='*.py' -E 'channel="(ops|recos)"|channel: str = "ops"' app tests
```
Attendu : aucune sortie.

- [ ] **Étape 8 : lancer les tests**

```bash
cd backend && uv run pytest tests/test_alerts.py -v
```
Attendu : SUCCÈS.

```bash
cd backend && uv run pytest tests/ -q
```
Attendu : aucune régression (les tests qui appelaient `send_alert` sans canal ont été adaptés à l'étape 1).

- [ ] **Étape 9 : commit**

```bash
git add backend/app/config.py backend/app/notifications.py backend/app/alerts.py \
        backend/app/worker.py backend/app/services/recommendation_service.py \
        backend/tests/test_alerts.py
git commit -m "feat(alerts): trois canaux Telegram (value/incidents/autopilot) + repli sans silence"
```

---

### Task 2 : Plafond anti-crashloop sur les alertes d'erreur

**Files:**
- Modify: `backend/app/alerts.py` (classe `ErrorAlertHandler`)
- Test: `backend/tests/test_alerts.py`

**Interfaces:**
- Consumes: `alerts.alert_bg(message, channel)` (Task 1).
- Produces: `ErrorAlertHandler` avec attributs de classe `_CAP = 3`, `_WINDOW_S = 3600`.

**Contexte.** `ErrorAlertHandler` relaie tout log `ERROR` du worker. La déduplication existante ne couvre que 15 minutes et ne protège pas d'un bug dont le message varie (numéro de fixture, exception différente) : un crashloop peut sonner en continu sur le canal `incidents`.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
import logging
import time

from app.alerts import ErrorAlertHandler


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("app.worker", logging.ERROR, __file__, 1, msg, None, None)


def test_error_handler_caps_at_three_per_hour(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.alerts.alert_bg", lambda message, channel: sent.append((message, channel))
    )

    handler = ErrorAlertHandler(level=logging.ERROR)
    for i in range(3):
        handler.emit(_record(f"boom {i}"))

    assert len(sent) == 3
    assert all(channel == "incidents" for _, channel in sent)
    assert all(m.startswith("🔴") for m, _ in sent)

    handler.emit(_record("boom 4"))
    assert len(sent) == 4
    assert sent[-1][0].startswith("⚠️")
    assert "erreur" in sent[-1][0]

    handler.emit(_record("boom 5"))
    assert len(sent) == 4, "la 5e erreur est absorbée, pas de 2e synthèse dans l'heure"


def test_error_handler_budget_resets_after_window(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.alerts.alert_bg", lambda message, channel: sent.append((message, channel))
    )
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    handler = ErrorAlertHandler(level=logging.ERROR)
    for i in range(4):
        handler.emit(_record(f"boom {i}"))
    assert len(sent) == 4

    clock["t"] += 3601
    handler.emit(_record("nouvelle heure"))
    assert len(sent) == 5
    assert sent[-1][0].startswith("🔴")


def test_error_handler_ignores_its_own_modules(monkeypatch):
    sent: list = []
    monkeypatch.setattr("app.alerts.alert_bg", lambda message, channel: sent.append(message))
    handler = ErrorAlertHandler(level=logging.ERROR)
    handler.emit(
        logging.LogRecord("app.alerts", logging.ERROR, __file__, 1, "récursion", None, None)
    )
    assert sent == []
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/test_alerts.py -k error_handler -v
```
Attendu : ÉCHEC (la 4ᵉ erreur produit aujourd'hui une 4ᵉ alerte `🔴`, pas une synthèse).

- [ ] **Étape 3 : implémenter le plafond**

Remplacer `ErrorAlertHandler` dans `backend/app/alerts.py` :

```python
class ErrorAlertHandler(logging.Handler):
    """Handler de logs : tout record >= ERROR du worker part sur `incidents`.

    Indispensable car les jobs attrapent leurs exceptions (logger.exception)
    — un listener APScheduler seul ne verrait rien.

    Plafond anti-crashloop : au-delà de `_CAP` alertes dans une fenêtre
    glissante de `_WINDOW_S`, les erreurs sont absorbées et remplacées par un
    unique message de synthèse (au plus un par fenêtre). La dédup de
    `send_alert` ne suffit pas : elle ne couvre que 15 min et un bug dont le
    message varie y échappe entièrement.
    """

    _IGNORED = ("app.alerts", "app.notifications")
    _CAP = 3
    _WINDOW_S = 3600

    def __init__(self, level: int = logging.ERROR) -> None:
        super().__init__(level)
        self._sent_at: list[float] = []
        self._suppressed = 0
        self._summary_at = float("-inf")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(self._IGNORED):
                return

            now = time.monotonic()
            self._sent_at = [t for t in self._sent_at if now - t < self._WINDOW_S]

            if len(self._sent_at) < self._CAP:
                self._sent_at.append(now)
                alert_bg(
                    f"🔴 [Ev0 worker] {record.name}\n{record.getMessage()[:500]}",
                    channel="incidents",
                )
                return

            self._suppressed += 1
            if now - self._summary_at >= self._WINDOW_S:
                self._summary_at = now
                count, self._suppressed = self._suppressed, 0
                alert_bg(
                    f"⚠️ [Ev0 worker] {count} erreur(s) supprimée(s) — "
                    f"cadence trop élevée, voir les logs du worker",
                    channel="incidents",
                )
        except Exception:  # jamais de récursion / crash depuis le handler
            pass
```

Vérifier que `import time` est bien présent en tête de `alerts.py` (il l'est déjà, utilisé par la dédup).

- [ ] **Étape 4 : lancer les tests**

```bash
cd backend && uv run pytest tests/test_alerts.py -v
```
Attendu : SUCCÈS.

- [ ] **Étape 5 : commit**

```bash
git add backend/app/alerts.py backend/tests/test_alerts.py
git commit -m "feat(alerts): plafond anti-crashloop sur les alertes d'erreur (3/h + synthese)"
```

---

### Task 3 : Colonne `alerted_odds` et migration 050

**Files:**
- Create: `backend/alembic/versions/050_recommendation_alerted_odds.py`
- Modify: `backend/app/models/recommendations.py`
- Test: `backend/tests/test_alerted_odds_migration.py`

**Interfaces:**
- Consumes: rien.
- Produces: `Recommendation.alerted_odds: Mapped[float | None]` — cote sur laquelle l'utilisateur a été alerté la dernière fois ; `None` = jamais alerté.

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `backend/tests/test_alerted_odds_migration.py` :

```python
from app.models.recommendations import Recommendation


def test_recommendation_has_alerted_odds_column():
    col = Recommendation.__table__.columns.get("alerted_odds")
    assert col is not None, "colonne alerted_odds absente du modèle"
    assert col.nullable is True


def test_migration_050_follows_049():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "050_recommendation_alerted_odds.py"
    spec = importlib.util.spec_from_file_location("m050", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "050"
    assert module.down_revision == "049"
    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
```

- [ ] **Étape 2 : lancer le test, vérifier qu'il échoue**

```bash
cd backend && uv run pytest tests/test_alerted_odds_migration.py -v
```
Attendu : ÉCHEC (`colonne alerted_odds absente` puis `FileNotFoundError`).

- [ ] **Étape 3 : écrire la migration**

Créer `backend/alembic/versions/050_recommendation_alerted_odds.py` :

```python
"""Add recommendations.alerted_odds (memoire du dernier plus haut notifie).

Revision ID: 050
Revises: 049
Create Date: 2026-08-16
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "050"
down_revision: str | None = "049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("alerted_odds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendations", "alerted_odds")
```

Pas de backfill : les recos existantes restent à `NULL` et se comporteront comme des nouvelles values au premier passage.

- [ ] **Étape 4 : ajouter la colonne au modèle**

Dans `backend/app/models/recommendations.py`, juste après `best_odds` :

```python
    # Cote sur laquelle l'utilisateur a été alerté la dernière fois.
    # Jamais abaissée : seul un nouveau plus haut redéclenche une notification.
    alerted_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
```

- [ ] **Étape 5 : lancer les tests**

```bash
cd backend && uv run pytest tests/test_alerted_odds_migration.py -v
```
Attendu : SUCCÈS.

- [ ] **Étape 6 : commit**

```bash
git add backend/alembic/versions/050_recommendation_alerted_odds.py \
        backend/app/models/recommendations.py backend/tests/test_alerted_odds_migration.py
git commit -m "feat(db): migration 050 — recommendations.alerted_odds"
```

---

### Task 4 : Mouvements de cote — nouveau plus haut, groupés en un message

**Files:**
- Modify: `backend/app/services/recommendation_service.py` (fonction `process_scraped_fixtures`)
- Modify: `backend/app/notifications.py`
- Test: `backend/tests/services/test_value_movements.py`
- Test: `backend/tests/test_notifications_format.py`

**Interfaces:**
- Consumes: `Recommendation.alerted_odds` (Task 3), `alerts.send_alert(message, channel)` (Task 1).
- Produces :
  - `recommendation_service.ALERT_RISE_RATIO: float` = `1.05`
  - `recommendation_service.should_alert(alerted_odds: float | None, market_odds: float) -> bool`
  - `async notifications.notify_value_movements(movements: list[dict]) -> None`
  - Forme d'un `movement` : `{"kind": "new" | "rise", "player": str, "fixture": str, "kickoff": str, "bet_type": str, "odds": float, "previous_odds": float | None, "bookmaker": str, "edge": float, "previous_edge": float | None}`

**Contexte.** `process_scraped_fixtures` est appelée après **chaque** cycle de scraping (`job_odds_scheduler_tick`, toutes les 60 s). Elle construit aujourd'hui `new_value_alerts` uniquement à la création d'une reco, et envoie **une notification par élément** en fin de fonction. Les mises à jour de cote sur recos existantes sont silencieuses. Cette tâche fait les deux : ajouter le déclencheur « nouveau plus haut », et grouper en un unique message par cycle.

- [ ] **Étape 1 : écrire les tests de la règle**

Créer `backend/tests/services/test_value_movements.py` :

```python
from app.services.recommendation_service import ALERT_RISE_RATIO, should_alert


def test_never_alerted_always_alerts():
    assert should_alert(None, 2.50) is True


def test_small_rise_stays_silent():
    # 2.50 -> 2.57 = +2.8 %, sous le seuil
    assert should_alert(2.50, 2.57) is False


def test_rise_above_threshold_alerts():
    # Seuil = 2.50 * 1.05 = 2.625. On teste de part et d'autre SANS toucher la
    # valeur exacte : 2.5 * 1.05 vaut 2.6250000000000004 en binaire, une
    # comparaison sur la borne serait un test fragile.
    assert should_alert(2.50, 2.62) is False
    assert should_alert(2.50, 2.63) is True
    assert should_alert(2.50, 2.70) is True


def test_drop_stays_silent():
    assert should_alert(2.50, 2.10) is False


def test_ratio_is_five_percent():
    assert ALERT_RISE_RATIO == 1.05


def test_yoyo_only_alerts_once_when_level_never_beaten():
    """2.50 (alerté) -> 2.60 -> 2.45 -> 2.58 : aucun nouveau plus haut suffisant."""
    alerted = 2.50
    for odds in (2.60, 2.45, 2.58):
        assert should_alert(alerted, odds) is False


def test_staircase_alerts_at_each_new_high():
    """Une montée franche redéclenche à chaque palier franchi."""
    alerted = 2.50
    fired = []
    for odds in (2.55, 2.70, 2.80, 3.00):
        if should_alert(alerted, odds):
            fired.append(odds)
            alerted = odds
    assert fired == [2.70, 3.00]
```

- [ ] **Étape 2 : écrire les tests de mise en forme**

Créer `backend/tests/test_notifications_format.py` :

```python
from app import notifications


async def test_no_movements_sends_nothing(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append((message, channel))
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_value_movements([])
    assert sent == []


async def test_movements_are_grouped_into_one_message(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append((message, channel))
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)

    await notifications.notify_value_movements([
        {
            "kind": "new", "player": "Dembélé", "fixture": "PSG vs Marseille",
            "kickoff": "21:00", "bet_type": "goal", "odds": 2.85,
            "previous_odds": None, "bookmaker": "Betclic",
            "edge": 0.124, "previous_edge": None,
        },
        {
            "kind": "rise", "player": "Mbappé", "fixture": "Real vs Girona",
            "kickoff": "21:00", "bet_type": "goal", "odds": 3.40,
            "previous_odds": 3.10, "bookmaker": "Unibet",
            "edge": 0.142, "previous_edge": 0.081,
        },
    ])

    assert len(sent) == 1, "un seul message par cycle, pas un par mouvement"
    message, channel = sent[0]
    assert channel == "value"
    assert "2 mouvements" in message
    assert "NOUVELLE VALUE" in message
    assert "Dembélé" in message and "Buteur" in message
    assert "3.10 → 3.40" in message
    assert "+8.1% → +14.2%" in message


async def test_single_movement_is_singular(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append(message)
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_value_movements([{
        "kind": "new", "player": "Saka", "fixture": "Arsenal vs Chelsea",
        "kickoff": "18:30", "bet_type": "assist", "odds": 4.20,
        "previous_odds": None, "bookmaker": "Betclic",
        "edge": 0.06, "previous_edge": None,
    }])
    assert "1 mouvement" in sent[0]
    assert "2 mouvements" not in sent[0]
    assert "Passeur" in sent[0]
```

- [ ] **Étape 3 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/services/test_value_movements.py tests/test_notifications_format.py -v
```
Attendu : ÉCHEC (`ImportError: cannot import name 'should_alert'`, `notify_value_movements` inexistante).

- [ ] **Étape 4 : implémenter la mise en forme**

Dans `backend/app/notifications.py`, ajouter :

```python
_BET_LABELS = {"goal": "Buteur", "assist": "Passeur"}


def _format_movement(m: dict) -> str:
    label = _BET_LABELS.get(m["bet_type"], m["bet_type"])
    head = "▲ NOUVELLE VALUE\n" if m["kind"] == "new" else "▲ "
    name_line = f"<b>{m['player']}</b> — {label}"
    fixture_line = f"{m['fixture']} ({m['kickoff']})"

    if m["kind"] == "new":
        odds_line = f"{m['odds']:.2f} {m['bookmaker']} | edge {m['edge']:+.1%}"
    else:
        odds_line = (
            f"{m['previous_odds']:.2f} → {m['odds']:.2f} {m['bookmaker']} | "
            f"edge {m['previous_edge']:+.1%} → {m['edge']:+.1%}"
        )

    return f"{head}{name_line}\n{fixture_line}\n{odds_line}"


async def notify_value_movements(movements: list[dict]) -> None:
    """Un seul message par cycle de scraping. Rien si le cycle est vide."""
    if not movements:
        return

    n = len(movements)
    header = f"🎯 <b>{n} mouvement{'s' if n > 1 else ''}</b>"
    body = "\n\n".join(_format_movement(m) for m in movements)

    from app.alerts import send_alert

    await send_alert(f"{header}\n\n{body}", channel="value")
```

- [ ] **Étape 5 : implémenter la règle du nouveau plus haut**

Dans `backend/app/services/recommendation_service.py`, au niveau module :

```python
# Une value déjà signalée ne resonne que si le marché dépasse de 5 % la cote
# sur laquelle l'utilisateur a été alerté. Évite le flood : le scraping
# réévalue toutes les recos toutes les 60 secondes.
ALERT_RISE_RATIO = 1.05


def should_alert(alerted_odds: float | None, market_odds: float) -> bool:
    """True si `market_odds` constitue un nouveau plus haut notifiable."""
    if alerted_odds is None:
        return True
    return market_odds >= alerted_odds * ALERT_RISE_RATIO
```

- [ ] **Étape 6 : câbler les trois branches dans `process_scraped_fixtures`**

Renommer la liste : `new_value_alerts: list[dict] = []` devient `movements: list[dict] = []`.

Ajouter un helper local juste après la boucle de chargement des fixtures :

```python
    def _record_movement(kind: str, rec, fixture_orm, bet_type: str,
                         market_odds: float, bookmaker: str, edge: float,
                         previous_odds: float | None, previous_edge: float | None) -> None:
        movements.append({
            "kind": kind,
            "player": rec.player_name,
            "fixture": f"{fixture_orm.home_team} vs {fixture_orm.away_team}",
            "kickoff": fixture_orm.kickoff_utc.strftime("%H:%M"),
            "bet_type": bet_type,
            "odds": market_odds,
            "previous_odds": previous_odds,
            "bookmaker": bookmaker,
            "edge": edge,
            "previous_edge": previous_edge,
        })
        rec.alerted_odds = market_odds
```

**Branche création** (`if existing_rec is None:`) — remplacer le bloc `new_value_alerts.append({...})` par :

```python
                        new_rec.alerted_odds = market_odds
                        _record_movement(
                            "new", new_rec, fixture_orm, bet_type,
                            market_odds, bookmaker, edge,
                            previous_odds=None, previous_edge=None,
                        )
```

**Branche résurrection** (`elif existing_rec.status == "expired":`) — remplacer le commentaire « SANS notification » et ajouter, après les affectations existantes :

```python
                        # Une résurrection ne notifie que si elle constitue un
                        # nouveau plus haut : une micro-variation autour du
                        # seuil n'est pas un signal actionnable.
                        if should_alert(existing_rec.alerted_odds, market_odds):
                            _record_movement(
                                "rise", existing_rec, fixture_orm, bet_type,
                                market_odds, bookmaker, edge,
                                previous_odds=_prev_odds, previous_edge=_prev_edge,
                            )
```

où `_prev_odds` / `_prev_edge` sont capturés **avant** les affectations :

```python
                        _prev_odds = existing_rec.alerted_odds or existing_rec.best_odds
                        _prev_edge = existing_rec.edge
```

**Branche mise à jour** (`else:` — pending/approved) — après les affectations `existing_rec.best_odds = …` déjà présentes, ajouter, en capturant les valeurs avant écrasement :

```python
                            _prev_odds = existing_rec.alerted_odds or existing_rec.best_odds
                            _prev_edge = existing_rec.edge
                            existing_rec.best_odds = market_odds
                            existing_rec.best_bookmaker = bookmaker
                            existing_rec.edge = edge
                            stats["updated"] += 1
                            if should_alert(existing_rec.alerted_odds, market_odds):
                                _record_movement(
                                    "rise", existing_rec, fixture_orm, bet_type,
                                    market_odds, bookmaker, edge,
                                    previous_odds=_prev_odds, previous_edge=_prev_edge,
                                )
```

**Envoi groupé** — remplacer la boucle finale `for alert in new_value_alerts:` par :

```python
    try:
        from app.notifications import notify_value_movements

        await notify_value_movements(movements)
    except Exception as exc:
        logger.warning("Envoi des mouvements value échoué: %s", exc)
```

Le `session.commit()` existant précède déjà cet envoi : `alerted_odds` est donc persistée avant notification.

- [ ] **Étape 7 : lancer les tests**

```bash
cd backend && uv run pytest tests/services/test_value_movements.py tests/test_notifications_format.py tests/services/test_h2h_recommendations.py -v
```
Attendu : SUCCÈS.

- [ ] **Étape 8 : commit**

```bash
git add backend/app/services/recommendation_service.py backend/app/notifications.py \
        backend/tests/services/test_value_movements.py backend/tests/test_notifications_format.py
git commit -m "feat(value): alerte sur nouveau plus haut (+5%) et groupement par cycle"
```

---

### Task 5 : Suppression du digest des recos expirantes

**Files:**
- Modify: `backend/app/worker.py` (supprimer `job_recos_expiry_digest` et son enregistrement)

**Interfaces:**
- Consumes: rien.
- Produces: rien.

**Contexte.** Ce job tourne toutes les 15 minutes pour annoncer les recos qui expireront dans ~2h. La fenêtre de disponibilité d'une value appartient aux bookmakers : l'utilisateur n'a aucune action à prendre sur une expiration. Le job `expire_recommendations` (nettoyage en base) est **conservé** — il ne notifie rien.

- [ ] **Étape 1 : supprimer la fonction**

Supprimer l'intégralité de `async def job_recos_expiry_digest(...)` dans `backend/app/worker.py` (repérable par la chaîne `⏳ <b>[Ev0] ` et `job_recos_expiry_digest: digest envoyé`).

- [ ] **Étape 2 : supprimer l'enregistrement du job**

Supprimer le bloc `scheduler.add_job(...)` portant `id="recos_expiry_digest"` (nom : `Digest WhatsApp des recos pending expirant dans ~2h`).

- [ ] **Étape 3 : vérifier qu'aucune référence ne subsiste**

```bash
cd backend && grep -rn --include='*.py' "recos_expiry_digest" app tests
```
Attendu : aucune sortie.

- [ ] **Étape 4 : vérifier que le worker démarre et que `expire_recommendations` survit**

```bash
cd backend && uv run python -c "
import app.worker as w
print('import worker OK')
" && grep -n 'id="expire_recommendations"' app/worker.py
```
Attendu : `import worker OK` puis une ligne trouvée pour `expire_recommendations`.

- [ ] **Étape 5 : lancer la suite complète**

```bash
cd backend && uv run pytest tests/ -q
```
Attendu : aucune régression.

- [ ] **Étape 6 : commit**

```bash
git add backend/app/worker.py
git commit -m "refactor(worker): suppression du digest des recos expirantes (non actionnable)"
```

---

### Task 6 : Positions autopilot groupées par run

**Files:**
- Modify: `backend/app/notifications.py` (remplacer `notify_autopilot_position`)
- Modify: `backend/app/worker.py` (boucle d'envoi du job autopilot)
- Test: `backend/tests/test_notifications_format.py`

**Interfaces:**
- Consumes: `alerts.send_alert(message, channel)` (Task 1), `notifications._scorecard(...)` (existante, inchangée).
- Produces: `async notifications.notify_autopilot_run(*, bets: list[dict], mode: str, settled: int, won: int, total_pnl: float, staked_total: float, fine_tune_runs: int, odds_api_remaining: int | None = None) -> None`
  - Forme d'un `bet` : `{"player_name": str, "fixture_name": str, "market_type": str, "best_odds": float, "edge": float, "stake": float, "action_idx": int}` — exactement ce que `worker.py` empile déjà dans `bets_this_run`.

**Contexte.** `worker.py` construit déjà une liste `bets_this_run`, puis boucle dessus en appelant `notify_autopilot_position` **une fois par pari**. Chaque message répète le scorecard complet à l'identique. Un run à 12 paris produit 12 notifications de 10 lignes.

- [ ] **Étape 1 : écrire les tests qui échouent**

Ajouter à `backend/tests/test_notifications_format.py` :

```python
_BETS = [
    {"player_name": "Dembélé", "fixture_name": "PSG vs OM", "market_type": "goalscorer",
     "best_odds": 2.85, "edge": 0.124, "stake": 12.5, "action_idx": 1},
    {"player_name": "Mbappé", "fixture_name": "Real vs Girona", "market_type": "goalscorer",
     "best_odds": 3.40, "edge": 0.142, "stake": 25.0, "action_idx": 2},
]

_SCORE = dict(settled=120, won=35, total_pnl=42.0, staked_total=400.0, fine_tune_runs=2)


async def test_autopilot_run_sends_one_message_with_one_scorecard(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append((message, channel))
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_autopilot_run(bets=_BETS, mode="paper", **_SCORE)

    assert len(sent) == 1
    message, channel = sent[0]
    assert channel == "autopilot"
    assert "2 positions" in message
    assert "Dembélé" in message and "Mbappé" in message
    assert message.count("Scorecard live") == 1, "scorecard affiché une seule fois"


async def test_autopilot_run_without_bets_sends_nothing(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append(message)
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_autopilot_run(bets=[], mode="paper", **_SCORE)
    assert sent == []


async def test_autopilot_run_singular_and_mode_tag(monkeypatch):
    sent: list = []

    async def fake_send(message, channel):
        sent.append(message)
        return True

    monkeypatch.setattr("app.alerts.send_alert", fake_send)
    await notifications.notify_autopilot_run(bets=_BETS[:1], mode="live", **_SCORE)
    assert "1 position" in sent[0]
    assert "2 positions" not in sent[0]
    assert "LIVE" in sent[0]
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/test_notifications_format.py -k autopilot_run -v
```
Attendu : ÉCHEC (`notify_autopilot_run` inexistante).

- [ ] **Étape 3 : remplacer la fonction**

Dans `backend/app/notifications.py`, supprimer `notify_autopilot_position` et ajouter :

```python
async def notify_autopilot_run(
    *,
    bets: list[dict],
    mode: str,
    settled: int,
    won: int,
    total_pnl: float,
    staked_total: float,
    fine_tune_runs: int,
    odds_api_remaining: int | None = None,
) -> None:
    """Un message par run d'autopilot, scorecard affiché une seule fois."""
    if not bets:
        return

    mode_tag = "PAPER" if mode == "paper" else "LIVE"
    n = len(bets)
    lines = [
        f"<b>[Autopilot {mode_tag}] {n} position{'s' if n > 1 else ''} prise{'s' if n > 1 else ''} 📌</b>",
        "",
    ]
    for bet in bets:
        market_label = (
            "Buteur" if bet["market_type"] == "goalscorer" else bet["market_type"].capitalize()
        )
        action_label = _ACTION_LABELS.get(bet["action_idx"], str(bet["action_idx"]))
        lines.append(f"• <b>{bet['player_name']}</b> — {market_label}")
        lines.append(f"  {bet['fixture_name']}")
        lines.append(
            f"  {bet['best_odds']:.2f} | edge {bet['edge']:+.1%} | "
            f"{action_label} → <b>€{bet['stake']:.2f}</b>"
        )

    msg = "\n".join(lines) + _scorecard(
        settled, won, total_pnl, staked_total, fine_tune_runs, odds_api_remaining
    )

    from app.alerts import send_alert

    await send_alert(msg, channel="autopilot")
```

- [ ] **Étape 4 : câbler le worker**

Dans `backend/app/worker.py`, remplacer l'import `from app.notifications import notify_autopilot_position` par `from app.notifications import notify_autopilot_run`, puis remplacer la boucle :

```python
            # Send one Telegram notification per bet taken
            for bet in bets_this_run:
                await notify_autopilot_position(
                    **bet,
                    mode=mode,
                    ...
                )
```

par :

```python
            # Un seul message pour tout le run (scorecard non répété)
            await notify_autopilot_run(
                bets=bets_this_run,
                mode=mode,
                settled=_sc_settled,
                won=_sc_won,
                total_pnl=_sc_total_pnl,
                staked_total=_sc_staked,
                fine_tune_runs=_sc_ft_runs,
            )
```

- [ ] **Étape 5 : vérifier qu'aucune référence à l'ancienne fonction ne subsiste**

```bash
cd backend && grep -rn --include='*.py' "notify_autopilot_position" app tests
```
Attendu : aucune sortie.

- [ ] **Étape 6 : lancer les tests**

```bash
cd backend && uv run pytest tests/test_notifications_format.py -v && uv run pytest tests/ -q
```
Attendu : SUCCÈS, aucune régression.

- [ ] **Étape 7 : commit**

```bash
git add backend/app/notifications.py backend/app/worker.py backend/tests/test_notifications_format.py
git commit -m "feat(autopilot): un seul message par run, scorecard non repete"
```

---

### Task 7 : Rapport de santé conditionnel

**Files:**
- Modify: `backend/app/worker.py` (fonction du job `daily_health_report`)
- Test: `backend/tests/test_worker_health_report.py`

**Interfaces:**
- Consumes: `alerts.send_alert(message, channel)` (Task 1).
- Produces: `worker._health_red_flags(row: dict, now: datetime) -> list[str]` — libellés des indicateurs au rouge, liste vide si tout va bien.

**Contexte.** Le rapport part tous les jours à 8h avec le même format, que tout aille bien ou non — donc il n'est plus lu le jour où il vire au rouge. Il doit désormais ne sonner (`incidents`) que si un seuil est franchi, et tomber sur `autopilot` sinon.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/test_worker_health_report.py` :

```python
from datetime import UTC, datetime, timedelta

from app.worker import _health_red_flags

NOW = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)


def _row(**over):
    base = {
        "last_player_odds": NOW - timedelta(minutes=30),
        "last_match_odds": NOW - timedelta(hours=2),
        "wc_odds_actives": 120,
        "wc_pricing_at": NOW - timedelta(hours=1),
        "recs_24h": 14,
        "recs_pending": 6,
        "backlog_settle": 3,
    }
    base.update(over)
    return base


def test_all_green_returns_no_flags():
    assert _health_red_flags(_row(), NOW) == []


def test_stale_player_odds_is_red():
    flags = _health_red_flags(_row(last_player_odds=NOW - timedelta(hours=25)), NOW)
    assert flags == ["cotes joueurs"]


def test_never_scraped_is_red():
    flags = _health_red_flags(_row(last_match_odds=None), NOW)
    assert flags == ["cotes matchs"]


def test_settle_backlog_over_twenty_is_red():
    assert _health_red_flags(_row(backlog_settle=21), NOW) == ["backlog settlement"]
    assert _health_red_flags(_row(backlog_settle=20), NOW) == []


def test_zero_recos_in_24h_is_red():
    assert _health_red_flags(_row(recs_24h=0), NOW) == ["aucune reco en 24h"]


def test_multiple_flags_accumulate():
    flags = _health_red_flags(
        _row(last_player_odds=None, recs_24h=0, backlog_settle=99), NOW
    )
    assert set(flags) == {"cotes joueurs", "backlog settlement", "aucune reco en 24h"}
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/test_worker_health_report.py -v
```
Attendu : ÉCHEC (`ImportError: cannot import name '_health_red_flags'`).

- [ ] **Étape 3 : implémenter le calcul des seuils**

Dans `backend/app/worker.py`, au niveau module, juste avant la fonction du job `daily_health_report` :

```python
_HEALTH_STALE_H = 24
_HEALTH_BACKLOG_MAX = 20


def _health_red_flags(row: dict, now: datetime) -> list[str]:
    """Indicateurs de santé au rouge. Liste vide = rien à signaler."""
    from datetime import timedelta

    stale = timedelta(hours=_HEALTH_STALE_H)
    flags: list[str] = []

    for key, label in (
        ("last_player_odds", "cotes joueurs"),
        ("last_match_odds", "cotes matchs"),
    ):
        ts = row[key]
        if ts is None or (now - ts) > stale:
            flags.append(label)

    if row["backlog_settle"] > _HEALTH_BACKLOG_MAX:
        flags.append("backlog settlement")
    if row["recs_24h"] == 0:
        flags.append("aucune reco en 24h")

    return flags
```

- [ ] **Étape 4 : brancher la bascule de canal**

Dans le job `daily_health_report`, remplacer la construction du message et l'envoi. Le bloc existant se termine par `msg = ("🩺 <b>[Ev0] Santé quotidienne</b>\n\n" ... )` suivi de `await send_alert(msg, channel="incidents")` (posé en Task 1). Remplacer par :

```python
        red = _health_red_flags(row, now)
        title = (
            f"🚨 <b>[Ev0] Santé — {', '.join(red)}</b>"
            if red
            else "🩺 <b>[Ev0] Santé quotidienne</b>"
        )
        msg = (
            f"{title}\n\n"
            f"Cotes joueurs : {age(row['last_player_odds'])}\n"
            f"Cotes matchs : {age(row['last_match_odds'])}\n"
            f"Outrights WC actifs : {row['wc_odds_actives']}\n"
            f"Pricing WC : {age(row['wc_pricing_at'])}\n"
            f"Recos 24h : {row['recs_24h']} (pending: {row['recs_pending']})\n"
            f"Backlog settle : {row['backlog_settle']} décision(s)"
        )
        await send_alert(msg, channel="incidents" if red else "autopilot")
```

`row` est un `RowMapping` : `_health_red_flags` y accède par clé, ce qui fonctionne aussi bien avec le `dict` des tests.

- [ ] **Étape 5 : lancer les tests**

```bash
cd backend && uv run pytest tests/test_worker_health_report.py -v && uv run pytest tests/ -q
```
Attendu : SUCCÈS, aucune régression.

- [ ] **Étape 6 : commit**

```bash
git add backend/app/worker.py backend/tests/test_worker_health_report.py
git commit -m "feat(sante): le rapport quotidien ne sonne que si un indicateur est rouge"
```

---

### Task 8 : Mise en service et documentation

**Files:**
- Modify: `backend/.env.example`
- Modify: `.env.example`
- Modify: `docs/user-guide/02-using-the-dashboard.md` (section notifications) ou `docs/08-monitoring.md` selon celle qui décrit les alertes
- Test: vérification manuelle sur la production

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien de programmatique.

- [ ] **Étape 1 : documenter les variables d'environnement**

Ajouter dans `backend/.env.example` et `.env.example` :

```bash
# Telegram — un groupe par canal. Créer 3 groupes, y ajouter le bot,
# récupérer les chat_id (négatifs pour un groupe).
# Si une variable est vide, le canal se replie sur TELEGRAM_CHAT_ID avec un
# préfixe [canal] et un log WARNING — rien n'est jamais perdu en silence.
TELEGRAM_CHAT_ID_VALUE=
TELEGRAM_CHAT_ID_INCIDENTS=
TELEGRAM_CHAT_ID_AUTOPILOT=
```

- [ ] **Étape 2 : documenter les trois canaux**

Dans le fichier de doc qui décrit les alertes, ajouter une section :

```markdown
## Canaux de notification

| Canal | Groupe Telegram | Sonore | Contenu |
|---|---|---|---|
| `value` | 🎯 Ev0 Value | oui | nouvelle value, ou nouveau plus haut de cote (+5 % au-dessus du dernier niveau notifié) |
| `incidents` | 🚨 Ev0 Incidents | oui | job en exception, log ERROR, settlement bloqué >48h, santé au rouge |
| `autopilot` | 🤖 Ev0 Autopilot | **mettre en sourdine** | positions prises, paris réglés, P&L, fine-tune, auto-finish, santé quand tout va bien |

Aucune notification n'est envoyée quand une value se dégrade ou disparaît : la
fenêtre de disponibilité appartient aux bookmakers.
```

- [ ] **Étape 3 : commit**

```bash
git add backend/.env.example .env.example docs/
git commit -m "docs: trois canaux de notification Telegram + variables d'environnement"
```

- [ ] **Étape 4 : créer les groupes Telegram (action utilisateur)**

1. Créer trois groupes : `🎯 Ev0 Value`, `🚨 Ev0 Incidents`, `🤖 Ev0 Autopilot`.
2. Ajouter le bot Ev0 à chacun.
3. Envoyer un message dans chaque groupe, puis relever les `chat_id` :
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool | grep -A3 '"chat"'
   ```
   Un `chat_id` de groupe est **négatif** (ex. `-1001234567890`).
4. Mettre `🤖 Ev0 Autopilot` en sourdine dans le client Telegram.

- [ ] **Étape 5 : poser les variables des DEUX côtés (Dokploy)**

**Rappel critique :** Dokploy stocke les variables dans sa propre base et **écrase le `.env`** à chaque déploiement lancé depuis son interface. Il faut donc mettre à jour les deux.

```bash
# 1) le fichier .env
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
# ajouter TELEGRAM_CHAT_ID_VALUE / _INCIDENTS / _AUTOPILOT à .env

# 2) la base Dokploy — reprendre le contenu complet du .env
docker exec -i $(docker ps -qf name=dokploy-postgres) psql -U dokploy -d dokploy -c \
  "UPDATE compose SET env = \$env\$<contenu complet du .env>\$env\$ WHERE \"composeId\" = 'bpQY8Yr986JiwJRR_b0sk';"
```

- [ ] **Étape 6 : migrer la base et redéployer**

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env \
  up -d --build --no-deps --remove-orphans backend worker
docker exec ev0-compose-z5hvqt-backend-1 alembic upgrade head
```

Vérifier que la migration 050 est bien la tête :

```bash
docker exec ev0-compose-z5hvqt-backend-1 alembic current
```
Attendu : `050 (head)`.

- [ ] **Étape 7 : vérifier bout en bout**

```bash
# Un message de test sur chaque canal
docker exec ev0-compose-z5hvqt-backend-1 python -c "
import asyncio
from app.alerts import send_alert
async def main():
    for c in ('value', 'incidents', 'autopilot'):
        ok = await send_alert(f'test canal {c}', channel=c)
        print(c, ok)
asyncio.run(main())
"
```
Attendu : `True` pour les trois, et **un message dans chaque groupe** — aucun message préfixé `[canal]` dans le chat historique. Un préfixe `[canal]` signale un `chat_id` mal posé.

- [ ] **Étape 8 : surveiller 24h**

Vérifier dans les logs du worker qu'aucun `WARNING` de repli n'apparaît :

```bash
docker logs ev0-compose-z5hvqt-worker-1 --since 24h 2>&1 | grep -i "repli sur le chat historique"
```
Attendu : aucune sortie.

---

## Auto-revue

**Couverture de la spec.**

| Exigence | Tâche |
|---|---|
| Trois canaux, un `chat_id` par groupe | 1 |
| Table de routage des onze points d'appel | 1 |
| Filet de sécurité (chat_id absent / bot retiré / Telegram KO) | 1 |
| WhatsApp : secours pour `value` et `incidents`, aucun pour `autopilot` | 1 |
| Suppression du canal par défaut | 1 |
| Plafond anti-crashloop (3/h + synthèse) | 2 |
| Colonne `alerted_odds`, migration 050 après 049 | 3 |
| Déclencheur « nouvelle value » | 4 |
| Déclencheur « nouveau plus haut » à +5 % | 4 |
| Silence sur dégradation / disparition | 4 (aucune branche ne notifie sur `expired`) |
| Groupement en un message par cycle de scraping | 4 |
| Format des messages value | 4 |
| Suppression du digest expiry, conservation d'`expire_recommendations` | 5 |
| Positions autopilot groupées, scorecard non répété | 6 |
| Santé conditionnelle avec seuils nommés | 7 |
| Variables d'environnement + mise en service Dokploy | 8 |

**Cohérence des noms.** `send_alert(message, channel)` et `alert_bg(message, channel)` gardent le même ordre d'arguments partout ; `send_telegram_alert(message, channel)` idem. `should_alert(alerted_odds, market_odds)` est appelée avec cet ordre dans les trois branches de la Task 4. `notify_value_movements(movements)` et `notify_autopilot_run(bets=…, mode=…, …)` sont testées avec les signatures qu'elles exposent. `_health_red_flags(row, now)` est appelée avec `row` puis `now` en Task 7, comme dans ses tests.

**Points de vigilance pour l'implémenteur.**

- Les numéros de ligne cités deviendront faux après le merge Transfermarkt : chercher par motif.
- Task 1 casse volontairement tout appel `send_alert` sans canal. C'est le but : l'erreur doit survenir à l'écriture, pas en production.
- En Task 4, capturer `_prev_odds` / `_prev_edge` **avant** d'écraser `existing_rec.best_odds` et `existing_rec.edge`, sinon le message affichera deux fois la même valeur.
- `_record_movement` pose `rec.alerted_odds`, mais le `session.commit()` qui la persiste est celui déjà présent en fin de `process_scraped_fixtures` — ne pas en ajouter un second.
