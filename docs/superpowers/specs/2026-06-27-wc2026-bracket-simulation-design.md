# WC2026 Bracket Simulation & Team Advancement Pricing — Design

## Goal

Replace the current static `compute_expected_games()` (based on pre-tournament bookmaker outright odds) with a dynamic simulation engine that computes per-team tournament advancement probabilities from actual group results and an ELO model updated after every match. These probabilities flow directly into the existing player pricing pipeline (`lambda_remaining`) for improved accuracy as the tournament progresses.

## Architecture

```
bzz_events (résultats réels WC2026)
    ↓
[pricing/wc2026_bracket.py]  — NEW
  1. _build_groups()           → 12 groupes de 4 équipes
  2. _elo_from_team_bm()       → ELO initial depuis TEAM_BM
  3. _update_elo()             → mise à jour K=30 après chaque match joué
  4. simulate_group_stage()    → classements finaux (matchs restants simulés)
  5. _place_in_bracket()       → 32 équipes → slots R32 (table FIFA)
  6. _simulate_knockout()      → R32→R16→QF→SF→Finale
  compute_wc_advancement()     → public entry point
    ↓
wc2026_team_advancement  — NEW TABLE
    ↓
[pricing/wc2026_tournament.py]  — MODIFIED
  compute_expected_games() lit wc2026_team_advancement.e_games
  (fallback: cotes bookmakers si table vide)
    ↓
wc2026_player_pricing  — EXISTANT INCHANGÉ
```

**Tech stack:** Pure Python + NumPy (Monte Carlo), SQLAlchemy async, APScheduler (déjà en place).

---

## Section 1 — ELO Engine

### Initialisation depuis TEAM_BM

```python
import math
BASE_ELO = 1500
geo_mean = math.exp(sum(math.log(bm) for bm in TEAM_BM.values()) / len(TEAM_BM))
elo_init: dict[str, float] = {
    nation: round(BASE_ELO + 400 * math.log10(bm / geo_mean), 1)
    for nation, bm in TEAM_BM.items()
}
# Exemples: Spain ≈ 1678, France ≈ 1641, Iraq ≈ 1053
```

Le `geo_mean` sert d'ancre : toutes les équipes ont ELO centré sur 1500 en moyenne géométrique.

### Mise à jour après chaque match joué

```python
K = 30  # WC, conservateur (FIFA utilise 60, on reste prudent)

def _update_elo(elo: dict, team_a: str, team_b: str,
                score_a: int, score_b: int) -> dict:
    ea = 1 / (1 + 10 ** ((elo[team_b] - elo[team_a]) / 400))
    result_a = 1.0 if score_a > score_b else (0.5 if score_a == score_b else 0.0)
    elo[team_a] = elo[team_a] + K * (result_a - ea)
    elo[team_b] = elo[team_b] + K * ((1 - result_a) - (1 - ea))
    return elo
```

Les matchs sont appliqués dans l'ordre chronologique (`bzz_events.event_date ASC`, `league_api_id=27`, `status='finished'`).

### Probabilités de match

**Phase de groupes (W/D/L) :**
```python
def _match_proba_group(elo_a: float, elo_b: float) -> tuple[float, float, float]:
    e_a = 1 / (1 + 10 ** ((elo_b - elo_a) / 400))
    # Draw plus probable quand les équipes sont proches
    p_draw = 0.28 * (1 - abs(2 * e_a - 1))
    p_win_a = e_a * (1 - p_draw)
    p_win_b = (1 - e_a) * (1 - p_draw)
    return p_win_a, p_draw, p_win_b
```

**Matchs à élimination directe (pas de nul) :**
```python
def _match_proba_ko(elo_a: float, elo_b: float) -> float:
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))  # P(A avance)
```

---

## Section 2 — Classements de groupe

### Reconstruction des groupes depuis bzz_events

