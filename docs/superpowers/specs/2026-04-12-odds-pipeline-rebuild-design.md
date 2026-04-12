# Odds Pipeline Rebuild — Design Spec
**Date:** 2026-04-12
**Status:** Approved

---

## 1. Objectif

Remplacer l'intégralité du pipeline d'ingestion des cotes par une architecture propre, fiable et
réactive. Le pipeline actuel est mort (OddsPortal bloqué Cloudflare, The Odds API payante/quota
épuisé, fallbacks silencieux). Le calculateur retourne systématiquement "No market odds available".

**Résultat attendu :**
- Cotes match (1X2 / Over-Under / BTTS) scrapées en continu → xG implicites du marché toujours frais
- Cotes joueurs (goalscorer / assist) scrapées en continu → détection d'edge en temps réel
- Fréquence adaptative basée sur la distance au KO (3min dans la fenêtre pré-match)
- Absence de données = état explicite "données indisponibles", jamais de valeur périmée servie

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  INTELLIGENT SCHEDULER                   │
│  (piloté par bzz_events.event_date — distance au KO)    │
└──────────────┬──────────────────────────┬───────────────┘
               │                          │
               ▼                          ▼
┌─────────────────────┐      ┌─────────────────────┐
│  Betclic gRPC       │      │  Unibet LVS         │
│  (1 appel = tout)   │      │  (1 appel = tout)   │
│                     │      │                     │
│  → 1X2 / OU / BTTS  │      │  → 1X2 / OU / BTTS  │
│  → Goalscorer       │      │  → Goalscorer       │
│  → Assist           │      │  → Assist           │
└──────────┬──────────┘      └──────────┬──────────┘
           │                            │
           └──────────┬─────────────────┘
                      ▼
         ┌────────────────────────┐
         │  match_odds_snapshots  │  ← 1X2 / OU / BTTS  (existante)
         │  player_odds_snapshots │  ← Goalscorer / Assist (nouvelle)
         └──────────┬─────────────┘
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
  ┌──────────────┐    ┌─────────────────┐
  │ MarketXgSvc  │    │  Edge detector  │
  │ (solver      │    │  fair_odds vs   │
  │  Poisson)    │    │  book_odds      │
  └──────┬───────┘    └────────┬────────┘
         │                     │
         └──────────┬──────────┘
                    ▼
            ┌──────────────┐
            │  Calculateur │
            │  Recommanda- │
            │  tions       │
            └──────────────┘
```

---

## 3. Scheduler intelligent

### Logique de fréquence (basée sur `bzz_events.event_date`)

| Distance au KO       | Intervalle de scraping |
|----------------------|------------------------|
| > 6h                 | Toutes les 2h          |
| 2h → 6h              | Toutes les 30min       |
| < 2h                 | Toutes les 3min        |
| Match commencé (> 0) | Stop                   |

### Implémentation

- Remplacement de `MarketScrapeScheduler` (actuel : token bucket OddsPortal) par un nouveau
  `OddsScheduler` qui :
  1. Charge les fixtures à venir depuis `bzz_events` (status != finished/cancelled)
  2. Pour chaque fixture, calcule l'intervalle requis selon la table ci-dessus
  3. Compare avec `last_scraped_at` en DB — déclenche le scraping si l'intervalle est dépassé
  4. Tourne toutes les 60 secondes (job APScheduler) pour ne jamais rater une fenêtre

- Le scheduler déclenche **les deux scrapers en parallèle** (Betclic + Unibet) pour chaque
  fixture due.

- Table de tracking : `odds_scrape_state`
  ```
  fixture_id          INTEGER PK FK → fixtures.id
  last_scraped_at     TIMESTAMPTZ
  next_scrape_at      TIMESTAMPTZ   (calculé après chaque run)
  betclic_ok          BOOLEAN
  unibet_ok           BOOLEAN
  ```

---

## 4. Extension des scrapers

### 4.1 BetclicGrpcScraper

L'endpoint `GetMatchWithNotification` retourne déjà tous les marchés en un seul appel protobuf.
Extension du parser existant :

**`_classify_market` — nouveaux types :**
```
"résultat du match" / "1x2" / "vainqueur"  → "h2h"
"total de buts" / "nombre de buts"          → "totals"
"les deux équipes marquent" / "btts"        → "btts"
```

**`_parse_match_proto` — nouveaux champs extraits :**
- h2h : sélections `home / draw / away`
- totals : sélections `over_1.5 / under_1.5 / over_2.5 / under_2.5 / over_3.5 / under_3.5`
- btts : sélections `yes / no`

Aucun appel réseau supplémentaire.

### 4.2 UnibetLVSScraper

Extension de `_MARKET_TYPES` avec les IDs match-level (à valider par audit réseau avant
implémentation — les IDs ci-dessous sont des estimations à confirmer) :

```python
_MARKET_TYPES: dict[int, str] = {
    # Match-level (à confirmer)
    1:         "h2h",
    9:         "totals",
    8:         "btts",
    # Player props (confirmés)
    31:        "goalscorer",
    4:         "goalscorer",
    100002524: "assist",
}
```

L'audit réseau (inspection des requêtes sur un match live) est la **première étape de
l'implémentation**.

### 4.3 Output unifié : MatchScrapeResult

Les deux scrapers retournent le même dataclass :

```python
@dataclass
class MatchScrapeResult:
    fixture_id: int
    home_team: str
    away_team: str
    kickoff_utc: datetime | None
    league: str
    bookmaker: str
    scraped_at: datetime
    # Match-level
    h2h: dict | None          # {home, draw, away}
    totals: dict | None       # {over_2.5, under_2.5, ...}
    btts: dict | None         # {yes, no}
    # Player props
    goalscorer: list[PlayerOdds]
    assist: list[PlayerOdds]
