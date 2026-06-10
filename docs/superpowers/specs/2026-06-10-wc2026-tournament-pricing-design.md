# WC2026 Tournament Pricing Design

**Goal:** Price player goal and assist markets for WC2026 (cuts ≥1/2/3/4, top scorer, top assister) by distributing pre-tournament team BM via the existing per-match pricing engine, storing results in DB, and surfacing edges vs scraped bookmaker outrights.

**Architecture:** Reuse `compute_player_shares` + `allocate_player` (Option B) — pass team BM directly as `lambda_team`, bypassing `MarketXgService` entirely. Monte Carlo (50k sims) for top scorer/assister probabilities. Results stored in `wc2026_player_pricing`, truncated and reinserted on each recompute. Edges computed at query time via join on `wc2026_outright_odds`.

**Tech Stack:** Python (pricing modules, SQLAlchemy async), FastAPI, PostgreSQL, Next.js 14, Tailwind.

---

## Data Inputs

### Team BM
Hardcoded dict in `backend/app/ingestion/wc2026/team_bm.py` — pre-tournament expected goals for the full WC2026 per nation (48 entries). Treated as `lambda_team` in the pricing engine. Example:

```python
TEAM_BM: dict[str, float] = {
    "Spain": 13.03,
    "Brazil": 12.33,
    "Germany": 11.78,
    ...
}
```

Nation names must match `wc2026_squad_players.nation` (English names from DB).

### Player Expected Minutes
From `wc2026_expected_lineup_players` joined to `wc2026_expected_lineups` (context=`default`). Used to compute `mins_ratio = expected_minutes / 90` which weights each player's share. Players not in a lineup get `expected_minutes=0` and contribute zero share (excluded from output).

### Player Club Stats
From `bzz_player_season_stats` + `bzz_players` via `_load_national_team_players(db, national_team_api_id)` — the exact same loader already used for international fixtures. Returns npxg_per_90, xa_per_90, finishing metrics, etc.

`national_team_api_id` is resolved from the nation name via a single query:
```sql
SELECT DISTINCT national_team_api_id
FROM bzz_players
WHERE lower(national_team_name) = lower(:nation)
LIMIT 1
```
Nations with no matching Bzzoiro national team are skipped (warning logged). A `WC2026_NATION_NAME_ALIASES` dict handles divergences between DB nation names and Bzzoiro names (e.g. `"United States" → "USA"`).

---

## Pricing Engine

### Player λ (goals)
```
shares = compute_player_shares(players, team, lambda_team=BM)
alloc  = allocate_player(share, BM, is_pen_taker, budget_assists)
λ_goals = alloc.lambda_total
```

This is identical to per-match pricing with `lambda_team = BM` instead of market-implied xG. The finishing_multiplier and pen-taker bonus apply unchanged. λ_goals encodes the player's expected goal contribution over the full tournament.

### Player λ (assists)
```
λ_assists = alloc.lambda_assist
```

Already computed by `allocate_player` via `calculate_assist_lambda`.

### Cuts (Poisson CDF)
For goals:
- `P(≥k goals) = 1 - poisson.cdf(k-1, λ_goals)` for k = 1, 2, 3, 4
- `fair_odds_kg = 1 / P(≥k goals)`

For assists:
- `P(≥k assists) = 1 - poisson.cdf(k-1, λ_assists)` for k = 1, 2, 3

### Top Scorer / Top Assister (Monte Carlo)
```
N_SIM = 50_000
samples_goals[player_i]   ~ Poisson(λ_goals[i])    shape (N_SIM,)
samples_assists[player_i] ~ Poisson(λ_assists[i])   shape (N_SIM,)

top_scorer_idx   = argmax(samples_goals,   axis=0)
top_assister_idx = argmax(samples_assists, axis=0)

p_top_scorer[i]   = count(top_scorer_idx == i)   / N_SIM
p_top_assister[i] = count(top_assister_idx == i) / N_SIM
```

Run with numpy vectorized across all ~1200 players simultaneously. Expected runtime < 3s on VPS.

---

## Storage

### Table: `wc2026_player_pricing`

