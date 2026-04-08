# Autonomous Settlement — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remplacer FotMob (cassé) par Understat + ESPN pour les match events, ajouter un auto-finish basé sur l'heure, corriger le workflow GitHub Actions, et ajouter des alertes Telegram sur les blocages de settlement.

**Architecture:** Understat fournit buts + passes pour les 5 grands championnats via `rostersData` (déjà parsé dans `understat_match.py`). ESPN couvre la LDC via `espn_client.py` (déjà implémenté). Un job `job_auto_finish_fixtures` passe les matchs de `scheduled` à `finished` si le coup d'envoi + 2h est dépassé — éliminant la dépendance à FotMob `/api/leagues` (404). Le code FotMob pour les match events est supprimé.

**Tech Stack:** Python 3.13, SQLAlchemy async, APScheduler, httpx, Telegram Bot API.

---

## Contexte important (lire avant de toucher au code)

- `backend/app/ingestion/fotmob_scraper.py` contient deux types de fonctions :
  - **CDN** (`data.fotmob.com`) → stats joueurs → **fonctionne** → ne pas toucher
  - **API** (`www.fotmob.com/api/leagues`, `/api/matchDetails`) → **cassé (404/403)** → à supprimer des jobs
- `backend/app/ingestion/understat_match.py` a déjà `fetch_league_match_ids()` (retourne les matchs terminés avec `home_team`, `away_team`, `match_date`, `understat_id`) et `fetch_match_roster()` (retourne `PlayerMatchRow` avec `goals` et `assists`)
- `backend/app/ingestion/espn_client.py` supporte Ligue 1, PL, CL — il suffit d'ajouter Bundesliga/La Liga/Serie A
- `backend/app/ingestion/storage.py::store_match_events()` prend une liste de dicts `{player_name, event_type, minute}` et gère les doublons
- `backend/app/notifications.py` a `send_telegram_alert(message)` déjà fonctionnel (token configuré sur VPS)
- Il n'y a **aucun test** dans ce projet — créer `backend/tests/` pour les nouveaux modules

---

## Task 1: Fix auto-settle.yml — curl → python3

**Files:**
- Modify: `.github/workflows/auto-settle.yml:64-68`

**Step 1: Write the failing test (manuel)**

Vérifier que le step "Trigger auto-settle" utilise `curl` :
```bash
grep -n "curl" .github/workflows/auto-settle.yml
```
Expected: ligne 68 avec `docker exec ... curl ...`

**Step 2: Apply fix**

Remplacer le step "Trigger auto-settle" (lignes 64-68) :

```yaml
      - name: Trigger auto-settle
        if: steps.pending.outputs.count != '0'
        run: |
          ssh -i ~/.ssh/vps_key root@"${{ secrets.VPS_HOST }}" \
            "docker exec ev0-compose-z5hvqt-backend-1 python3 -c \
            \"import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/history/settle', method='POST'))\""
```

**Step 3: Verify**

```bash
grep -n "python3" .github/workflows/auto-settle.yml
```
Expected: ligne 68 avec `python3 -c ...urlopen...`

**Step 4: Commit**

```bash
git add .github/workflows/auto-settle.yml
git commit -m "fix: replace curl with python3 urllib in auto-settle workflow

curl not available in backend container. Use stdlib urllib instead.
Fixes: 'executable file not found in PATH' on every workflow run."
```

---

## Task 2: Ajouter les slugs ESPN manquants + test

**Files:**
- Modify: `backend/app/ingestion/espn_client.py:21-25`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_espn_slugs.py`

**Step 1: Write the failing test**

Créer `backend/tests/__init__.py` (vide) et `backend/tests/test_espn_slugs.py` :

```python
"""Tests for ESPN client configuration."""
from app.ingestion.espn_client import ESPN_LEAGUE_SLUGS


def test_espn_covers_all_big5_and_cl():
    required = {"ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league"}
    missing = required - set(ESPN_LEAGUE_SLUGS.keys())
    assert not missing, f"Missing ESPN slugs: {missing}"


