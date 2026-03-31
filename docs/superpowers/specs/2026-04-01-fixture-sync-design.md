# Fixture Sync — Design Spec

**Date:** 2026-04-01
**Status:** Approved

## Problème

`job_sync_fixtures` est un no-op depuis que l'API FotMob retourne 404. Les fixtures sont seedées pour toute la saison via le backfill, mais leurs `kickoff_utc` sont des placeholders (`15:00 UTC`) — jamais mis à jour. Conséquence : les horaires affichés dans le dashboard sont faux, les recommandations s'appuient sur de mauvaises fenêtres temporelles.

En parallèle, `SPORT_KEYS` dans `odds.py` ne couvre que 3 ligues sur 6 — Bundesliga, La Liga et Serie A n'ont aucune cote collectée depuis The Odds API.

## Solution

Réactiver `job_sync_fixtures` en utilisant The Odds API comme source de vérité pour les horaires. La même clé API (`ODDS_API_KEY`) et le même module `fixture_matcher` déjà utilisés pour la collecte de cotes sont réutilisés — zéro nouvelle dépendance.

## Périmètre

- **Mise à jour des `kickoff_utc` uniquement** — pas de création de fixtures. La DB contient déjà toute la saison (backfill FotMob). Créer des fixtures depuis The Odds API poserait des problèmes de `fotmob_id` manquants.
- **6 ligues** : ligue_1, premier_league, bundesliga, la_liga, serie_a, champions_league.
- **Fix `SPORT_KEYS`** : ajouter bundesliga, la_liga, serie_a (bénéfice immédiat sur la collecte de cotes aussi).
- **Fix `DEFAULT_LEAGUES`** : ajouter `champions_league` pour que le job couvre bien les 6 ligues.

## Fichiers modifiés

| Fichier | Changement |
|---------|-----------|
| `backend/app/ingestion/odds.py` | Étendre `SPORT_KEYS` aux 6 ligues + nouvelle fonction `fetch_events_for_league()` |
| `backend/app/ingestion/fixture_matcher.py` | Ajouter aliases Bundesliga, La Liga, Serie A dans `TEAM_ALIASES` |
| `backend/app/worker.py` | Réactiver `job_sync_fixtures` + ajouter `champions_league` à `DEFAULT_LEAGUES` + mettre à jour le nom du job scheduler |

## Design détaillé

### 1. `odds.py` — `SPORT_KEYS` complet

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

### 2. `odds.py` — `fetch_events_for_league(league)`

Nouvelle fonction async qui appelle `/v4/sports/{sport_key}/events` (sans paramètre `markets` — juste les fixtures).

```python
async def fetch_events_for_league(league: str) -> list[dict]:
    """Fetch upcoming events (fixtures) for a league from The Odds API.

    Uses the /events endpoint without odds markets.
    Returns [] on unknown league or HTTP error.
    Each dict has keys: id, home_team, away_team, commence_time (ISO 8601 UTC).
    """
```

**Contrat d'erreur :** retourne `[]` (jamais raise) en cas d'erreur HTTP ou de ligue inconnue — cohérent avec les autres fonctions du module.

**Fenêtre temporelle :** l'endpoint `/events` retourne par défaut les événements à venir (pas de paramètre date requis). C'est suffisant car l'objectif est de corriger les kickoffs des prochains matchs.

**Client HTTP :** crée son propre `httpx.AsyncClient` en context manager, comme `fetch_odds_for_league` existant.

**Quota :** l'endpoint `/events` compte comme une requête normale sur le plan actuel (~6 requêtes/jour). Négligeable par rapport au quota mensuel.

### 3. `fixture_matcher.py` — `TEAM_ALIASES` étendu

Ajouter les aliases pour les 3 nouvelles ligues. Exemples clés :

```python
# Bundesliga
"fc bayern münchen": "bayern münchen",
"borussia dortmund": "borussia dortmund",   # déjà exact
"bayer 04 leverkusen": "bayer leverkusen",
"rb leipzig": "rb leipzig",
"eintracht frankfurt": "eintracht frankfurt",
"vfb stuttgart": "vfb stuttgart",
"sc freiburg": "freiburg",
"1. fc union berlin": "union berlin",
"hamburger sv": "hamburger sv",
"1. fc köln": "1. fc köln",

# La Liga
"fc barcelona": "barcelona",
"real madrid cf": "real madrid",
"atletico de madrid": "atletico madrid",
"athletic club": "athletic club",   # pas "athletic bilbao" dans The Odds API
"real sociedad": "real sociedad",
"villarreal cf": "villarreal",
"deportivo alaves": "deportivo alaves",

# Serie A
"ac milan": "milan",
"inter milan": "inter",
"juventus fc": "juventus",
"ssc napoli": "napoli",
"as roma": "roma",
"ss lazio": "lazio",
"atalanta bc": "atalanta",
"acf fiorentina": "fiorentina",
"hellas verona": "hellas verona",
```

**Note :** les noms dans The Odds API peuvent légèrement différer des noms en DB (ex. "FC Bayern München" vs "Bayern München"). Les aliases ci-dessus couvrent les cas connus — à enrichir depuis les logs `No match for` en production.

### 4. `worker.py` — `job_sync_fixtures` réactivé

```
Pour chaque ligue dans _get_leagues(user_settings) :
  1. fetch_events_for_league(league) → list[event]
  2. Si liste vide → skip (log WARNING si ligue active)
  3. SELECT fixtures WHERE league = ? AND status != 'finished'
  4. Pour chaque event :
     a. match_odds_event_to_fixture(event, db_fixtures) → fixture DB
     b. Si match trouvé ET kickoff_utc diffère → UPDATE kickoff_utc
  5. Commit
  6. LOG : "job_sync_fixtures: N kickoffs updated for <league>"
```

**Fenêtre DB :** `status != 'finished'` (plutôt que `kickoff_utc >= now`) pour couvrir aussi les matchs dont le kickoff placeholder est dans le passé à cause d'une erreur de date.

**Gestion d'erreurs :** exception sur une ligue → log ERROR + continue les autres ligues.

### 5. `worker.py` — `DEFAULT_LEAGUES` et scheduler

```python
DEFAULT_LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league"]
```

Nom du job scheduler : `"Sync fixture kickoffs via The Odds API"` (remplace `"Sync fixtures from FotMob"`).

### 6. Fréquence

`job_sync_fixtures` : **1× par jour à 06:00 UTC** — avant `job_sync_player_stats` (07:00).

## Tests

| Test | Description |
|------|-------------|
| `test_fetch_events_for_league_returns_list` | Mock HTTP 200, vérifie `list[dict]` avec `home_team`, `away_team`, `commence_time` |
| `test_fetch_events_for_league_unknown_league_returns_empty` | Ligue sans SPORT_KEY → `[]` |
| `test_fetch_events_for_league_http_error_returns_empty` | HTTP 403 → `[]` (pas de raise) |
| `test_job_sync_fixtures_updates_kickoff` | Mock fetch + DB, vérifie UPDATE quand kickoff diffère |
| `test_job_sync_fixtures_no_update_when_same` | Vérifie qu'aucun UPDATE si kickoff déjà correct |
| `test_job_sync_fixtures_skips_unmatched_event` | Event sans match DB → skip silencieux |
| `test_job_sync_fixtures_one_league_error_does_not_block_others` | Erreur sur ligue 1 → ligues 2 et 3 toujours traitées |
| `test_sport_keys_covers_all_six_leagues` | Assertion que les 6 ligues ont une clé dans `SPORT_KEYS` |
