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

### Statistiques joueurs — Source primaire : Bzzoiro API

Depuis l'intégration Bzzoiro, toutes les statistiques joueurs sont alimentées par l'**API Bzzoiro** (source officielle). Six jobs de synchronisation tournent quotidiennement :

| Job | Table(s) alimentée(s) | Fréquence |
|-----|-----------------------|-----------|
| `job_sync_bzzoiro_reference` | `bzz_leagues`, `bzz_teams` | 06:00 UTC |
| `job_sync_bzzoiro_players` | `bzz_players` | 06:05 UTC |
| `job_sync_bzzoiro_events` | `bzz_events` (fixtures enrichis) | 06:10 UTC |
| `job_sync_bzzoiro_player_stats` | `bzz_player_match_stats` (métriques par match) | 06:20 UTC |
| `job_sync_bzzoiro_predictions` | `bzz_predictions` (xG prédits par Bzzoiro) | 06:30 UTC |
| `job_aggregate_season_stats` | `bzz_player_season_stats` (agrégats saison) | 04:00 UTC |

**Métriques collectées par match** (dans `bzz_player_match_stats`) :
- Minutes jouées, buts, passes décisives, tirs, tirs cadrés
- xG (expected goals), xA (expected assists), rating
- Passes clés (`key_passes`), centres précis (`accurate_crosses`)
- Métriques dérivées calculées à l'ingestion : `xg_per_shot`, `shot_accuracy`, `key_pass_per_90`, `xa_per_90`, `accurate_cross_per_90`

### Sources de secours (fallback uniquement)

Understat et Sofascore sont désormais relégués au rang de **sources de secours** — ils ne sont plus utilisés dans le pipeline principal. Ils peuvent être réactivés manuellement si l'API Bzzoiro est indisponible.

| Source | Données | Statut |
|--------|---------|--------|
| Understat | npxG, xA, xGChain — données historiques | Fallback uniquement |
| Sofascore | SOT, rating, passes clés — bloqué sur VPS (Cloudflare 403) | Fallback uniquement |
| FotMob | Fixtures, match events, lineups | Backfill initial uniquement |

### Matchs et événements

- **Fixtures** : FotMob API → table `fixtures` (backfill initial) + Bzzoiro `bzz_events` (sync continue)
- **Kickoffs** : The Odds API `/v4/sports/{sport_key}/events` → mise à jour quotidienne de `kickoff_utc` via `job_sync_fixtures` (06:00 UTC). Couvre les 6 ligues (Big 5 + Ligue des Champions).
- **Match events** (buts, passes décisives) : Bzzoiro `bzz_player_match_stats` → table `match_events` (via ingestion)

### Cotes de marché (solveur Poisson)

OddsPortal est la source primaire pour le solveur Poisson (MarketXgService). La chaîne de fallback est : OddsPortal → Betclic → Unibet. Ces cotes alimentent `oddsportal_poll_state` et `match_odds_snapshots`, distinctes des OddsSnapshot bookmakers ci-dessus.

**Seeding automatique (`job_discover_oddsportal_urls`)** — tourne chaque jour à 08:00 UTC :
1. Scrape les pages listing OddsPortal pour les 6 ligues (Big 5 + Ligue des Champions) via Playwright
2. Mappe chaque match découvert vers une fixture DB (fenêtre ±30min, fuzzy matching + alias DB)
3. Upsert dans `oddsportal_poll_state` pour que le `MarketScrapeScheduler` puisse scraper les cotes

**Apprentissage des alias** : à chaque match confirmé, le nom OddsPortal non encore connu est ajouté à `canonical_teams.aliases` pour accélérer les run suivants.

## Limitations du scraping

- **Unibet LVS** : API non documentée, node IDs des compétitions peuvent changer si Unibet restructure son catalogue
- **Betclic gRPC** : API non documentée, susceptible de casser si Betclic change son protocole
- **Parions Sport** : retourne parfois 404 (protection anti-bot connue)
- **Bzzoiro** : données disponibles uniquement après la première exécution des jobs de sync (voir `05-limitations.md`)
- **Compositions** : non disponibles avant ~1h du match → incertitude sur les minutes attendues

## Données non scrappées (calcul interne)

- Probabilités fair : modèle Poisson interne
- Kelly fractions : calcul algébrique pur
- Backtest rewards : simulation sur données historiques
