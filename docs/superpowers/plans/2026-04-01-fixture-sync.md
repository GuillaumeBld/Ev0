# Fixture Sync Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réactiver `job_sync_fixtures` pour corriger automatiquement les `kickoff_utc` via The Odds API, et étendre `SPORT_KEYS` aux 6 ligues pour que la collecte de cotes couvre également Bundesliga, La Liga et Serie A.

**Architecture:** Trois modifications coordonnées — (1) `odds.py` reçoit les 3 ligues manquantes dans `SPORT_KEYS` et une nouvelle fonction `fetch_events_for_league()`, (2) `fixture_matcher.py` reçoit une fonction de matching par noms uniquement (sans fenêtre de date), (3) `worker.py` implémente `job_sync_fixtures` qui orchestre fetch → match → update.

**Tech Stack:** Python 3.11, httpx, SQLAlchemy async, pytest + unittest.mock. Fichiers clés : `backend/app/ingestion/odds.py`, `backend/app/ingestion/fixture_matcher.py`, `backend/app/worker.py`.

---

## Contexte technique

### Fichiers modifiés

| Fichier | Action |
|---------|--------|
| `backend/app/ingestion/odds.py` | Modifier : étendre `SPORT_KEYS` + ajouter `fetch_events_for_league()` |
| `backend/app/ingestion/fixture_matcher.py` | Modifier : ajouter `match_event_to_fixture_by_teams()` + aliases domestiques |
| `backend/app/worker.py` | Modifier : réactiver `job_sync_fixtures` + `DEFAULT_LEAGUES` + nom scheduler |
| `backend/tests/test_fixture_sync.py` | Créer : tous les tests |

### Pourquoi un matcher sans date

`match_odds_event_to_fixture()` filtre dans une fenêtre ±36h. Si les kickoffs DB sont des placeholders (début de saison), ce filtre rejette tous les événements. `job_sync_fixtures` doit donc utiliser un matcher basé uniquement sur les noms d'équipes — c'est précisément parce qu'on ne peut pas faire confiance à la date DB qu'on fait cette sync.

### Pattern `OddsAPIClient.get_events()`

La classe `OddsAPIClient` dans `odds.py` expose déjà `get_events(sport_key)` avec cache Redis. `fetch_events_for_league(league)` sera une fonction module-level qui instancie le client et l'appelle — cohérent avec `ingest_odds_for_league()`.

---

## Chunk 1 : Fondations (SPORT_KEYS, TEAM_ALIASES, DEFAULT_LEAGUES)

### Task 1 : Étendre SPORT_KEYS et TEAM_ALIASES + DEFAULT_LEAGUES

**Files:**
- Modify: `backend/app/ingestion/odds.py:22-27`
- Modify: `backend/app/ingestion/fixture_matcher.py:20-156`
- Modify: `backend/app/worker.py:40`
- Test: `backend/tests/test_fixture_sync.py`

- [ ] **Step 1 : Écrire le test SPORT_KEYS**

```python
# backend/tests/test_fixture_sync.py
"""Tests for fixture sync via The Odds API."""
from app.ingestion.odds import SPORT_KEYS
from app.ingestion.fixture_matcher import normalize_team_name


class TestSportKeys:
    def test_covers_all_six_leagues(self):
        expected = {
            "ligue_1", "premier_league", "bundesliga",
            "la_liga", "serie_a", "champions_league",
        }
        assert set(SPORT_KEYS.keys()) == expected

    def test_bundesliga_key(self):
        assert SPORT_KEYS["bundesliga"] == "soccer_germany_bundesliga"

    def test_la_liga_key(self):
        assert SPORT_KEYS["la_liga"] == "soccer_spain_la_liga"

    def test_serie_a_key(self):
        assert SPORT_KEYS["serie_a"] == "soccer_italy_serie_a"
```

