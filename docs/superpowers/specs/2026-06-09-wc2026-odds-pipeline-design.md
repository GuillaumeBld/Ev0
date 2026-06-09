# WC2026 — Odds pipeline (fixtures + match odds + outrights)

**Date :** 2026-06-09
**Statut :** Approuvé

---

## Objectif

Alimenter le `MarketXgService` existant avec les odds de matchs CDM, et scraper les outrights nation (vainqueur, top4, top8) pour alimenter le moteur de pricing Spec 3. Zéro dépendance récurrente à The Odds API.

## Diagnostic

Le `MarketXgService` et l'`OddsScheduler` fonctionnent correctement pour les ligues de club. La logique adaptative existe déjà :

```python
> 6h avant KO   → scrape toutes les 2h   (7200s)
2h–6h avant KO  → scrape toutes les 30min (1800s)
< 2h avant KO   → scrape toutes les 2min  (120s)
```

`_league_key()` dans `odds_scheduler.py` contient déjà `"world_cup_2026"`. Le seul problème : **les fixtures CDM ne sont pas dans la table `fixtures`**, donc l'`OddsScheduler` ne les trouve pas. Une fois seedés, tout le pipeline existant fonctionne sans modification.

## Architecture

### 1. Seeder fixtures CDM

Script one-shot `backend/scripts/seed_wc2026_fixtures.py` :

- Source : calendrier FIFA officiel CDM 2026 hardcodé en JSON (176 matchs : 144 groupes + 32 KO)
- Format de chaque fixture :

```python
{
  "home_team": "Mexico",   # EN → FR via _TEAM_ALIASES
  "away_team": "Poland",
  "kickoff_utc": "2026-06-11T23:00:00Z",
  "league": "world_cup_2026",
  "group": "B",            # pour les matchs de groupes
  "round": "group"         # "group" | "r32" | "r16" | "qf" | "sf" | "final" | "3rd_place"
}
```

- Les matchs KO sans adversaires connus (ex. "Gagnant groupe A") sont insérés avec `home_team=NULL`, `away_team=NULL` et mis à jour une fois les qualifiés connus
- Utilise `INSERT ... ON CONFLICT DO NOTHING` basé sur `(home_team, away_team, kickoff_utc)`
- The Odds API utilisé **une seule fois** optionnellement pour valider les dates (1 crédit)

### 2. Extension scrapers match (h2h + totals + btts)

Les 3 scrapers existants doivent gérer la league key `"world_cup_2026"` :

**`betclic_grpc_scraper.py`** :
- Ajouter `"world_cup_2026"` dans la liste des leagues supportées
- Mapping compétition Betclic → `"world_cup_2026"` (ex. "FIFA Coupe du Monde 2026")

**`unibet_lvs_scraper.py`** :
- Idem : ajouter mapping compétition Unibet WC → `"world_cup_2026"`

**`pmu_scraper.py`** :
- Idem : mapping compétition PMU/Kambi WC → `"world_cup_2026"`

**Noms d'équipes :** les nations apparaissent déjà dans `_TEAM_ALIASES` (anglais → français). Ajouter les manquants si nécessaire lors des tests.

L'`OddsScheduler.tick()` prend le relai automatiquement dès que les fixtures sont en DB. Aucune modification de l'orchestrateur.

### 3. Scraper outrights nation

Nouveau module `backend/app/ingestion/wc2026/sync_wc_outrights.py`.

**Marchés cibles par book :**

| Market | Betclic | Unibet | PMU |
|---|---|---|---|
| Vainqueur CDM | ✓ | ✓ | ✓ |
| Finaliste (top 2) | ✓ | ✓ | ✓ |
| Demi-finaliste (top 4) | ✓ | ✓ | ✓ |
| Quart-de-finaliste (top 8) | ✓ | ✓ | ✓ |
| Passer la phase de groupes | ✓ | ✓ | ✓ |
| Meilleur buteur CDM | ✓ | ✓ | ✓ |
| Meilleur passeur CDM | ✓ | si dispo | si dispo |

**Table `wc2026_outright_odds` :**

```
id            SERIAL PK
nation        VARCHAR(60)         -- NULL pour les outrights joueur
player_name   VARCHAR(100)        -- NULL pour les outrights nation
market_type   VARCHAR(30)         -- "winner" | "top2" | "top4" | "top8" | "group_stage" | "top_scorer" | "top_assister"
bookmaker     VARCHAR(20)         -- "betclic" | "unibet" | "pmu"
odds          FLOAT               -- cote décimale
scraped_at    TIMESTAMPTZ NOT NULL DEFAULT now()
UNIQUE (nation, player_name, market_type, bookmaker)
```

**Job worker :**

```python
# worker.py — nouveau job
@scheduler.scheduled_job(IntervalTrigger(hours=6))
async def job_sync_wc_outright_odds():
    """Toutes les 6h : scrape outrights CDM sur les 3 books."""
    ...
```

Fréquence 6h suffit — les outrights bougent lentement sauf événement majeur (blessure star).

### 4. Pas de modification à MarketXgService

`MarketXgService.compute(fixture_id, session)` fonctionne tel quel dès que :
- Le fixture est dans la table `fixtures`
- Des `MatchOddsSnapshot` existent pour ce fixture

### Fichiers à créer / modifier

| Fichier | Action |
|---|---|
| `backend/scripts/seed_wc2026_fixtures.py` | Script one-shot seeder 176 fixtures |
| `backend/alembic/versions/037_wc2026_outright_odds.py` | Migration : table `wc2026_outright_odds` |
| `backend/app/models/wc2026_odds.py` | SQLAlchemy model `WC2026OutrightOdd` |
| `backend/app/ingestion/betclic_grpc_scraper.py` | Ajouter `world_cup_2026` dans leagues |
| `backend/app/ingestion/unibet_lvs_scraper.py` | Ajouter `world_cup_2026` dans leagues |
| `backend/app/ingestion/pmu_scraper.py` | Ajouter `world_cup_2026` dans leagues |
| `backend/app/ingestion/wc2026/sync_wc_outrights.py` | Scraper outrights 3 books |
| `backend/app/worker.py` | Ajouter `job_sync_wc_outright_odds` |