Les 12 groupes (A→L) sont déduits dynamiquement : deux équipes qui se rencontrent en rounds 1-3 (`round_number IN (1,2,3)`) appartiennent au même groupe. Pas de hardcoding des groupes.

```python
def _build_groups(events: list[BzzEvent]) -> dict[str, list[str]]:
    """Retourne {group_id: [team1, team2, team3, team4]}."""
    # Union-Find sur les équipes qui se rencontrent en phase de groupes
    ...
```

### Classement

Pour les matchs déjà joués, on lit les scores réels. Pour les matchs restants (group_round non encore terminé), on simule le résultat en tirant depuis `_match_proba_group()`.

Critères de classement (ordre de priorité) :
1. Points (3/1/0)
2. Différence de buts
3. Buts marqués
4. ELO courant (dernier recours)

### Qualification pour le R32

- Top 2 de chaque groupe (12 × 2 = 24 équipes)
- Meilleurs 8 troisièmes sur 12 (sélection par pts → GD → GF) → 32 équipes total

---

## Section 3 — Simulation du bracket (Monte Carlo)

### Placement R32 (table FIFA)

FIFA WC2026 spécifie exactement dans quel slot chaque 3e qualifié est placé selon les groupes d'origine des 8 qualifiés. Ce mapping est hardcodé comme constante dans `wc2026_bracket.py` (table de 66 combinaisons possibles → 8 slots).

Les slots R32 déjà connus (ex. `Brazil vs Japan`, `South Africa vs Canada`) sont lus directement depuis `bzz_events` quand les deux équipes sont déjà déterminées.

### Monte Carlo principal

```python
N_SIM = 50_000  # ~2s sur VPS

def simulate_bracket(elo: dict, group_results: dict, n_sim: int = N_SIM) -> dict[str, dict]:
    counters = {nation: {stage: 0 for stage in STAGES} for nation in elo}

    for _ in range(n_sim):
        # 1. Simuler matchs de groupe restants → classements finaux
        standings = _simulate_group_stage(group_results, elo)

        # 2. Sélectionner les 8 meilleurs 3es
        qualified_32 = _select_qualified(standings)

        # 3. Placer dans le bracket R32
        slots = _place_in_bracket(qualified_32, standings)

        # 4. Simuler R32 → R16 → QF → SF → Finale
        advancement = _simulate_knockout(slots, elo)

        # 5. Incrémenter compteurs
        for nation, stages_reached in advancement.items():
            for stage in stages_reached:
                counters[nation][stage] += 1

    # Normaliser → probabilités
    return {
        nation: {stage: count / n_sim for stage, count in stages.items()}
        for nation, stages in counters.items()
    }

STAGES = ["r32", "r16", "qf", "sf", "finalist", "winner"]
```

### Calcul de `e_games`

Même formule que l'existant, valeurs issues de la simulation :
```python
e_games = 3.0 + p_r32 + p_r16 + p_qf + 2.0 * p_sf + p_finalist
```
(3 matchs de groupe garantis + probabilités de chaque round supplémentaire)

---

## Section 4 — Nouveau modèle DB

### Table `wc2026_team_advancement`

```python
class WC2026TeamAdvancement(Base):
    __tablename__ = "wc2026_team_advancement"

    id: Mapped[int]          # PK
    nation: Mapped[str]      # clé canonique (= TEAM_BM keys), UNIQUE
    elo: Mapped[float]       # ELO courant après matchs joués
    p_r32: Mapped[float]     # P(passe les groupes)
    p_r16: Mapped[float]     # P(gagne match R32)
    p_qf: Mapped[float]      # P(atteint QF)
    p_sf: Mapped[float]      # P(atteint SF)
    p_finalist: Mapped[float]# P(finaliste)
    p_winner: Mapped[float]  # P(vainqueur)
    e_games: Mapped[float]   # espérance de matchs (alimente player pricing)
    n_sim: Mapped[int]       # N simulations utilisées
    computed_at: Mapped[datetime]
```

