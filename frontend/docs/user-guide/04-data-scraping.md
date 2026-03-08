# Sources de données et scraping

## Données scrappées

### Cotes bookmakers

| Bookmaker | Méthode | Fréquence |
|-----------|---------|-----------|
| Betclic | gRPC (API non officielle) | Toutes les heures |
| Parions Sport | HTTP scraping | Toutes les heures |

Le worker (`backend/app/worker.py`) lance `job_collect_odds()` toutes les heures.
Les cotes sont stockées dans `OddsSnapshot` avec timestamp.

### Statistiques joueurs

| Source | Données | Méthode |
|--------|---------|---------|
| FBref | xG, xA, passes clés, centres, minutes | HTTP scraping (BeautifulSoup) |
| Understat | xG/tir détaillé | API JSON |
| FotMob | Fixtures, match events, lineups | API non officielle |

Fréquence : mise à jour quotidienne via `job_update_stats()`.

### Matchs et événements

- **Fixtures** : FotMob API → table `fixtures`
- **Match events** (buts, passes décisives) : FotMob API → table `match_events`
- Backfill initial : script `python -m app.scripts.backfill`

## Limitations du scraping

- **Betclic gRPC** : API non documentée, susceptible de casser si Betclic change son protocole
- **Parions Sport** : retourne parfois 404 (protection anti-bot connue)
- **FBref** : rate limiting strict — maximum 1 requête / 3 secondes
- **Compositions** : non disponibles avant ~1h du match → incertitude sur les minutes attendues

## Données non scrappées (calcul interne)

- Probabilités fair : modèle Poisson interne
- Kelly fractions : calcul algébrique pur
- Backtest rewards : simulation sur données historiques
