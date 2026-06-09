# WC2026 — Moteur de pricing outright buteur / passeur

**Date :** 2026-06-09
**Statut :** Approuvé

---

## Objectif

Calculer des cotes ev0 "fair" pour les marchés outright "meilleur buteur CDM" et "meilleur passeur CDM" (et leurs déclinaisons par nation), en combinant :
- les xG par match de chaque équipe (via `MarketXgService`)
- la probabilité d'avancement au tournoi (via outrights Pinnacle/books)
- les compos attendues (via Spec 1)
- les stats de club des joueurs (via `bzz_player_season_stats`)

Le résultat est un tableau ev0 vs odds books avec edge calculé.

---

## Architecture globale

```
Outrights nation (Spec 2)      xG match (Spec 2)         Compos (Spec 1)
P(nation → tour R)             λ_home / λ_away            expected_minutes
        │                             │                           │
        ▼                             ▼                           ▼
 E[matchs joués]          λ_équipe par match          share joueur
        │                             │                (stats Bzzoiro)
        └──────────────┬──────────────┘
                       ▼
             λ_joueur_total = Σ_matchs [ P(match joué) × λ_équipe × share × (mins/90) ]
                       │
                       ▼
          Monte Carlo 10 000 simulations
          → P(joueur X marque le plus) → cote fair ev0
                       │
                       ▼
          Tableau : joueur | ev0 | Betclic | Unibet | PMU | Edge%
```

---

## Modèle de pricing joueur

### Étape 1 — Probabilité d'avancement par tour

Pour chaque nation N et chaque tour T ∈ {group_1, group_2, group_3, r16, qf, sf, final} :

```python
# Dérivé des outrights scrapés dans wc2026_outright_odds
P(N joue group_1) = 1.0
P(N joue group_2) = 1.0
P(N joue group_3) = 1.0
P(N joue r16)     = P(N passe les groupes)  = 1 / devig([betclic, unibet, pmu]["group_stage"][N])
P(N joue qf)      = P(N atteint top 8)       = 1 / devig([...]["top8"][N])
P(N joue sf)      = P(N atteint top 4)       = 1 / devig([...]["top4"][N])
P(N joue final)   = P(N atteint top 2)       = 1 / devig([...]["top2"][N])
```

Devig multiplicatif sur les 3 books disponibles (moyenne pondérée), avec fallback sur 1 book si les autres manquent.

### Étape 2 — xG de la nation par match

Pour les matchs de groupes (connus) : `λ_N_m = MarketXgService.compute(fixture_id)`.

Pour les matchs KO (adversaire inconnu) : `λ_N_KO = moyenne des λ de la nation sur les 3 matchs de groupe` (approximation — sera affinée une fois les matchs KO scrapés).

### Étape 3 — Share joueur (modèle existant adapté)

Réutilise la logique de `goalscorer.py` / `assist.py` :

```python
# Buteur
blended_xg   = 0.60 × xg_per_90 + 0.40 × form_xg_rate
weight_i     = blended_xg × (expected_minutes / 90)   # intègre les minutes dans le share
share_i      = weight_i / max(Σ weights équipe, λ_N_m) # ∈ [0,1], minutes déjà incluses
finishing    = clamp(normalize(shot_accuracy, xg_per_shot, avg_rating), 0.70, 1.50)
conversion   = clamp(goals / xg, 0.75, 1.40) si ≥5 matchs, sinon 1.0

λ_i_m = share_i × λ_N_m × finishing × conversion   # minutes déjà dans share_i, pas en double

# Passeur (même logique avec xa_per_90, key_pass_per_90, profil créateur)
λ_assist_i_m = share_xa_i × (λ_N_m × 0.65) × creation_mult × xa_conversion   # idem, minutes dans share_xa_i
```

`expected_minutes_i` : issu de `wc2026_expected_lineups`, résolu avec fallback context → default.

### Étape 4 — λ total sur le tournoi

```python
λ_i_total = Σ_m [ P(nation joue match m) × λ_i_m ]
```

Somme sur les 7 tours potentiels (3 groupes + R16 + QF + SF + F), chaque terme pondéré par la probabilité d'y être.

### Étape 5 — Monte Carlo top scorer / passeur