- [ ] **Step 2 : Vérifier que le test échoue**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run pytest tests/test_fixture_sync.py::TestSportKeys -v
```

Attendu : `FAILED` — `AssertionError: {'ligue_1', 'premier_league', 'champions_league'} != {'ligue_1', ...}`

- [ ] **Step 3 : Étendre SPORT_KEYS dans `odds.py`**

Trouver (lignes 22-27) :
```python
SPORT_KEYS = {
    "ligue_1": "soccer_france_ligue_one",
    "premier_league": "soccer_epl",
    "champions_league": "soccer_uefa_champs_league",
}
```

Remplacer par :
```python
SPORT_KEYS = {
    "ligue_1":          "soccer_france_ligue_one",
    "premier_league":   "soccer_epl",
    "bundesliga":       "soccer_germany_bundesliga",
    "la_liga":          "soccer_spain_la_liga",
    "serie_a":          "soccer_italy_serie_a",
    "champions_league": "soccer_uefa_champs_league",
}
```

- [ ] **Step 4 : Vérifier que les tests SPORT_KEYS passent**

```bash
uv run pytest tests/test_fixture_sync.py::TestSportKeys -v
```

Attendu : `3 passed`

- [ ] **Step 5 : Écrire les tests TEAM_ALIASES**

Ajouter dans `test_fixture_sync.py` :

```python
class TestTeamAliases:
    """Vérifie que les noms The Odds API normalisent vers les noms DB."""

    def test_athletic_bilbao_normalizes(self):
        # The Odds API retourne "Athletic Bilbao", DB a "Athletic Club"
        assert normalize_team_name("Athletic Bilbao") == normalize_team_name("Athletic Club")

    def test_inter_milan_normalizes(self):
        assert normalize_team_name("Inter Milan") == normalize_team_name("Inter")

    def test_ac_milan_normalizes(self):
        assert normalize_team_name("AC Milan") == normalize_team_name("Milan")

    def test_pisa_normalizes(self):
        # The Odds API: "Pisa" / DB peut avoir "AC Pisa 1909"
        assert normalize_team_name("AC Pisa 1909") == normalize_team_name("Pisa")

    def test_bayer_leverkusen_normalizes(self):
        assert normalize_team_name("Bayer 04 Leverkusen") == normalize_team_name("Bayer Leverkusen")
```

- [ ] **Step 6 : Vérifier que certains tests échouent (aliases manquants)**

```bash
uv run pytest tests/test_fixture_sync.py::TestTeamAliases -v
```

Note : `test_inter_milan_normalizes` et `test_ac_milan_normalizes` passent déjà (aliases CL existants). Les 3 autres (`athletic_bilbao`, `pisa`, `bayer_leverkusen`) doivent échouer — ce sont les aliases à ajouter.

- [ ] **Step 7 : Ajouter les aliases manquants dans `fixture_matcher.py`**

Trouver la fin du dict `TEAM_ALIASES` (avant la ligne `"barcelone": "barcelona",`) et ajouter :

```python
    # Domestic league aliases (The Odds API name → normalized DB name)
    "athletic bilbao": "athletic-club",
    "athletic club bilbao": "athletic-club",
    "ac pisa 1909": "pisa",
    "pisa calcio": "pisa",
    "bayer 04 leverkusen": "leverkusen",
    "hamburger sv": "hamburger-sv",
    "1. fc union berlin": "union-berlin",
    "sc freiburg": "freiburg",
    "vfb stuttgart": "vfb-stuttgart",
    "1. fc koln": "koln",
    "1. fc köln": "koln",
    "mainz 05": "mainz",
    "1. fsv mainz 05": "mainz",
    "fc augsburg": "augsburg",
    "fc heidenheim": "heidenheim",
    "deportivo alaves": "deportivo-alaves",
    "ud almeria": "almeria",
    "real valladolid": "valladolid",
    "rayo vallecano": "rayo-vallecano",
    "elche cf": "elche",
    "ud las palmas": "las-palmas",
    "acf fiorentina": "fiorentina",
    "udinese calcio": "udinese",
    "us cremonese": "cremonese",
    "us lecce": "lecce",
    "cagliari calcio": "cagliari",
    "parma calcio 1913": "parma",
    "genoa cfc": "genoa",
    "como 1907": "como",
    "venezia fc": "venezia",
    "monza": "monza",
    "empoli fc": "empoli",
