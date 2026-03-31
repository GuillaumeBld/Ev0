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

## Fichiers modifiés

| Fichier | Changement |
|---------|-----------|
| `backend/app/ingestion/odds.py` | Étendre `SPORT_KEYS` aux 6 ligues + nouvelle fonction `fetch_events_for_league()` |
| `backend/app/worker.py` | Réactiver `job_sync_fixtures` avec logique The Odds API |

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

Nouvelle fonction async qui appelle `/v4/sports/{sport_key}/events` (sans paramètre `markets` — juste les fixtures, 0 quota consommé pour les cotes).

Retourne `list[dict]` avec `id`, `home_team`, `away_team`, `commence_time`.

```python
async def fetch_events_for_league(league: str) -> list[dict]:
    """Fetch upcoming events (fixtures) for a league from The Odds API.

    Uses the /events endpoint — no odds markets, no quota consumption beyond
    the event list itself.
    """
```

### 3. `worker.py` — `job_sync_fixtures` réactivé

```
Pour chaque ligue active :
  1. fetch_events_for_league(league) → list[{home, away, commence_time}]
  2. Charger les fixtures DB à venir pour cette ligue
  3. Pour chaque event API :
     a. match_odds_event_to_fixture(event, db_fixtures) → fixture DB
     b. Si match trouvé ET kickoff_utc diffère → UPDATE
  4. Commit
  5. Loguer : N fixtures mises à jour pour <league>
```

Gestion d'erreurs : une ligue en erreur n'arrête pas les autres.

### 4. Fréquence

`job_sync_fixtures` : **1× par jour à 06:00 UTC** (avant `job_sync_player_stats` à 07:00).

Les horaires de matchs changent rarement — une sync quotidienne est suffisante et préserve le quota API.

## Quota The Odds API

L'endpoint `/events` ne consomme **pas** de requêtes sur le quota de cotes — il est gratuit hors abonnement. Vérifier dans la doc si ce point est confirmé pour le plan actuel ; sinon, la consommation reste négligeable (6 requêtes/jour).

## Tests

- `test_fetch_events_for_league` : mock HTTP, vérifie retour `list[dict]` avec les bons champs
- `test_job_sync_fixtures_updates_kickoff` : mock fetch + DB session, vérifie que `kickoff_utc` est mis à jour quand il diffère
- `test_job_sync_fixtures_no_update_when_same` : vérifie qu'aucun UPDATE n'est émis si le kickoff est déjà correct
- `test_job_sync_fixtures_skips_unknown_league` : ligue sans `SPORT_KEY` → skip silencieux
- `test_sport_keys_covers_all_leagues` : assertion que les 6 ligues ont une clé dans `SPORT_KEYS`
