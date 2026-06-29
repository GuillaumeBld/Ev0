# Architecture

## Vue d'ensemble

Ev0 est un moteur de pricing de paris sportifs (marchés buteur/passeur) déployé sur VPS via Docker Compose. Il se compose d'un backend FastAPI, d'un worker de jobs planifiés, d'un frontend Next.js, et d'une base PostgreSQL.

```
Bzzoiro API ──────────────────────┐
OddsPortal / Betclic / Unibet ────┤
Sofascore (WC2026 events) ────────┤
                                  ▼
                         [Worker — APScheduler]
                         jobs planifiés toutes les 30min
                                  │
                         ┌────────┼────────┐
                         ▼        ▼        ▼
                     PostgreSQL  Redis  [Backend — FastAPI]
                                           │
                                  [Frontend — Next.js 14]
                                  https://ev0-213-130-144-204.sslip.io
```

---

## Composants

### Backend (`app/`)

FastAPI, port 8000. Expose les endpoints de pricing, recommandations, fixtures, WC2026.

Modules clés :
- `app/api/` — endpoints REST
- `app/pricing/` — moteur de pricing (team_xg, goalscorer, assist, supersub, wc2026_bracket, wc2026_tournament)
- `app/ingestion/` — clients Bzzoiro, OddsPortal, Betclic, Unibet
- `app/services/` — MarketXgService, recommendation engine
- `app/models/` — modèles SQLAlchemy (voir `04-database-schema.md`)

### Worker (`app/worker.py`)

Process séparé (`python -m app.worker`). APScheduler avec jobs :

| Job | Fréquence | Description |
|-----|-----------|-------------|
| `job_settle_pipeline` | 30 min | Pipeline principal : sync BzzEvents → fixtures → auto-finish → match events → settle → bracket ELO → stats joueurs WC |
| `job_sync_wc_bracket` | 1h | Recalcul ELO + Monte Carlo + repricing joueurs CDM |
| `job_sync_odds` | 30 min | Scraping cotes bookmakers (Betclic, Unibet, OddsPortal) |
| `job_sync_bzzoiro_*` | Variable | Sync stats joueurs, compos, fixtures depuis Bzzoiro |

### Frontend (`frontend/`)

Next.js 14 App Router, TypeScript, Tailwind CSS. Authentification via NextAuth (credentials). Port 3000.

### Infrastructure

- VPS OVH — 213.130.144.204
- Dokploy pour la gestion du compose project `ev0-compose-z5hvqt`
- Traefik pour le reverse proxy HTTPS
- Redis pour le cache et l'état des jobs

---

## Flux de données principal (marché championnat)

```
1. Bzzoiro API
   → bzz_events (fixtures), bzz_player_season_stats, bzz_players, bzz_teams

2. OddsPortal / Betclic / Unibet scraping
   → player_odds_snapshots, match_odds_snapshots

3. MarketXgService
   → xG d'équipe depuis les cotes WDW (ou Bzzoiro live xG)
   → xg_source: "market_implied" | "bzzoiro" | "override"

4. compute_player_shares()
   → npxg_share / xa_share par joueur (form-blendé)

5. allocate_player()
   → lambda_goal, lambda_assist, prob_goal, prob_assist

6. calculate_supersub_prob()
   → p_goal_supersub, p_assist_supersub

7. Recommendations engine
   → edge vs cotes bookmakers → table recommendations
```

## Flux de données CDM 2026

```
1. Bzzoiro API (league_api_id=27)
   → bzz_events WC2026, bzz_player_season_stats (stats nationales)

2. job_sync_wc_bracket()
   → ELO par nation (initialisé depuis TEAM_BM, updaté après chaque match)
   → Monte Carlo 50 000 simulations
   → wc2026_team_advancement (p_r32, p_r16, ..., p_winner, e_games)
   → compute_tournament_pricing() → wc2026_player_pricing

3. API /wc2026/pricing
   → cotes fair buteur/passeur CDM vs bookmakers
```
