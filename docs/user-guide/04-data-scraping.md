# Sources de données et scraping

## Données scrappées

### Cotes bookmakers

| Bookmaker | Méthode | Fréquence |
|-----------|---------|-----------|
| Unibet | API LVS HTTP (nouveau site post-fusion PSEL, mars 2026) | Toutes les 3h |
| Betclic | gRPC (API non officielle) | Toutes les 3h |
| Parions Sport | HTTP scraping | Toutes les 3h |

Le worker (`backend/app/worker.py`) lance `job_snapshot_direct_odds()` toutes les 3h.
Les cotes sont stockées dans `OddsSnapshot` avec timestamp.

**Note Unibet** : Le site Unibet.fr a fusionné avec PSEL en mars 2026 (Kambi abandonné). Le nouveau site tourne sur la plateforme LVS (Lineup7/SportEase). Le scraper (`unibet_lvs_scraper.py`) s'authentifie via token anonyme, sans compte ni Playwright requis. Couvre le Big 5 + Ligue des Champions, marchés buteur et passeur décisif.

### Statistiques joueurs (Modèle C)

| Source | Données | Méthode |
|--------|---------|---------|
| Understat | npxG, xA, xGChain, xGBuildup, goals, assists, minutes | API JSON non officielle |
| Sofascore | SOT, TAP, BCC, accurate crosses, through balls, key passes, rating | API JSON non officielle |
| FotMob | Fixtures, match events, lineups | API non officielle |

Fréquence : mise à jour quotidienne via `job_sync_player_stats()` (07:00 UTC) + `job_sync_sofascore_stats()` (07:15 UTC).

**Note** : Sofascore est bloqué sur le VPS (Cloudflare 403). Si le job Sofascore échoue, les données peuvent être importées manuellement depuis une machine locale.

### Matchs et événements

- **Fixtures** : FotMob API → table `fixtures` (backfill initial uniquement)
- **Kickoffs** : The Odds API `/v4/sports/{sport_key}/events` → mise à jour quotidienne de `kickoff_utc` via `job_sync_fixtures` (06:00 UTC). Couvre les 6 ligues (Big 5 + Ligue des Champions). Utilise la même clé `ODDS_API_KEY` que la collecte de cotes.
- **Match events** (buts, passes décisives) : FotMob API → table `match_events`
- Backfill initial : script `python -m app.scripts.backfill`

## Limitations du scraping

- **Unibet LVS** : API non documentée, node IDs des compétitions peuvent changer si Unibet restructure son catalogue
- **Betclic gRPC** : API non documentée, susceptible de casser si Betclic change son protocole
- **Parions Sport** : retourne parfois 404 (protection anti-bot connue)
- **Sofascore** : bloqué sur VPS (Cloudflare) — import manuel nécessaire depuis une machine locale
- **Compositions** : non disponibles avant ~1h du match → incertitude sur les minutes attendues

## Données non scrappées (calcul interne)

- Probabilités fair : modèle Poisson interne
- Kelly fractions : calcul algébrique pur
- Backtest rewards : simulation sur données historiques