def test_espn_slugs_are_correct():
    assert ESPN_LEAGUE_SLUGS["bundesliga"] == "ger.1"
    assert ESPN_LEAGUE_SLUGS["la_liga"] == "esp.1"
    assert ESPN_LEAGUE_SLUGS["serie_a"] == "ita.1"
```

**Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_espn_slugs.py -v
```
Expected: FAIL — `KeyError` ou `AssertionError` sur les clés manquantes.

**Step 3: Ajouter les 3 slugs manquants dans `espn_client.py`**

Trouver le dict `ESPN_LEAGUE_SLUGS` (lignes 21-25) et le remplacer par :

```python
ESPN_LEAGUE_SLUGS = {
    "ligue_1": "fra.1",
    "premier_league": "eng.1",
    "bundesliga": "ger.1",
    "la_liga": "esp.1",
    "serie_a": "ita.1",
    "champions_league": "uefa.champions",
}
```

**Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/test_espn_slugs.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/ingestion/espn_client.py backend/tests/
git commit -m "feat: add Bundesliga, La Liga, Serie A slugs to ESPN client

ESPN was missing ger.1, esp.1, ita.1 → match events returned 0 for
these leagues (silent fallback). Fixes settlement for Serie A, La Liga, Bundesliga."
```

---

## Task 3: Ajouter la corrélation Understat → DB fixtures dans understat_match.py

**Files:**
- Modify: `backend/app/ingestion/understat_match.py`
- Create: `backend/tests/test_understat_match.py`

**Context:** `understat_match.py` a déjà `MatchRef` et `PlayerMatchRow`. On ajoute :
1. `UNDERSTAT_TEAM_MAP` — mapping noms Understat → noms DB (même logique que `ops/import_understat_rosters.py`)
2. `norm_understat_team(name)` — normalise un nom d'équipe Understat
3. `fetch_league_match_events(league, season)` — retourne `list[dict]` avec `{home_team, away_team, match_date, events}` pour tous les matchs terminés du championnat. Chaque `events` est une liste `{player_name, event_type, minute}`.

**Step 1: Write the failing test**

Créer `backend/tests/test_understat_match.py` :

```python
"""Unit tests for understat_match team normalization."""
from app.ingestion.understat_match import norm_understat_team, UNDERSTAT_TEAM_MAP


def test_norm_understat_team_lowercase_and_strip():
    assert norm_understat_team("  PSG  ") == "psg"


def test_norm_understat_team_maps_known_names():
    # Marseille stored as "marseille" in DB, Understat calls it "Olympique de Marseille"
    assert norm_understat_team("Olympique de Marseille") == "marseille"


def test_norm_understat_team_passthrough_unknown():
    assert norm_understat_team("Paris Saint-Germain") == "paris saint-germain"


def test_team_map_covers_all_leagues():
    # Spot-check a few entries from each league
    assert "olympique de marseille" in UNDERSTAT_TEAM_MAP
    assert "1. fc union berlin" in UNDERSTAT_TEAM_MAP
    assert "ac milan" in UNDERSTAT_TEAM_MAP
```

**Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_understat_match.py -v
```
Expected: FAIL — `ImportError: cannot import name 'norm_understat_team'`

**Step 3: Implémenter dans `understat_match.py`**

Ajouter à la fin du fichier, après les dataclasses existantes (après la ligne `RATE_LIMIT = 2.0`), **avant** les fonctions `fetch_league_match_ids` et `fetch_match_roster` :

