# Sources de données

## Statistiques joueurs — Bzzoiro API

Toutes les statistiques joueurs sont alimentées par l'**API Bzzoiro**. Six jobs de synchronisation tournent quotidiennement :

| Job | Tables alimentées | Fréquence |
|-----|-------------------|-----------|
| `job_sync_bzzoiro_reference` | `bzz_leagues`, `bzz_teams` | 06:00 UTC |
| `job_sync_bzzoiro_players` | `bzz_players` | 06:05 UTC |
| `job_sync_bzzoiro_events` | `bzz_events` | 06:10 UTC |
| `job_sync_bzzoiro_player_stats` | `bzz_player_match_stats` | 06:20 UTC |
| `job_aggregate_season_stats` | `bzz_player_season_stats` | 04:00 UTC |

**Métriques collectées par match** :
- Minutes jouées, buts, passes décisives, tirs, tirs cadrés
- xG (expected goals), xA (expected assists), rating
- Passes clés (`key_passes`), centres précis (`accurate_crosses`)
- Métriques dérivées calculées à l'ingestion : `xg_per_shot`, `shot_accuracy`, `key_pass_per_90`, `xa_per_90`, `accurate_cross_per_90`

Les **agrégats saison** (`bzz_player_season_stats`) incluent les données de forme des 5 derniers matchs (`form_xg_5`, `form_assists_5`), utilisées dans le blend 60/40 du modèle top-down.

---

## Cotes de marché — solveur Poisson (xG équipe)

Les cotes H2H (1×2), Over/Under 2.5 et BTTS sont collectées pour alimenter le solveur qui calcule `λ_home` et `λ_away`. La chaîne de priorité est :

**OddsPortal** (Playwright) → **Betclic** (HTTP) → **Unibet** (HTTP)

Ces cotes sont stockées dans `match_odds_snapshots`. Un snapshot est valide pendant **4 heures**.

**Découverte automatique des URLs (`job_discover_oddsportal_urls`)** — tourne chaque jour à 08:00 UTC :
1. Scrape les pages listing OddsPortal pour les 5 ligues (Big 5) + Champions League via Playwright
2. Mappe chaque match vers une fixture DB (fenêtre ±30 min, fuzzy matching + alias équipe)
3. Met à jour `oddsportal_poll_state` pour que le scraper puisse collecter les cotes

**Cadence de scrape** adaptative selon le temps avant le match :
- > 24h → toutes les 120 min
- 6–24h → toutes les 60 min
- 2–6h → toutes les 20 min
- < 2h → toutes les 7 min
- < 30 min → stop (la cote ne bouge plus)

---

## Cotes joueurs — buteur / passeur

Les cotes anytime goalscorer et anytime assist sont scrappées directement :

| Bookmaker | Méthode |
|-----------|---------|
| Unibet | API LVS HTTP (plateforme post-fusion PSEL, mars 2026) |
| Betclic | HTTP scraping |

Ces cotes sont stockées dans `player_odds_snapshots`. Le worker (`job_snapshot_direct_odds`) tourne toutes les 2 heures. Pour chaque paire (joueur, marché), seule la meilleure cote disponible est retenue lors du calcul des recommandations.

---

## Fixtures et événements de match

- **Fixtures** : FotMob API (backfill initial) + The Odds API (`job_sync_fixtures`, 06:00 UTC)
- **Match events** (buts, passes décisives) : importés manuellement depuis Sofascore via script one-off (Sofascore est bloqué sur le VPS par Cloudflare). Les events sont stockés dans `match_events` et utilisés pour le settlement automatique.

---

## Données non scrappées (calcul interne)

- Probabilités fair : modèle top-down Poisson interne
- Kelly fractions : calcul algébrique pur
- Backtest rewards : simulation sur données historiques
