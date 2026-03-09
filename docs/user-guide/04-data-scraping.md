# Sources de données et scraping

## Données scrappées

### Cotes bookmakers

| Bookmaker | Méthode | Fréquence |
|-----------|---------|-----------|
| Betclic | gRPC (API non officielle) | Toutes les heures |
| Parions Sport | HTTP scraping | Toutes les heures |

Le worker (`backend/app/worker.py`) lance `job_collect_odds()` toutes les heures.
Les cotes sont stockées dans `OddsSnapshot` avec timestamp.

### Statistiques joueurs (Modèle C)

| Source | Données | Méthode |
|--------|---------|---------|
| Understat | npxG, xA, xGChain, xGBuildup, goals, assists, minutes | API JSON non officielle |
| Sofascore | SOT, TAP, BCC, accurate crosses, through balls, key passes, rating | API JSON non officielle |
| FotMob | Fixtures, match events, lineups | API non officielle |

Fréquence : mise à jour quotidienne via `job_sync_player_stats()` (07:00 UTC) + `job_sync_sofascore_stats()` (07:15 UTC).

**Note** : Sofascore est bloqué sur le VPS (Cloudflare 403). Si le job Sofascore échoue, les données peuvent être importées manuellement depuis une machine locale.

### Matchs et événements

- **Fixtures** : FotMob API → table `fixtures`
- **Match events** (buts, passes décisives) : FotMob API → table `match_events`
- Backfill initial : script `python -m app.scripts.backfill`

## Limitations du scraping

- **Betclic gRPC** : API non documentée, susceptible de casser si Betclic change son protocole
- **Parions Sport** : retourne parfois 404 (protection anti-bot connue)
- **Sofascore** : bloqué sur VPS (Cloudflare) — import manuel nécessaire depuis une machine locale
- **Compositions** : non disponibles avant ~1h du match → incertitude sur les minutes attendues

## Données non scrappées (calcul interne)

- Probabilités fair : modèle Poisson interne
- Kelly fractions : calcul algébrique pur
- Backtest rewards : simulation sur données historiques