```python
# ── Team name normalization ────────────────────────────────────────
# Maps Understat team names (lowercase) → DB team names (lowercase)
UNDERSTAT_TEAM_MAP: dict[str, str] = {
    # Ligue 1
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais fc": "rennes",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "montpellier hsc": "montpellier",
    "rc lens": "lens",
    "toulouse fc": "toulouse",
    # Premier League
    "tottenham": "tottenham hotspur",
    "bournemouth": "afc bournemouth",
    "leeds": "leeds united",
    "brighton": "brighton & hove albion",
    "west ham": "west ham united",
    "leicester": "leicester city",
    "ipswich": "ipswich town",
    # Bundesliga
    "1. fc union berlin": "union berlin",
    "1. fsv mainz 05": "mainz 05",
    "fc augsburg": "augsburg",
    "1. fc heidenheim 1846": "fc heidenheim",
    "sv werder bremen": "werder bremen",
    "vfl wolfsburg": "wolfsburg",
    "vfl bochum": "bochum",
    "fc st. pauli": "st. pauli",
    "sc freiburg": "freiburg",
    # Serie A
    "ac milan": "milan",
    # La Liga (Understat uses Spanish full names)
    "atletico madrid": "atlético madrid",
    "real betis balompie": "real betis",
    "deportivo alaves": "alavés",
    "rcd espanyol": "espanyol",
    "athletic club": "athletic bilbao",
    "cd leganes": "leganés",
}


def norm_understat_team(name: str) -> str:
    """Normalize a team name from Understat to match DB storage."""
    n = name.lower().strip()
    return UNDERSTAT_TEAM_MAP.get(n, n)


def _roster_to_events(roster: list[PlayerMatchRow]) -> list[dict]:
    """Convert Understat roster rows to MatchEvent dicts.

    Stores one event per player per event type (minute=None).
    Sufficient for settlement: we only need to know IF a player scored/assisted.
    """
    events: list[dict] = []
    for row in roster:
        if not row.player_name:
            continue
        if row.goals > 0:
            events.append({"player_name": row.player_name, "event_type": "goal", "minute": None})
        if row.assists > 0:
            events.append({"player_name": row.player_name, "event_type": "assist", "minute": None})
    return events


async def fetch_league_match_events(
    league: str,
    season: str = "2025-2026",
) -> list[dict]:
    """Fetch match events (goals, assists) for all finished matches in a league.

    Makes one HTTP call to fetch match list, then one call per finished match.
    Rate-limited to RATE_LIMIT seconds between requests.

    Returns:
        List of dicts: {
            "understat_id": str,
            "home_team": str,   # as returned by Understat
            "away_team": str,
            "match_date": str,  # "YYYY-MM-DD"
            "events": list[dict]  # {player_name, event_type, minute}
        }
    """
    match_refs = await fetch_league_match_ids(league, season)
    results: list[dict] = []

    for ref in match_refs:
        try:
            roster = await fetch_match_roster(ref.understat_id)
            events = _roster_to_events(roster)
            results.append({
                "understat_id": ref.understat_id,
                "home_team": ref.home_team,
                "away_team": ref.away_team,
                "match_date": ref.match_date.isoformat(),
                "events": events,
            })
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "understat_match: failed to fetch roster for match %s (%s vs %s): %s",
                ref.understat_id, ref.home_team, ref.away_team, exc,
            )
        await asyncio.sleep(RATE_LIMIT)

    return results
```

**Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/test_understat_match.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/ingestion/understat_match.py backend/tests/test_understat_match.py
git commit -m "feat: add team normalization and batch match events fetcher to understat_match

