# Sources de données

Ev0 s'appuie sur deux sources externes pour les statistiques joueurs, un pipeline dédié pour les
cotes, et un scraper automatique pour les compositions officielles. Toutes les données transitent
par le worker backend et sont stockées en PostgreSQL avant d'être exploitées par le moteur de
pricing.

---

## 1. Statistiques joueurs — Bzzoiro API (source primaire)

**Bzzoiro** est la source de vérité pour l'ensemble des statistiques joueurs. C'est une API
privée (clé configurée via `BZZOIRO_API_KEY`) couvrant les 6 ligues cibles avec 30+ métriques
par match.

### Jobs de synchronisation

| Job | Tables alimentées | Fréquence |
|-----|-------------------|-----------|
| `job_sync_bzzoiro_reference` | `bzz_leagues`, `bzz_teams` | Quotidien 02:00 UTC |
| `job_sync_bzzoiro_players` | `bzz_players` | Quotidien 03:00 UTC |
| `job_aggregate_season_stats` | `bzz_player_season_stats` | Quotidien 04:00 UTC |
| `job_sync_bzzoiro_events` | `bzz_events` | Toutes les 6h |
| `job_sync_bzzoiro_player_stats` | `bzz_player_match_stats` | Toutes les 6h |
| `job_sync_bzzoiro_events_full_season` | `bzz_events` (saison entière) | Lundi 01:00 UTC |
| `job_sync_bzzoiro_player_stats_full_season` | `bzz_player_match_stats` (saison entière) | Lundi 02:30 UTC |
| `job_sync_bzzoiro_predictions` | `bzz_predictions` | Quotidien 07:45 UTC |

### Métriques collectées par match (`bzz_player_match_stats`)

- **Offensif :** buts, passes décisives, tirs, tirs cadrés, xG, xA, passes clés
- **Création :** centres totaux / précis, passes longues, dribbles
- **Physique :** duels gagnés/perdus, duels aériens, tacles, interceptions
- **Disciplinaire :** fautes commises/subies, cartons jaunes/rouges
- **Métriques dérivées** calculées à l'ingestion : `xg_per_shot`, `shot_accuracy`,
  `finishing_delta`, `xa_delta`, `cross_accuracy`, `duel_win_rate`

Les **agrégats saison** (`bzz_player_season_stats`) incluent les données de forme des
5 derniers matchs (`form_xg_5`, `form_assists_5`), utilisées dans le blend 60/40 du modèle
top-down.

### Fixtures — calendrier