```

- [ ] **Step 8 : Vérifier que tous les tests TEAM_ALIASES passent**

```bash
uv run pytest tests/test_fixture_sync.py::TestTeamAliases -v
```

Attendu : `5 passed`

- [ ] **Step 9 : Étendre DEFAULT_LEAGUES dans `worker.py`**

Trouver (ligne 40) :
```python
DEFAULT_LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a"]
```

Remplacer par :
```python
DEFAULT_LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league"]
```

- [ ] **Step 10 : Lancer tous les tests existants pour vérifier aucune régression**

```bash
uv run pytest tests/ -x -q --ignore=tests/test_match_events.py --ignore=tests/test_pricing_assist.py
```

Attendu : même nombre de tests pass/fail qu'avant (176 passed, 10 pre-existing failures)

- [ ] **Step 11 : Commit**

```bash
git add backend/app/ingestion/odds.py \
        backend/app/ingestion/fixture_matcher.py \
        backend/app/worker.py \
        backend/tests/test_fixture_sync.py
git commit -m "feat: étendre SPORT_KEYS aux 6 ligues + aliases équipes domestiques"
```

---

## Chunk 2 : fetch_events_for_league + matcher sans date + job_sync_fixtures

### Task 2 : `fetch_events_for_league()` et `match_event_to_fixture_by_teams()`

**Files:**
- Modify: `backend/app/ingestion/odds.py` (ajouter fonction en fin de fichier)
- Modify: `backend/app/ingestion/fixture_matcher.py` (ajouter fonction après `match_odds_event_to_fixture`)
- Test: `backend/tests/test_fixture_sync.py`

- [ ] **Step 1 : Écrire les tests pour `fetch_events_for_league`**

Ajouter dans `test_fixture_sync.py` :

```python
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.odds import fetch_events_for_league