Adds UNDERSTAT_TEAM_MAP, norm_understat_team(), _roster_to_events(), and
fetch_league_match_events() — batch fetcher that returns all finished match
events for a league. Prepares Understat as primary events source replacing FotMob."
```

---

## Task 4: Réécrire job_sync_match_events dans worker.py

**Files:**
- Modify: `backend/app/worker.py` — fonction `job_sync_match_events` (lignes ~351-481)

**Context:** La fonction actuelle tente FotMob d'abord (403) puis ESPN en fallback.
Le nouveau comportement :
- Pour les championnats Big 5 (`ligue_1`, `premier_league`, `bundesliga`, `la_liga`, `serie_a`) → Understat
- Pour la LDC (`champions_league`) → ESPN (déjà implémenté)
- Rate limit Understat : on fait le fetch par batch par championnat (1 appel league + N appels match)
- Alerte Telegram si des fixtures ont encore 0 events >24h après être `finished`

**Step 1: Écrire la logique de corrélation (pas de test pour cette partie — trop couplée à la DB)**

Remplacer la fonction `job_sync_match_events` entière (de la ligne `async def job_sync_match_events():` jusqu'à `logger.info("=== Match events sync complete ===")`) par :

```python
async def job_sync_match_events():
    """Sync match events (goals, assists) for finished fixtures.

    Primary source: Understat (Big 5 leagues) — rostersData provides goals + assists per player.
    Fallback source: ESPN public API — used for Champions League.

    FotMob is no longer used (api/matchDetails returns 403 on VPS).
    """
    logger.info("=== Starting match events sync ===")

    from datetime import timedelta

    from app.ingestion.espn_client import ESPNClient
    from app.ingestion.understat_match import (
        UNDERSTAT_TEAM_MAP,
        fetch_league_match_events,
        norm_understat_team,
    )
    from app.models.fixtures import Fixture
    from app.models.match_events import MatchEvent
    from app.notifications import send_telegram_alert

    _UNDERSTAT_LEAGUES = {"ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a"}
    _ESPN_LEAGUES = {"champions_league"}

    try:
        async with async_session() as session:
            # Find finished fixtures that have no match events yet
            fixtures_with_events = (
                select(MatchEvent.fixture_id).distinct().subquery()
            )
            result = await session.execute(
                select(Fixture)
                .where(Fixture.status == "finished")
                .where(Fixture.id.notin_(select(fixtures_with_events.c.fixture_id)))
                .order_by(Fixture.kickoff_utc.desc())
                .limit(100)
            )
            fixtures = list(result.scalars().all())

            if not fixtures:
                logger.info("No finished fixtures missing match events")
                logger.info("=== Match events sync complete ===")
                return

            logger.info("Found %d finished fixtures without match events", len(fixtures))

            # Group by league
            by_league: dict[str, list[Fixture]] = {}
            for fx in fixtures:
                by_league.setdefault(fx.league, []).append(fx)

            synced = 0
            understat_ok = 0
            espn_ok = 0

            # ── Understat (Big 5) ─────────────────────────────────────────
            for league, league_fixtures in by_league.items():
                if league not in _UNDERSTAT_LEAGUES:
                    continue

                logger.info("Fetching Understat events for %s (%d fixtures)", league, len(league_fixtures))
                try:
                    understat_matches = await fetch_league_match_events(league, CURRENT_SEASON)
                except Exception as exc:
                    logger.warning("Understat fetch failed for %s: %s", league, exc)
                    continue

                # Index Understat matches by (norm_home, norm_away, date)
                understat_index: dict[tuple[str, str, str], dict] = {}
                for m in understat_matches:
                    key = (
                        norm_understat_team(m["home_team"]),
                        norm_understat_team(m["away_team"]),
                        m["match_date"],
                    )
                    understat_index[key] = m

                for fixture in league_fixtures:
                    # Try to find matching Understat match
                    kickoff_date = fixture.kickoff_utc.strftime("%Y-%m-%d")
                    norm_home = fixture.home_team.lower().strip()
                    norm_away = fixture.away_team.lower().strip()

                    # Try same date ± 1 day (UTC offset edge cases)
                    from datetime import date as _date, timedelta as _td
                    d = _date.fromisoformat(kickoff_date)
                    dates_to_try = [
                        kickoff_date,
                        (d - _td(days=1)).isoformat(),
                        (d + _td(days=1)).isoformat(),
                    ]

                    match_data = None
                    for try_date in dates_to_try:
                        match_data = understat_index.get((norm_home, norm_away, try_date))
                        if match_data:
                            break

                    if not match_data or not match_data["events"]:
                        logger.debug(
                            "No Understat match found for %s vs %s on %s",
                            fixture.home_team, fixture.away_team, kickoff_date,
                        )
                        continue

                    try:
                        stored = await store_match_events(session, fixture.id, match_data["events"])
                        if stored > 0:
                            synced += 1
                            understat_ok += 1
                            logger.info(
                                "Stored %d events for %s vs %s (source=understat)",
                                stored, fixture.home_team, fixture.away_team,
                            )
                    except Exception as exc:
                        logger.warning("Failed to store Understat events for fixture %s: %s", fixture.id, exc)

            # ── ESPN (Champions League) ───────────────────────────────────
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as http:
                espn_client = ESPNClient(http)

                for league, league_fixtures in by_league.items():
                    if league not in _ESPN_LEAGUES:
                        continue

                    for fixture in league_fixtures:
                        kickoff_date = fixture.kickoff_utc.strftime("%Y-%m-%d")
                        try:
                            events = await espn_client.get_match_events(
                                league,
                                fixture.home_team,
                                fixture.away_team,
                                kickoff_date,
                            )
                            if events:
                                stored = await store_match_events(session, fixture.id, events)
                                if stored > 0:
                                    synced += 1
                                    espn_ok += 1
                                    logger.info(
                                        "Stored %d events for %s vs %s (source=espn)",
                                        stored, fixture.home_team, fixture.away_team,
                                    )
                            else:
                                logger.debug(
                                    "ESPN: no events for %s vs %s on %s",
                                    fixture.home_team, fixture.away_team, kickoff_date,
                                )
                        except Exception as exc:
                            logger.warning("ESPN failed for fixture %s: %s", fixture.id, exc)

                        import asyncio as _asyncio
                        await _asyncio.sleep(1.0)

            logger.info(
                "Synced match events for %d/%d fixtures (understat=%d, espn=%d)",
                synced, len(fixtures), understat_ok, espn_ok,
            )

            # ── Telegram alert for fixtures stuck >24h without events ─────
            now = datetime.now(UTC)
            stuck = [
                fx for fx in fixtures
                if fx.id not in {
                    # re-fetch which ones now have events
                } and (now - fx.kickoff_utc).total_seconds() > 86400
            ]

            # Reload fixtures without events after sync
            result2 = await session.execute(
                select(Fixture)
                .where(Fixture.status == "finished")
                .where(Fixture.id.notin_(select(fixtures_with_events.c.fixture_id)))
                .where(Fixture.kickoff_utc < now - timedelta(hours=24))
            )
            still_missing = list(result2.scalars().all())

            if still_missing:
                names = ", ".join(
                    f"{fx.home_team} vs {fx.away_team} ({fx.kickoff_utc.strftime('%Y-%m-%d')})"
                    for fx in still_missing[:5]
                )
                await send_telegram_alert(
                    f"⚠️ <b>[Ev0] Match events manquants</b>\n\n"
                    f"{len(still_missing)} match(s) terminé(s) depuis >24h sans événements :\n"
                    f"{names}"
                    + (" ..." if len(still_missing) > 5 else "")
                )

    except Exception as exc:
        logger.error("Error syncing match events: %s", exc, exc_info=True)

    logger.info("=== Match events sync complete ===")