TRUNCATE + INSERT à chaque recalcul (même pattern que `wc2026_player_pricing`).

---

## Section 5 — Intégration worker

### Nouveau job `job_sync_wc_bracket()`

```python
async def job_sync_wc_bracket() -> None:
    logger.info("job_sync_wc_bracket: start")
    try:
        from app.pricing.wc2026_bracket import compute_wc_advancement
        from app.models.wc2026_advancement import WC2026TeamAdvancement
        async with async_session() as session:
            rows = await compute_wc_advancement(session)
            await session.execute(text("TRUNCATE TABLE wc2026_team_advancement RESTART IDENTITY"))
            for row in rows:
                session.add(WC2026TeamAdvancement(**row))
            await session.commit()
        logger.info("job_sync_wc_bracket: %d nations computed", len(rows))
    except Exception as exc:
        logger.exception("job_sync_wc_bracket failed: %s", exc)
```

### Position dans `job_settle_pipeline`

```python
async def job_settle_pipeline():
    # ... WC BzzEvents refresh, fixture status sync, auto-finish, match events, settle ...
    await job_sync_wc_bracket()           # ← NOUVEAU (avant wc_match_stats)
    await job_sync_wc_match_stats()       # stats joueurs + pricing joueur
```

Également appelé au startup (après `job_sync_wc_match_stats` dans la séquence initiale).

### Job horaire dédié (optionnel)

Un `IntervalTrigger(hours=1)` pour `job_sync_wc_bracket` peut être ajouté en complément du pipeline 30 min pour garantir un recalcul même si un tick rate saute.

---

## Section 6 — Modification `compute_expected_games()`

```python
async def compute_expected_games(db: AsyncSession) -> dict[str, float]:
    # Priorité 1 : simulation bracket (dynamique)
    rows = (await db.execute(
        select(WC2026TeamAdvancement.nation, WC2026TeamAdvancement.e_games)
    )).all()
    if rows:
        return {r.nation: r.e_games for r in rows}

    # Fallback : cotes bookmakers (comportement actuel)
    # ... code existant inchangé ...
```

---

## Fichiers à créer / modifier

| Action | Fichier | Description |
|--------|---------|-------------|
| CREATE | `backend/app/pricing/wc2026_bracket.py` | ELO + classements + Monte Carlo (~350 lignes) |
| CREATE | `backend/app/models/wc2026_advancement.py` | SQLAlchemy model WC2026TeamAdvancement |
| CREATE | `backend/alembic/versions/XXXX_wc2026_team_advancement.py` | Migration table |
| MODIFY | `backend/app/pricing/wc2026_tournament.py` | `compute_expected_games()` lit advancement table en priorité |
| MODIFY | `backend/app/worker.py` | `job_sync_wc_bracket()` + wiring pipeline + startup |
| MODIFY | `backend/app/models/__init__.py` | Import `WC2026TeamAdvancement` |

---

## Contraintes & limites

- **Performance** : 50 000 simulations × 16 groupes × matchs restants. Estimé ~2-3s sur VPS. Acceptable pour un job 30 min.
- **3e place FIFA table** : La table de placement des 8 meilleurs 3es dans le bracket R32 est hardcodée (constante). Elle ne change pas d'une édition à l'autre du format WC48.
- **Équipes sans TEAM_BM** : Si une équipe qualifiée n'a pas de TEAM_BM (impossible en l'état — 48 équipes couvertes), elle reçoit l'ELO moyen (1500).
- **Matchs terminés vs simulés** : Les matchs réels sont toujours lus depuis `bzz_events`, jamais resimulés.

## Related

- [[pricing-model]] — lambda et Poisson pour le pricing joueur
- [[worker]] — pipeline schedule et startup sequence
- [[database-schema]] — tables wc2026_*
- [[data-sources]] — Bzzoiro comme source des scores WC2026