```python
N_SIMS = 10_000

counts_scorer  = Counter()
counts_assister = Counter()

for _ in range(N_SIMS):
    # Tirer les buts/passes de chaque joueur selon sa loi de Poisson
    goals   = {p: poisson.rvs(λ_goal[p])   for p in all_players}
    assists = {p: poisson.rvs(λ_assist[p]) for p in all_players}

    counts_scorer[max(goals,   key=goals.get)]   += 1
    counts_assister[max(assists, key=assists.get)] += 1

# Probabilités
P_top_scorer   = {p: counts_scorer[p]   / N_SIMS for p in all_players}
P_top_assister = {p: counts_assister[p] / N_SIMS for p in all_players}

# Cotes fair ev0
fair_odds_scorer   = {p: 1 / P_top_scorer[p]   if P_top_scorer[p]   > 0 else 9999 for p in all_players}
fair_odds_assister = {p: 1 / P_top_assister[p] if P_top_assister[p] > 0 else 9999 for p in all_players}
```

Simulation rapide même pour 400+ joueurs (loi de Poisson = numpy vectorisable).

### Edge

```python
edge_pct = (book_best_odds / ev0_fair_odds - 1) × 100
# > 0 → value bet, < 0 → book a l'avantage
```

`book_best_odds` = meilleure cote parmi Betclic / Unibet / PMU.

---

## API endpoints

```
GET /api/v1/wc2026/outright/top-scorer
    ?nation=France        (optionnel — restreint à la nation)
    ?min_prob=0.005       (optionnel — filtre joueurs < 0.5% de chance)
    → {
        computed_at: datetime,
        players: [
          {
            player_name, nation, club, position,
            lambda_total, prob, fair_odds_ev0,
            betclic_odds, unibet_odds, pmu_odds,
            best_book_odds, edge_pct
          }
        ]
      }

GET /api/v1/wc2026/outright/top-assister
    (mêmes paramètres)

POST /api/v1/wc2026/outright/recompute
    → déclenche un recalcul complet (Monte Carlo) en tâche de fond
    → retourne { task_id, status: "queued" }
```

### Cache

Le calcul Monte Carlo prend ~2-5 secondes pour 400 joueurs. Résultat mis en cache dans Redis ou en mémoire (`app_state`) avec TTL de 30 minutes. `POST /recompute` invalide le cache.

---

## UI — Page `/dashboard/wc2026/outright`

```
Tabs: [Meilleur buteur] [Meilleur passeur]
      [Buteur par nation ▼]

┌────────────────────────────────────────────────────────────────────────┐
│  Joueur          Nation    Pos  λ total  ev0    Betclic Unibet PMU Edge│
│  ─────────────────────────────────────────────────────────────────     │
│  Mbappé          France    FWD   3.41   8.50    11.00   10.00  12.0 +29%│
│  Vinicius Jr     Brésil    FWD   3.12   9.00    13.00   12.00  14.0 +44%│
│  Bellingham      Angleterre MID  2.87  11.00    15.00   14.00  15.0 +36%│
│  ...                                                                    │
└────────────────────────────────────────────────────────────────────────┘

[ Recalculer ]   Dernière mise à jour : il y a 12 min
```

- Trié par `ev0` asc (les plus probables en haut) par défaut
- Edge en vert si > 0%, rouge si < 0%
- Click sur une ligne → fiche joueur avec détail : λ par match, expected_minutes, stats Bzzoiro utilisées

---

## Fichiers à créer / modifier

| Fichier | Action |
|---|---|
| `backend/app/pricing/wc2026_player.py` | Calcul λ_total par joueur (buteur + passeur) |
| `backend/app/pricing/wc2026_outright.py` | Monte Carlo top scorer / passeur |
| `backend/app/api/wc2026_outright.py` | Endpoints `/wc2026/outright/...` |
| `backend/app/main.py` | Enregistrer le router outright |
| `frontend/src/app/dashboard/wc2026/outright/page.tsx` | Page tableau outrights |
| `frontend/src/components/wc2026/OutrightTable.tsx` | Table ev0 vs books avec edge |

---

## Dépendances

- **Spec 1 (compos)** — `wc2026_expected_lineups` doit être peuplé avant de calculer
- **Spec 2 (odds pipeline)** — `MarketXgService` doit avoir des snapshots WC, et `wc2026_outright_odds` doit être peuplé pour les probabilités d'avancement
- **`bzz_player_season_stats`** — stats de club 2025-2026 (déjà en DB et à jour)

## Cas limites

| Situation | Comportement |
|---|---|
| Nation sans compo type saisie | Share égal entre tous les joueurs du squad (fallback dégradé) |
| Match KO sans xG dispo | Utilise la moyenne des xG des 3 matchs de groupe de la nation |
| Joueur sans stats Bzzoiro | Share basé sur les moyennes de poste (mêmes defaults que `goalscorer.py`) |
| P(top scorer) = 0 | `fair_odds = 9999` (affiché `—` en UI) |
| Outright odds manquantes pour un tour | P(avancement) estimé par interpolation linéaire des tours disponibles |