```

**Step 2: Supprimer l'import FotMob `fetch_match_events` en haut du fichier**

Dans `worker.py`, vérifier que `fetch_match_events` n'est plus importé :
```bash
grep -n "fetch_match_events\|from app.ingestion.fotmob_scraper import" backend/app/worker.py
```

Si présent, supprimer uniquement l'import de `fetch_match_events` (garder `fetch_fotmob_fixtures` pour `job_sync_fixtures`).

**Step 3: Vérifier que le lint passe**

```bash
cd backend && uv run ruff check app/worker.py
```
Expected: no errors (corriger si besoin)

**Step 4: Commit**

```bash
git add backend/app/worker.py
git commit -m "feat: replace FotMob with Understat+ESPN for match events sync

- FotMob /api/matchDetails returns 403 on VPS — removed entirely
- Understat rostersData provides goals+assists for Big 5 leagues
- ESPN fallback for Champions League (already implemented)
- Telegram alert when fixtures stuck >24h without events"
```

---

## Task 5: Ajouter job_auto_finish_fixtures dans worker.py

**Files:**
- Modify: `backend/app/worker.py` — ajouter une nouvelle fonction + la scheduler

**Context:** Les fixtures restent `scheduled` même après le coup d'envoi car FotMob `/api/leagues` retourne 404. Ce job les passe en `finished` si `kickoff_utc + 2h < maintenant`.

**Step 1: Ajouter la fonction `job_auto_finish_fixtures`**

Ajouter après `job_auto_settle` (ligne ~1376), avant `job_autopilot_reoptimize` :

```python
# ── Job: Auto-Finish Fixtures ─────────────────────────────────────


