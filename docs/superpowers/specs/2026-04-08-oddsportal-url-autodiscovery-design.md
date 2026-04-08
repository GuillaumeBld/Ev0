# OddsPortal URL Auto-Discovery — Design Spec

**Date:** 2026-04-08

## Objectif

Remplir automatiquement `oddsportal_poll_state` avant chaque gameweek en scrappant les pages listing de ligue OddsPortal, sans intervention manuelle.

---

## Contexte

Le `MarketScrapeScheduler` (tourne toutes les 15s) ne scrape que les fixtures présentes dans `oddsportal_poll_state`. Sans lignes dans cette table, il est en mode éco idle (`due=0`). Actuellement, le seeding est manuel via `seed_poll_state.py --csv`. Ce nouveau système l'automatise.

**Tables existantes réutilisées :**
- `canonical_teams` — `CanonicalTeam.aliases: ARRAY(Text)` liste tous les noms connus d'un club
- `fixtures` — `Fixture.home_team`, `Fixture.away_team`, `Fixture.kickoff_utc`, `Fixture.league`
- `oddsportal_poll_state` — cible des upserts (contrainte `uq_poll_state_fixture`)

**Fonctions existantes réutilisées :**
- `normalize_team_name()` dans `app/ingestion/fixture_matcher.py` — normalisation + TEAM_ALIASES statiques

---

## Architecture — 3 composants

### 1. `OddsPortalLeagueDiscoverer` (`app/ingestion/oddsportal_league_discoverer.py`)

Playwright scrape les pages listing OddsPortal par compétition. Retourne pour chaque match à venir un `OddsPortalMatchItem`.

```python
@dataclass
class OddsPortalMatchItem:
    home_raw: str          # nom brut tel qu'affiché sur OddsPortal
    away_raw: str
    kickoff_utc: datetime  # converti en UTC depuis l'heure locale OddsPortal
    match_url: str         # URL complète du match (ex. /football/france/ligue-1/psg-lyon-abc/)
    league: str            # clé interne (ex. "ligue_1")
```

**URLs par ligue :**
```python
ODDSPORTAL_LEAGUE_URLS: dict[str, str] = {
    "ligue_1":         "https://www.oddsportal.com/football/france/ligue-1/",
    "premier_league":  "https://www.oddsportal.com/football/england/premier-league/",
    "bundesliga":      "https://www.oddsportal.com/football/germany/bundesliga/",
    "la_liga":         "https://www.oddsportal.com/football/spain/laliga/",
    "serie_a":         "https://www.oddsportal.com/football/italy/serie-a/",
    "champions_league":"https://www.oddsportal.com/football/europe/champions-league/",
}
```

**Stratégie de scraping :**
- Playwright headless, attend le rendu JS (React SPA)
- Filtre les matchs dans les **7 prochains jours** uniquement
- Les sélecteurs CSS sont des **placeholders** (à vérifier sur le DOM live en production, même situation que le match scraper)
- Gestion gracieuse des erreurs : si une ligue échoue, les autres continuent

### 2. `OddsPortalFixtureMatcher` (`app/ingestion/oddsportal_fixture_matcher.py`)

Mappe chaque `OddsPortalMatchItem` vers un `Fixture` DB.

**Algorithme de matching (en ordre) :**

**Étape 1 — Fenêtre temporelle**
Charger les fixtures DB de la ligue avec `kickoff_utc` dans les 7 prochains jours.
Pour chaque `OddsPortalMatchItem`, ne garder comme candidats que les fixtures avec `|kickoff_utc_db - kickoff_utc_op| ≤ 30min`.

**Étape 2 — Résolution des conflits temporels**
Si plusieurs items OddsPortal ont le même créneau horaire (±5min) : traiter le groupe comme un **problème d'assignation** (scipy `linear_sum_assignment`) sur une matrice de score `(nb_items × nb_candidats)`.