```sql
CREATE TABLE wc2026_player_pricing (
    id              SERIAL PRIMARY KEY,
    nation          VARCHAR(60)  NOT NULL,
    player_name     VARCHAR(100) NOT NULL,
    position        VARCHAR(10),
    lambda_goals    FLOAT        NOT NULL,
    lambda_assists  FLOAT        NOT NULL,
    p_1g  FLOAT, p_2g  FLOAT, p_3g  FLOAT, p_4g  FLOAT,
    p_1a  FLOAT, p_2a  FLOAT, p_3a  FLOAT,
    fair_1g  FLOAT, fair_2g  FLOAT, fair_3g  FLOAT, fair_4g  FLOAT,
    fair_1a  FLOAT, fair_2a  FLOAT, fair_3a  FLOAT,
    p_top_scorer    FLOAT,
    p_top_assister  FLOAT,
    fair_top_scorer   FLOAT,
    fair_top_assister FLOAT,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

No unique constraint — table is **truncated and bulk-inserted** on every recompute. `computed_at` is the same timestamp for all rows in a batch (set at compute time).

---

## API

Router: `backend/app/api/wc2026_pricing.py`, prefix `/wc2026/pricing`.

### `POST /wc2026/pricing/compute`

Triggers full recompute for all 48 nations:
1. Load BM dict
2. For each nation: load lineup (`default` context) + Bzzoiro stats via `_load_national_team_players`
3. `compute_player_shares` + `allocate_player` for each player
4. Compute cuts via Poisson CDF
5. Monte Carlo for top scorer + assister across all players
6. Truncate `wc2026_player_pricing`, bulk insert
7. Return `{ "players_computed": N, "nations_computed": 48, "duration_s": X }`

Nations with no lineup in DB are skipped (not an error). Nations with no Bzzoiro match fall back to positional priors (existing engine behaviour).

### `GET /wc2026/pricing/players`

Query params: `nation` (optional), `position` (optional, FW/MF/DF), `min_lambda` (optional float).

Response: list of players ordered by `lambda_goals DESC`, each enriched with:
- `bk_top_scorer`: best odds from `wc2026_outright_odds` where `market_type='top_scorer'` and player_name matches (NFKD normalization)
- `bk_top_assister`: same for `market_type='top_assister'`
- `edge_top_scorer`: `(bk_top_scorer / fair_top_scorer) - 1` if both exist, else null
- `edge_top_assister`: same

Name matching uses the same `_norm_name` NFKD helper already in `wc2026_lineups.py`.

---

## Frontend

**Page:** `frontend/src/app/dashboard/wc2026/pricing/page.tsx`

Two tabs: **Buts** and **Passes**.

**Buts tab columns:**
| Joueur | Nation | Pos | λ buts | ≥1 | ≥2 | ≥3 | ≥4 | Top buteur | Edge |
|--------|--------|-----|--------|----|----|----|----|------------|------|

**Passes tab columns:**
| Joueur | Nation | Pos | λ passes | ≥1 | ≥2 | ≥3 | Top passeur | Edge |
|--------|--------|-----|----------|----|----|-----|-------------|------|

- Cotes affichées (≥k): cotes **justes** du modèle (pas probabilités)
- Top buteur/passeur: `cote modèle / cote BK` si dispo, sinon cote modèle seule
- Edge: couleur verte si > 0, rouge si < 0, absent si pas de cote BK
- Bouton **Recalculer** → POST `/wc2026/pricing/compute` avec spinner
- Filtres : nation (select), position (FW/MF/DF), λ minimum (slider ou input)
- Timestamp "Calculé le ..." affiché sous le bouton

**Composants:**
- `frontend/src/app/dashboard/wc2026/pricing/page.tsx` — page principale
- `frontend/src/components/wc2026/PricingTable.tsx` — tableau réutilisable (buts + passes partagent la même structure)

---

## Migration

One Alembic migration: `add_wc2026_player_pricing_table`. Creates `wc2026_player_pricing` with no foreign keys (standalone table, player names are strings).

---

## Out of Scope

- Sync automatique / CRON du recalcul (manuel uniquement)
- Cuts de buts par match individuel (phase groupes vs KO)
- Marchés équipe (top 4, vainqueur) — déjà couverts par `wc2026_outright_odds`
- Fix bug MarketXgService / Pinnacle (sujet séparé)