Les fixtures sont créées automatiquement depuis les `BzzEvent` Bzzoiro par
`job_sync_fixtures` (quotidien 06:00 UTC). Ce job résout également les noms d'équipes
provisoires ("Winner QF1" → nom réel dès qu'il est connu).

---

## 2. Statistiques joueurs — StatsHub (source secondaire, gap-fill)

**Bzzoiro présente des trous** : certains joueurs ont des matchs sans stats ou des métriques
partiellement nulles. Ces trous dégradent le pricing et peuvent rendre des joueurs invisibles
dans le calculateur.

**StatsHub** est un agrégateur public (sans authentification) qui partage le même espace d'IDs
SofaScore que Bzzoiro. Il est utilisé exclusivement pour **combler les NULLs** — jamais pour
remplacer une valeur Bzzoiro existante.

### Stratégie COALESCE

```
Bzzoiro present + non-NULL → valeur Bzzoiro conservée
Bzzoiro present + NULL     → NULL remplacé par StatsHub
Bzzoiro absent             → nouvelle row créée depuis StatsHub
```

### Champs comblés par StatsHub

`shots_on_target`, `total_shots`, `expected_goals`, `expected_assists`, `key_pass`,
`touches`, `rating`, `aerial_won`, `goal_assist`, `goals`, `fouls`, `was_fouled`,
`total_cross`, `accurate_cross`, `dispossessed`, `interception`

### Jobs de synchronisation

| Job | Périmètre | Fréquence |
|-----|-----------|-----------|
| `job_sync_statshub_gap_fill` | Équipes avec match dans les 14j | Quotidien 08:15 UTC |
| `job_sync_statshub_full_season` | Toutes les équipes des 6 ligues | Lundi 03:00 UTC |

Chaque job se termine par un appel automatique à `job_aggregate_season_stats` pour que
les agrégats saison reflètent immédiatement les données fraîchement comblées.

---

## 3. Compositions officielles — StatsHub (lineup scraper)

StatsHub publie les compositions officielles **jusqu'à 1h avant le coup d'envoi**. Ev0 scrape
ces compos automatiquement pour alimenter la section Compos et le calculateur.

### Principe de fonctionnement

1. **Poll** — `job_poll_statshub_lineups` interroge l'endpoint `lineup-status` toutes les
   15 minutes dans la fenêtre **J-2h → J-10min** avant chaque KO.
   - Réponse possible : `"none"` / `"predicted"` / `"confirmed"`
2. **Fetch** — Dès qu'une compo passe à `"confirmed"`, les 11 titulaires sont récupérés via
   l'endpoint `team/players/performance` (champ `currentFixturePlayerStats`).
3. **Upsert** — La compo est insérée dans `team_lineups` avec `lineup_type="official"`,
   `source="statshub"`. Elle prend automatiquement la priorité maximale dans le resolver.

### Priorité des compositions (resolver)

| Priorité | Type | Source |
|----------|------|--------|
| 0 (plus haute) | `official` | StatsHub (automatique) |
| 1 | `probable_manual` | Saisie manuelle via le dashboard |
| 2 (fallback) | `last_known` | Dernière compo officielle du match précédent |

### Impact sur le calculateur

Sans compo officielle, le pricing est calculé sur l'ensemble des joueurs connus d'une équipe
(~30 joueurs). Avec une compo `official`, le xG d'équipe est redistribué uniquement sur les
11 titulaires — probabilités et edges sont significativement plus précis.

### Statut

> ⚠️ Ce job est **en cours d'implémentation**. La spec est disponible dans
> `docs/superpowers/specs/2026-04-28-statshub-lineup-scraper-design.md`.
> En attendant, les compos peuvent être saisies manuellement via le dashboard → **Compos**.

---

## 4. Cotes de marché — solveur Poisson (xG équipe)

Les cotes H2H (1×2), Over/Under 2.5 et BTTS sont collectées pour alimenter le solveur Poisson
qui calcule `λ_home` et `λ_away` (buts attendus par équipe).

### Sources et méthode

| Bookmaker | Méthode |
|-----------|---------|
| Betclic | gRPC scraping (parser protobuf maison) |
| Unibet | API LVS HTTP |

Les deux scrapers fonctionnent en parallèle. Les cotes sont stockées dans
`match_odds_snapshots`. Un snapshot est valide pendant **30 minutes** (au-delà, le solveur
passe en mode dégradé).

### Cadence adaptative (`job_odds_scheduler_tick`, toutes les 60s)

Le scheduler ajuste automatiquement la fréquence selon la distance au coup d'envoi :

| Distance au KO | Intervalle de scrape |
|----------------|---------------------|
| > 6h | Toutes les 2h |
| 2h → 6h | Toutes les 30 min |
| 5min → 2h | Toutes les 2 min |
| < 5min | Stop |

---

## 5. Cotes joueurs — buteur / passeur

Les cotes anytime goalscorer et anytime assist sont scrappées directement depuis les
bookmakers :

| Bookmaker | Méthode |
|-----------|---------|
| Unibet | API LVS HTTP |
| Betclic | HTTP scraping |

Ces cotes sont stockées dans `player_odds_snapshots`. Pour chaque paire (joueur, marché),
seule la meilleure cote disponible parmi les deux bookmakers est retenue lors du calcul des
recommandations.

---

## 6. Données calculées en interne (non scrappées)

| Donnée | Méthode |
|--------|---------|
| Probabilités fair joueur | Modèle top-down Poisson (`λ` équipe × share joueur) |
| `finishing_mult`, `creation_mult_v2` | Calculés depuis les stats Bzzoiro/StatsHub |
| Shares buteur/passeur | Blend 60/40 (saison × forme 5 matchs) |
| Kelly fractions | Calcul algébrique pur |
| Backtest rewards | Simulation sur données historiques |