async def job_auto_finish_fixtures():
    """Every 30 min: mark fixtures as finished if kickoff + 2h has passed.

    FotMob /api/leagues returns 404 so fixture statuses never update.
    This time-based fallback ensures settlement can proceed.
    Sends a Telegram alert listing which fixtures were auto-finished.
    """
    from datetime import timedelta

    from app.models.fixtures import Fixture
    from app.notifications import send_telegram_alert

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=2)

    async with async_session() as session:
        result = await session.execute(
            select(Fixture).where(
                Fixture.status == "scheduled",
                Fixture.kickoff_utc < cutoff,
            )
        )
        fixtures = list(result.scalars().all())

        if not fixtures:
            logger.debug("job_auto_finish_fixtures: no fixtures to auto-finish")
            return

        for fx in fixtures:
            fx.status = "finished"

        await session.commit()

        logger.info(
            "job_auto_finish_fixtures: auto-finished %d fixtures (kickoff + 2h passed)",
            len(fixtures),
        )

        names = "\n".join(
            f"• {fx.home_team} vs {fx.away_team} ({fx.kickoff_utc.strftime('%d/%m %H:%M')} UTC)"
            for fx in fixtures[:10]
        )
        await send_telegram_alert(
            f"⏱️ <b>[Ev0] Auto-finish fixtures</b>\n\n"
            f"{len(fixtures)} match(s) passés en <b>finished</b> (kickoff +2h dépassé) :\n"
            f"{names}"
            + (" ..." if len(fixtures) > 10 else "")
        )