```

---

## 5. Modèle de données

### 5.1 `match_odds_snapshots` (existante — structure inchangée)

Alimentée par les scrapers étendus au lieu de OddsPortal/Betclic-match/Unibet-Kambi.
Contrainte unique `uq_match_odds_snapshot` conservée.

### 5.2 `player_odds_snapshots` (nouvelle)

```sql
CREATE TABLE player_odds_snapshots (
    id              SERIAL PRIMARY KEY,
    fixture_id      INTEGER NOT NULL REFERENCES fixtures(id),
    bookmaker       VARCHAR(30) NOT NULL,
    market_type     VARCHAR(20) NOT NULL,   -- goalscorer / assist
    player_name     VARCHAR(200) NOT NULL,
    odds            FLOAT NOT NULL,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_player_odds UNIQUE (fixture_id, bookmaker, market_type, player_name)
);
CREATE INDEX ix_player_odds_fixture ON player_odds_snapshots(fixture_id);
```

Remplace la table `odds` actuelle (alimentée par The Odds API — morte).

### 5.3 `odds_scrape_state` (nouvelle)

```sql
CREATE TABLE odds_scrape_state (
    fixture_id      INTEGER PRIMARY KEY REFERENCES fixtures(id),
    last_scraped_at TIMESTAMPTZ,
    next_scrape_at  TIMESTAMPTZ,
    betclic_ok      BOOLEAN DEFAULT FALSE,
    unibet_ok       BOOLEAN DEFAULT FALSE
);
```

---

## 6. Pipeline xG — MarketXgService

`MarketXgService` est **conservé sans modification** — sa logique de solver Poisson est correcte.

Seul changement : `MAX_SNAPSHOT_AGE` passe de `3h` à `30min`. Avec un scraping toutes les 3min
en fenêtre pré-match, une snapshot vieille de plus de 30min indique un problème de scraping réel
→ retourner `None` (état explicite) est le bon comportement.

---

## 7. Comportement "données indisponibles"

Si `MarketXgService.compute()` retourne `None` :
- Le calculateur affiche un état explicite : **"Données de marché indisponibles — scraping en attente"**
- La date/heure du dernier scrape réussi est affichée (depuis `odds_scrape_state`)
- Aucun calcul de fair odds n'est effectué
- Aucune recommandation n'est générée pour ce fixture

---

## 8. Code mort à supprimer

| Fichier / Table | Raison |
|---|---|
| `app/ingestion/oddsportal_scraper.py` | OddsPortal bloqué Cloudflare |
| `app/ingestion/oddsportal_fixture_matcher.py` | Plus de OddsPortal |
| `app/ingestion/oddsportal_league_discoverer.py` | Plus de OddsPortal |
| `app/ingestion/market_scrape_chain.py` | Remplacé par OddsScheduler |
| `app/ingestion/betclic_match_scraper.py` | Remplacé par extension gRPC |
| `app/ingestion/unibet_match_scraper.py` | Remplacé par extension LVS |
| `app/ingestion/odds.py` | The Odds API morte |
| `app/ingestion/direct_scrapers.py` | Façade dupliquant MatchOdds/SelectionOdds |
| `app/services/market_scrape_scheduler.py` | Remplacé par OddsScheduler |
| Table `oddsportal_poll_state` | Plus de OddsPortal |
| Table `odds` | Remplacée par player_odds_snapshots |
| Jobs `job_oddsportal_scheduler_tick` | Remplacé |
| Jobs `job_discover_oddsportal_urls` | Remplacé |
| Jobs `job_snapshot_odds` | The Odds API |
| Jobs `job_snapshot_direct_odds` | Remplacé |

---

## 9. Nouveaux jobs worker

| Job | Fréquence | Description |
|---|---|---|
| `job_odds_scheduler_tick` | Toutes les 60s | Déclenche les scrapers pour les fixtures dues |

Remplace 4 jobs existants par 1 seul.

---

## 10. PMU — hors scope (Phase 2)

PMU nécessite un audit réseau préalable (inspection des requêtes de leur application mobile /
site web sur un match live) pour identifier les endpoints, le format d'authentification, et les
IDs de marchés. À implémenter dans un PR dédié après cet audit.

ParionsSport : même approche, phase 3.

---

## 11. Plan de migration

1. **Audit Unibet LVS** — identifier les markettypeId pour 1X2 / OU / BTTS sur un match live
2. **Alembic** — créer `player_odds_snapshots` + `odds_scrape_state`, drop `odds` + `oddsportal_poll_state`
3. **Étendre BetclicGrpcScraper** — parser h2h / totals / btts depuis le protobuf existant
4. **Étendre UnibetLVSScraper** — ajouter market type IDs confirmés
5. **Écrire OddsScheduler** — logique de fréquence adaptative
6. **Mettre à jour worker** — remplacer 4 jobs par `job_odds_scheduler_tick`
7. **Mettre à jour MarketXgService** — MAX_SNAPSHOT_AGE = 30min
8. **Supprimer code mort** — 9 fichiers + 2 tables + 4 jobs
9. **Test end-to-end** sur un match live (vérifier que h2h + totals + btts sont bien scrapés)
10. **Deploy VPS** — rebuild backend + worker uniquement (`--no-deps`)

---

## 12. Hors scope

- PMU / ParionsSport (Phase 2/3)
- Refonte du pricing engine (validation modèle — sujet séparé)
- Frontend calculateur (affichage "données indisponibles" — UI à designer séparément)
- Marchés outrights / garantie 2 buts d'avance (extension future des scrapers)