**Étape 3 — Score de similarité**
Pour chaque paire (item, fixture candidat) :
```
score = mean(
    rapidfuzz.token_sort_ratio(normalize(home_raw), normalize(fixture.home_team)),
    rapidfuzz.token_sort_ratio(normalize(away_raw), normalize(fixture.away_team)),
)
```
Où `normalize()` utilise la `normalize_team_name()` existante (qui consulte `TEAM_ALIASES` statiques).

**Étape 4 — Lookup dans `CanonicalTeam.aliases`**
Avant le fuzzy, tenter un match exact sur les alias DB :
```
canonical = SELECT * FROM canonical_teams WHERE aliases @> ARRAY[normalize(name)]
```
Si trouvé, score forcé à 1.0 (match certain).

**Seuil d'acceptation :** score ≥ 0.75 → match confirmé. En dessous → log WARNING, pas de crash.

**Étape 5 — Apprentissage des alias**
Pour chaque match confirmé où le nom OddsPortal n'était pas déjà dans `canonical_teams.aliases` :
```sql
UPDATE canonical_teams
SET aliases = aliases || ARRAY['man-city']
WHERE id = <id_manchester_city>
```
Après 2-3 gameweeks, la quasi-totalité des équipes est connue et le fuzzy n'est plus sollicité.

**Sortie :** `list[tuple[int, str]]` — `(fixture_id, oddsportal_url)`

### 3. Upsert dans `oddsportal_poll_state`

Réutilise la logique du `seed_poll_state.py` existant :
```sql
INSERT INTO oddsportal_poll_state (fixture_id, oddsportal_url, next_due_at_utc, ...)
ON CONFLICT (fixture_id) DO UPDATE SET oddsportal_url = excluded.oddsportal_url
-- NE PAS écraser next_due_at_utc ni error_streak
```

---

## Job Worker

**Nouveau job** dans `app/worker.py` :
```python
async def job_discover_oddsportal_urls() -> None:
    """Découverte automatique quotidienne des URLs OddsPortal."""
    async with async_session() as session:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            # 1. Scrape toutes les leagues
            # 2. Match vs fixtures DB
            # 3. Upsert poll_state
            await browser.close()

scheduler.add_job(
    job_discover_oddsportal_urls,
    CronTrigger(hour=8, minute=0),   # chaque jour à 8h UTC
    id="job_discover_oddsportal_urls",
    max_instances=1,
)
```

---

## Fichiers

| Action | Fichier |
|--------|---------|
| Créer | `backend/app/ingestion/oddsportal_league_discoverer.py` |
| Créer | `backend/app/ingestion/oddsportal_fixture_matcher.py` |
| Modifier | `backend/app/worker.py` |
| Créer | `backend/tests/ingestion/test_oddsportal_fixture_matcher.py` |

---

## Gestion d'erreur et limites

- **Sélecteurs OddsPortal** : placeholders comme dans `oddsportal_scraper.py` — doivent être vérifiés sur le DOM live avant production
- **Ligue indisponible** : si OddsPortal retourne une page vide ou erreur, la ligue est skippée (log WARNING), les autres continuent
- **Aucun match trouvé** : log INFO `"discovered 0 fixtures for ligue_1"`, pas d'exception
- **Score < 0.75** : log WARNING avec les noms bruts pour faciliter la correction manuelle des TEAM_ALIASES statiques
- **Timezone** : OddsPortal affiche les heures en CET/CEST — parser via la balise `datetime` HTML (format ISO) si disponible, sinon déduire depuis `Europe/Paris`

---

## Tests

- `TestOddsPortalFixtureMatcher` — unit tests avec fixtures DB mockées :
  - Match exact via alias connu
  - Match fuzzy (score > 0.75)
  - Rejet score < 0.75
  - Assignation optimale sur 3 matchs simultanés
  - Apprentissage d'un nouvel alias
- Pas de test E2E Playwright (sélecteurs non vérifiables hors production)