```

**Step 2: Enregistrer le job dans `create_scheduler()`**

Dans `create_scheduler()`, ajouter après le bloc `job_auto_settle` (vers ligne 1561) :

```python
    # Auto-finish fixtures: Every 30 minutes (replaces broken FotMob fixture sync)
    scheduler.add_job(
        job_auto_finish_fixtures,
        IntervalTrigger(minutes=30),
        id="auto_finish_fixtures",
        name="Auto-finish fixtures past kickoff + 2h",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

**Step 3: Vérifier le lint**

```bash
cd backend && uv run ruff check app/worker.py
```
Expected: no errors

**Step 4: Commit**

```bash
git add backend/app/worker.py
git commit -m "feat: add job_auto_finish_fixtures — time-based fallback for fixture status

FotMob /api/leagues returns 404, leaving fixtures stuck in 'scheduled'.
New job runs every 30min: if kickoff_utc + 2h < now → status = 'finished'.
Notifies Telegram when fixtures are auto-finished."
```

---

## Task 6: Supprimer FotMob de job_sync_fixtures dans worker.py

**Files:**
- Modify: `backend/app/worker.py` — fonction `job_sync_fixtures` (lignes ~77-105)

**Context:** `job_sync_fixtures` appelle `fetch_fotmob_fixtures()` qui appelle `www.fotmob.com/api/leagues` → 404 → retourne 0 fixtures → log trompeur. Remplacer par un no-op avec log explicatif.

**Step 1: Remplacer le corps de `job_sync_fixtures`**

Remplacer le contenu de la fonction (garder la signature) :

```python
async def job_sync_fixtures():
    """Fixture status updates are handled by job_auto_finish_fixtures (kickoff + 2h rule).

    FotMob /api/leagues returns 404 — this job is intentionally a no-op.
    New fixtures for future seasons must be seeded manually via the backfill scripts.
    """
    logger.info("job_sync_fixtures: no-op (FotMob API unavailable, see job_auto_finish_fixtures)")
```

**Step 2: Supprimer l'import `fetch_fotmob_fixtures` dans worker.py**

Trouver et supprimer la ligne :
```python
from app.ingestion.fotmob_scraper import fetch_fotmob_fixtures
```
(Elle est dans le bloc d'imports en haut du fichier, ligne ~25)

**Step 3: Vérifier que les autres imports FotMob sont préservés**

```bash
grep -n "fotmob" backend/app/worker.py
```
Expected: aucun import restant (la ligne `from app.ingestion.fotmob_scraper import fetch_fotmob_fixtures` doit être supprimée).

**Step 4: Lint**

```bash
cd backend && uv run ruff check app/worker.py
```

**Step 5: Commit**

```bash
git add backend/app/worker.py
git commit -m "refactor: disable FotMob fixture sync job (API returns 404)

job_sync_fixtures is now a no-op with explanatory log message.
Fixture status updates are handled by job_auto_finish_fixtures.
Removes the misleading '0 fixtures fetched' log every morning."
```

---

## Task 7: Nettoyer fotmob_scraper.py — supprimer le code match events cassé

**Files:**
- Modify: `backend/app/ingestion/fotmob_scraper.py`

**Context:** Les fonctions `fetch_match_events`, `_parse_match_events`, `_parse_minute`, `fetch_events_for_finished_fixtures` ne sont plus utilisées (FotMob `/api/matchDetails` retourne 403). Les supprimer. Garder tout le code CDN (`fetch_fotmob_league`, `_fetch_stat_list`, etc.) qui fonctionne.

**Step 1: Identifier les fonctions à supprimer**

```bash
grep -n "^async def\|^def" backend/app/ingestion/fotmob_scraper.py
```
Expected output (à supprimer) :
- `async def fetch_match_events(match_id: int)` (ligne ~417)
- `def _parse_match_events(data: dict)` (ligne ~450)
- `def _parse_minute(raw: object)` (ligne ~537)
- `async def fetch_events_for_finished_fixtures(...)` (ligne ~555)

**Step 2: Vérifier qu'aucun import ne référence ces fonctions**

```bash
grep -rn "fetch_match_events\|_parse_match_events\|fetch_events_for_finished_fixtures" backend/app/
```
Expected: aucun résultat (ou uniquement dans `fotmob_scraper.py` lui-même)

**Step 3: Supprimer les 4 fonctions de `fotmob_scraper.py`**

Supprimer les blocs de code pour :
- `fetch_match_events` (avec son docstring)
- `_parse_match_events` (avec son docstring)
- `_parse_minute` (avec son docstring)
- `fetch_events_for_finished_fixtures` (avec son docstring)

Conserver tout le reste.

**Step 4: Lint**

```bash
cd backend && uv run ruff check app/ingestion/fotmob_scraper.py
```
Expected: no errors

**Step 5: Commit**

```bash
git add backend/app/ingestion/fotmob_scraper.py
git commit -m "refactor: remove dead FotMob match events code

fetch_match_events(), _parse_match_events(), _parse_minute(), and
fetch_events_for_finished_fixtures() are removed — FotMob /api/matchDetails
returns 403 on VPS and has been replaced by Understat + ESPN.
CDN-based player stats fetching (fetch_fotmob_league) is untouched."
```

---

## Task 8: Alertes Telegram pour les blocages de settlement

**Files:**
- Modify: `backend/app/ingestion/auto_settle.py`
- Modify: `backend/app/worker.py` — fonction `job_auto_settle`

**Context:** Ajouter une alerte Telegram quand :
1. `settle_approved_recommendations()` settle des bets (notification de settlement)
2. Des recs approuvées ne peuvent pas être settlées après >48h (fixture finished mais données manquantes)

**Step 1: Modifier `auto_settle.py` pour retourner des stats détaillées**

Modifier la signature de retour de `settle_approved_recommendations` pour retourner un dict au lieu d'un int :

```python
async def settle_approved_recommendations(db: AsyncSession) -> dict:
    """...(same docstring)...

    Returns:
        dict with keys: settled, won, lost, void, stuck_fixture_ids
        - settled: number of recommendations settled this run
        - won/lost/void: counts by result
        - stuck_fixture_ids: fixture IDs where PMM or MatchEvents are missing
    """
```

À la fin de la fonction, remplacer `return settled` par :

```python
    await db.commit()
    logger.info("auto_settle: committed %d settlements", settled)
    return {
        "settled": settled,
        "won": won_count,
        "lost": lost_count,
        "void": void_count,
        "stuck_fixture_ids": list(stuck_fixture_ids),
    }
```

Et pendant la boucle, maintenir les compteurs :
```python
    won_count = 0
    lost_count = 0
    void_count = 0
    stuck_fixture_ids: set[int] = set()
```

Incrémenter :
- `won_count` quand `result = "won"`
- `lost_count` quand `result = "lost"`
- `void_count` quand `result = "void"`
- `stuck_fixture_ids.add(fixture.id)` quand on saute à cause de PMM manquant ou MatchEvents manquants

**Step 2: Modifier `job_auto_settle` dans `worker.py` pour utiliser les stats et envoyer une alerte**

Remplacer le corps de `job_auto_settle` :

```python
async def job_auto_settle():
    """Every 3 hours: auto-settle approved recommendations via Understat."""
    logger.info("=== Starting auto-settle job ===")
    from datetime import timedelta
    from app.models.fixtures import Fixture
    from app.notifications import send_telegram_alert

    try:
        async with async_session() as session:
            stats = await settle_approved_recommendations(session)

        settled = stats["settled"]
        logger.info("auto_settle: settled %d recommendations", settled)

        if settled > 0:
            await send_telegram_alert(
                f"✅ <b>[Ev0] Settlement automatique</b>\n\n"
                f"{settled} pari(s) réglé(s) :\n"
                f"• Gagnés : {stats['won']}\n"
                f"• Perdus : {stats['lost']}\n"
                f"• Voids : {stats['void']}"
            )

        # Alert if recs are stuck (fixture finished but data missing for >48h)
        if stats["stuck_fixture_ids"]:
            now = datetime.now(UTC)
            async with async_session() as session:
                result = await session.execute(
                    select(Fixture).where(
                        Fixture.id.in_(stats["stuck_fixture_ids"]),
                        Fixture.kickoff_utc < now - timedelta(hours=48),
                    )
                )
                old_stuck = list(result.scalars().all())

            if old_stuck:
                names = "\n".join(
                    f"• {fx.home_team} vs {fx.away_team} ({fx.kickoff_utc.strftime('%d/%m')})"
                    for fx in old_stuck[:5]
                )
                await send_telegram_alert(
                    f"🚨 <b>[Ev0] Settlement bloqué</b>\n\n"
                    f"{len(old_stuck)} match(s) terminé(s) depuis >48h impossible(s) à settler :\n"
                    f"{names}\n\n"
                    f"Cause probable : PlayerMatchMinutes ou MatchEvents manquants."
                )

    except Exception:
        logger.exception("auto_settle job failed")
```

**Step 3: Lint**

```bash
cd backend && uv run ruff check app/ingestion/auto_settle.py app/worker.py
```

**Step 4: Commit**

```bash
git add backend/app/ingestion/auto_settle.py backend/app/worker.py
git commit -m "feat: add Telegram alerts for settlement results and blockers

- auto_settle now returns detailed stats (won/lost/void/stuck)
- job_auto_settle sends Telegram alert on successful settlements
- Telegram alert if recs stuck >48h without being settleable (data missing)"
```

---

## Vérification finale

Après avoir déployé sur le VPS (rebuild backend + worker) :

```bash
# 1. Vérifier que le job auto-finish tourne
docker logs ev0-compose-z5hvqt-worker-1 --tail=50 | grep "auto_finish"

# 2. Forcer un run manuel du match events sync
docker exec -e PYTHONPATH=/app ev0-compose-z5hvqt-backend-1 python3 -c "
import asyncio
from app.worker import job_sync_match_events
asyncio.run(job_sync_match_events())
"

# 3. Forcer un auto-settle
docker exec ev0-compose-z5hvqt-backend-1 python3 -c "
import urllib.request
urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/history/settle', method='POST'))
"

# 4. Vérifier l'historique
docker exec ev0-compose-z5hvqt-backend-1 python3 -c "
import asyncio
from app.db import async_session
from sqlalchemy import select, func
from app.models.recommendations import Recommendation
async def check():
    async with async_session() as s:
        r = await s.execute(select(func.count()).where(Recommendation.status=='approved', Recommendation.result.is_(None)))
        print('Unsettled approved:', r.scalar())
asyncio.run(check())
"
```