class TestFetchEventsForLeague:
    @pytest.mark.asyncio
    async def test_returns_list_of_events(self):
        mock_events = [
            {"id": "abc", "home_team": "PSG", "away_team": "Marseille",
             "commence_time": "2026-04-05T18:45:00Z"},
        ]
        with patch("app.ingestion.odds.OddsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.get_events = AsyncMock(return_value=mock_events)
            result = await fetch_events_for_league("ligue_1")
        assert result == mock_events

    @pytest.mark.asyncio
    async def test_unknown_league_returns_empty(self):
        result = await fetch_events_for_league("ligue_inconnue")
        assert result == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        with patch("app.ingestion.odds.OddsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.get_events = AsyncMock(side_effect=Exception("HTTP 403"))
            result = await fetch_events_for_league("ligue_1")
        assert result == []
```

- [ ] **Step 2 : Écrire les tests pour `match_event_to_fixture_by_teams`**

Ajouter dans `test_fixture_sync.py` :

```python
from app.ingestion.fixture_matcher import match_event_to_fixture_by_teams


class TestMatchEventToFixtureByTeams:
    def _make_fixture(self, home, away, kickoff=None):
        fix = MagicMock()
        fix.home_team = home
        fix.away_team = away
        fix.kickoff_utc = kickoff
        fix.odds_api_event_id = None
        return fix

    def test_matches_by_team_names(self):
        event = {"home_team": "Paris Saint Germain", "away_team": "Toulouse",
                 "commence_time": "2026-04-03T18:45:00Z", "id": "x1"}
        fixtures = [self._make_fixture("Paris Saint-Germain", "Toulouse")]
        result = match_event_to_fixture_by_teams(event, fixtures)
        assert result is not None
        assert result.home_team == "Paris Saint-Germain"

    def test_no_date_window_constraint(self):
        """Must match even when DB kickoff is completely wrong (placeholder)."""
        event = {"home_team": "Lyon", "away_team": "Rennes",
                 "commence_time": "2026-04-05T18:45:00Z", "id": "x2"}
        from datetime import datetime, timezone
        wrong_kickoff = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)  # 3 months off
        fixtures = [self._make_fixture("Lyon", "Rennes", kickoff=wrong_kickoff)]
        result = match_event_to_fixture_by_teams(event, fixtures)
        assert result is not None

    def test_returns_none_when_no_match(self):
        event = {"home_team": "Liverpool", "away_team": "Arsenal",
                 "commence_time": "2026-04-05T18:45:00Z", "id": "x3"}
        fixtures = [self._make_fixture("PSG", "Marseille")]
        result = match_event_to_fixture_by_teams(event, fixtures)
        assert result is None
```

- [ ] **Step 3 : Vérifier que les tests échouent**

```bash
uv run pytest tests/test_fixture_sync.py::TestFetchEventsForLeague \
              tests/test_fixture_sync.py::TestMatchEventToFixtureByTeams -v
```

Attendu : `FAILED` — `ImportError: cannot import name 'fetch_events_for_league'`

- [ ] **Step 4 : Implémenter `fetch_events_for_league` dans `odds.py`**

Ajouter en fin de fichier (après `ingest_odds_for_league`) :

```python
async def fetch_events_for_league(league: str) -> list[dict]:
    """Fetch upcoming fixtures for a league from The Odds API.

    Used by job_sync_fixtures to update kickoff_utc in DB.
    Returns [] on unknown league or any HTTP error — never raises.
    Each dict has: id, home_team, away_team, commence_time (ISO 8601 UTC).

    Note: delegates to OddsAPIClient.get_events() which includes Redis caching
    — intentional reuse rather than a raw httpx.AsyncClient (DRY, avoids
    duplicate quota consumption when odds snapshot job runs on the same day).
    """
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        logger.warning("fetch_events_for_league: unknown league %s", league)
        return []
    client = OddsAPIClient()
    try:
        return await client.get_events(sport_key)
    except Exception as exc:
        logger.error("fetch_events_for_league %s: %s", league, exc)
        return []
```

- [ ] **Step 5 : Implémenter `match_event_to_fixture_by_teams` dans `fixture_matcher.py`**

Ajouter après la fonction `match_odds_event_to_fixture` :

```python
def match_event_to_fixture_by_teams(
    event: dict[str, Any],
    fixtures: list[Any],
) -> Any | None:
    """Match an Odds API event to a DB Fixture using team names only.

    Unlike match_odds_event_to_fixture, this function does NOT apply a date
    window filter — it is intended for job_sync_fixtures where kickoff_utc
    in DB may be a placeholder (incorrect date).

    Returns the matched Fixture ORM object, or None.
    """
    event_id = event.get("id", "")

    # Fast path: cached odds_api_event_id
    for fixture in fixtures:
        if fixture.odds_api_event_id and fixture.odds_api_event_id == event_id:
            return fixture

    # Team name matching only (no date window)
    event_home = normalize_team_name(event.get("home_team", ""))
    event_away = normalize_team_name(event.get("away_team", ""))

    if not event_home or not event_away:
        return None

    for fixture in fixtures:
        fix_home = normalize_team_name(fixture.home_team or "")
        fix_away = normalize_team_name(fixture.away_team or "")
        if fix_home == event_home and fix_away == event_away:
            return fixture

    return None
```

- [ ] **Step 6 : Vérifier que tous les nouveaux tests passent**

```bash
uv run pytest tests/test_fixture_sync.py -v
```

Attendu : tous les tests passent

- [ ] **Step 7 : Commit**

```bash
git add backend/app/ingestion/odds.py \
        backend/app/ingestion/fixture_matcher.py \
        backend/tests/test_fixture_sync.py
git commit -m "feat: fetch_events_for_league + match_event_to_fixture_by_teams"
```

---

### Task 3 : Réactiver `job_sync_fixtures` dans `worker.py`

**Files:**
- Modify: `backend/app/worker.py:75-81` (corps du job)
- Modify: `backend/app/worker.py:1516` (nom scheduler)
- Test: `backend/tests/test_fixture_sync.py`

- [ ] **Step 1 : Écrire les tests pour `job_sync_fixtures`**

Ajouter dans `test_fixture_sync.py` :

```python
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

# Note: job_sync_fixtures est une fonction async dans worker.py
# On la teste en isolation en mockant fetch_events_for_league et la DB session


class TestJobSyncFixtures:
    """Tests for job_sync_fixtures — vérifie mise à jour des kickoff_utc."""

    def _make_fixture(self, id_, home, away, kickoff, league="ligue_1"):
        fix = MagicMock()
        fix.id = id_
        fix.home_team = home
        fix.away_team = away
        fix.kickoff_utc = kickoff
        fix.league = league
        fix.odds_api_event_id = None
        fix.status = "scheduled"
        return fix

    @pytest.mark.asyncio
    async def test_updates_kickoff_when_different(self):
        wrong_kickoff = datetime(2026, 4, 5, 15, 0, tzinfo=timezone.utc)
        correct_kickoff = datetime(2026, 4, 3, 18, 45, tzinfo=timezone.utc)
        fixture = self._make_fixture(631, "Paris Saint-Germain", "Toulouse", wrong_kickoff, league="ligue_1")

        mock_events = [{
            "id": "event-abc",
            "home_team": "Paris Saint Germain",
            "away_team": "Toulouse",
            "commence_time": "2026-04-03T18:45:00Z",
        }]

        async def fake_fetch(league):
            # Ne retourner des events que pour ligue_1 pour isoler le test
            return mock_events if league == "ligue_1" else []

        with patch("app.worker.fetch_events_for_league", new=AsyncMock(side_effect=fake_fetch)), \
             patch("app.worker.match_event_to_fixture_by_teams", return_value=fixture), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
             patch("app.worker.async_session") as mock_session_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[fixture])))))
            mock_session.commit = AsyncMock()
            mock_session_ctx.return_value = mock_session

            from app.worker import job_sync_fixtures
            await job_sync_fixtures()

        assert fixture.kickoff_utc == correct_kickoff

    @pytest.mark.asyncio
    async def test_no_update_when_kickoff_already_correct(self):
        correct_kickoff = datetime(2026, 4, 3, 18, 45, tzinfo=timezone.utc)
        fixture = self._make_fixture(631, "Paris Saint-Germain", "Toulouse", correct_kickoff, league="ligue_1")

        mock_events = [{
            "id": "event-abc",
            "home_team": "Paris Saint Germain",
            "away_team": "Toulouse",
            "commence_time": "2026-04-03T18:45:00Z",
        }]

        original_kickoff = fixture.kickoff_utc

        async def fake_fetch(league):
            return mock_events if league == "ligue_1" else []

        with patch("app.worker.fetch_events_for_league", new=AsyncMock(side_effect=fake_fetch)), \
             patch("app.worker.match_event_to_fixture_by_teams", return_value=fixture), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
             patch("app.worker.async_session") as mock_session_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[fixture])))))
            mock_session.commit = AsyncMock()
            mock_session_ctx.return_value = mock_session

            from app.worker import job_sync_fixtures
            await job_sync_fixtures()

        assert fixture.kickoff_utc == original_kickoff

    @pytest.mark.asyncio
    async def test_skips_event_with_no_fixture_match(self):
        mock_events = [{
            "id": "event-xyz",
            "home_team": "Unknown Team",
            "away_team": "Also Unknown",
            "commence_time": "2026-04-03T18:45:00Z",
        }]

        with patch("app.worker.fetch_events_for_league", new=AsyncMock(return_value=mock_events)), \
             patch("app.worker.match_event_to_fixture_by_teams", return_value=None), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})), \
             patch("app.worker.async_session") as mock_session_ctx:

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
            mock_session.commit = AsyncMock()
            mock_session_ctx.return_value = mock_session

            from app.worker import job_sync_fixtures
            await job_sync_fixtures()  # doit terminer sans erreur

    @pytest.mark.asyncio
    async def test_one_league_error_does_not_block_others(self):
        """Une erreur sur ligue_1 ne doit pas empêcher le traitement de premier_league."""
        call_count = 0

        async def fake_fetch(league):
            nonlocal call_count
            call_count += 1
            if league == "ligue_1":
                raise Exception("Simulated API error")
            return []

        with patch("app.worker.fetch_events_for_league", new_callable=AsyncMock, side_effect=fake_fetch), \
             patch("app.worker._load_user_settings", new=AsyncMock(return_value={})):

            from app.worker import job_sync_fixtures
            from app.worker import DEFAULT_LEAGUES
            await job_sync_fixtures()  # ne doit pas raise

        # Toutes les ligues de DEFAULT_LEAGUES ont été tentées (pas d'arrêt au premier échec)
        assert call_count == len(DEFAULT_LEAGUES)
```

- [ ] **Step 2 : Vérifier que les tests échouent (job est encore un no-op)**

```bash
uv run pytest tests/test_fixture_sync.py::TestJobSyncFixtures -v
```

Attendu : `FAILED` — les mocks ne voient pas les imports attendus

- [ ] **Step 3 : Implémenter `job_sync_fixtures` dans `worker.py`**

Note : `select` est déjà importé en tête de `worker.py` — vérifier avec `grep -n "^from sqlalchemy" backend/app/worker.py`.

Remplacer le corps de `job_sync_fixtures` (lignes 75-81) :

```python
async def job_sync_fixtures():
    """Sync fixture kickoff_utc from The Odds API.

    Fetches upcoming events per league and updates kickoff_utc where it
    differs from the DB value. Matches by team names only (no date window)
    to handle placeholder kickoffs.
    """
    from datetime import datetime, timezone as _tz
    from app.ingestion.odds import fetch_events_for_league
    from app.ingestion.fixture_matcher import match_event_to_fixture_by_teams
    from app.models.fixtures import Fixture

    logger.info("=== Starting fixture sync ===")
    user_settings = await _load_user_settings()
    leagues = _get_leagues(user_settings)

    total_updated = 0

    for league in leagues:
        try:
            events = await fetch_events_for_league(league)
            if not events:
                logger.info("job_sync_fixtures: no events for %s", league)
                continue

            async with async_session() as session:
                result = await session.execute(
                    select(Fixture).where(
                        Fixture.league == league,
                        Fixture.status != "finished",
                    )
                )
                db_fixtures = list(result.scalars().all())

                updated = 0
                for event in events:
                    fixture = match_event_to_fixture_by_teams(event, db_fixtures)
                    if not fixture:
                        continue

                    api_kickoff_raw = event.get("commence_time", "")
                    if not api_kickoff_raw:
                        continue
                    try:
                        api_kickoff = datetime.fromisoformat(
                            api_kickoff_raw.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        continue

                    if fixture.kickoff_utc != api_kickoff:
                        fixture.kickoff_utc = api_kickoff
                        session.add(fixture)
                        updated += 1

                await session.commit()
                logger.info(
                    "job_sync_fixtures: %d kickoffs updated for %s",
                    updated, league,
                )
                total_updated += updated

        except Exception as exc:
            logger.error(
                "job_sync_fixtures: error on %s: %s", league, exc, exc_info=True
            )

    logger.info("=== Fixture sync complete: %d total kickoffs updated ===", total_updated)
```

- [ ] **Step 4 : Mettre à jour le nom du job scheduler**

Trouver (ligne ~1516) :
```python
        name="Sync fixtures from FotMob",
```

Remplacer par :
```python
        name="Sync fixture kickoffs via The Odds API",
```

Vérifier :
```bash
grep -n "Sync fixture" backend/app/worker.py
```
Attendu : `name="Sync fixture kickoffs via The Odds API"`

- [ ] **Step 5 : Vérifier que les tests passent**

```bash
uv run pytest tests/test_fixture_sync.py -v
```

Attendu : tous les tests passent

- [ ] **Step 6 : Lancer la suite complète**

```bash
uv run pytest tests/ -q --ignore=tests/test_match_events.py --ignore=tests/test_pricing_assist.py
```

Attendu : même nombre de passed qu'avant + nouveaux tests fixture_sync

- [ ] **Step 7 : Commit final**

```bash
git add backend/app/worker.py backend/tests/test_fixture_sync.py
git commit -m "feat: réactiver job_sync_fixtures via The Odds API (daily 06:00 UTC)"
```

---

## Intégration finale

- [ ] **Push et deploy**

```bash
git push origin main
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && git pull origin main && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker"
```

- [ ] **Vérifier les logs après le prochain job (06:00 UTC)**

```bash
ssh root@213.130.144.204 "docker logs ev0-compose-z5hvqt-worker-1 2>&1 | grep -i 'fixture sync\|kickoffs updated' | tail -20"
```

Attendu :
```
=== Starting fixture sync ===
job_sync_fixtures: N kickoffs updated for ligue_1
...
=== Fixture sync complete: N total kickoffs updated ===
```
