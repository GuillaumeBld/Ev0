# Lot 2a — Raccord saison, substitutions, settlement avec-sub, métriques : Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raccorder les derniers hardcodes "2025-2026" au season_service (deadline 1er août), persister les remplacements dans `match_events`, livrer le module de settlement "avec sub" et les métriques d'évaluation — les briques que le rejeu d'Alpha (lot 2b) assemblera.

**Architecture:** Les endpoints joueurs et la création de fixtures résolvent la saison via `season_service` (lot 1). `sync_incidents` cesse de jeter les incidents de type substitution : ils sont stockés dans `match_events` avec une nouvelle colonne `related_player_name` (migration 047). Un nouveau package `app/evaluation/` contient le settlement (chaîne de remplacement transitive + règlement des 4 marchés) et les métriques pures (log-loss, Brier, calibration, delta apparié). Un script échelonné backfille les substitutions 2025-26.

**Tech Stack:** Python 3.12, FastAPI/SQLAlchemy 2 async, Alembic, pytest(+asyncio), httpx. Spec : `docs/superpowers/specs/2026-07-18-transition-saison-alpha-beta-design.md` (§3.2). Dépend du lot 1 (branche `feat/lot1-fondations-saison`, PR #19).

## Global Constraints

- **`backend/app/pricing/team_xg.py` GELÉ** — zéro modification.
- **Branche** : `feat/lot2a-raccord-settlement`, créée depuis `feat/lot1-fondations-saison` (stacked — PR #19 pas encore mergée). PR vers `main` après merge de #19, sinon vers la branche lot 1.
- Migration Alembic suivante : **revision `047`, down_revision `"046"`**. Ne PAS lancer `alembic upgrade` (vérif à sec : `alembic heads`).
- **WC2026 = tournoi terminé** : si un fichier `wc2026*` référence la saison club 2025-2026, elle y reste FIGÉE (artefact historique du pricing CDM). Ne pas la brancher sur `current_season`.
- Marchés du settlement : exactement `KNOWN_MARKETS` du registre = `("goal_with_sub", "assist_with_sub", "goal", "assist")`. Convention avec-sub (spec §2) : le ticket est gagnant si le joueur nommé OU un joueur de sa chaîne de remplacement (transitive) réalise l'action.
- Normalisation des noms : réutiliser `_normalize_name` de `app.ingestion.auto_settle` — ne pas réinventer.
- Le script de backfill écrit UNIQUEMENT dans `match_events`, est idempotent, échelonné (pause paramétrable, limite par run) ; ordre des ligues = celui de `TARGET_LEAGUE_INTERNAL_IDS` (hypothèse non validée par mesure de volume — rapport du spike).
- Jamais d'échec silencieux. Suite verte : `cd ~/ev0/backend && .venv/bin/python -m pytest tests/ -q` (543+ tests) ; `ruff check` (`.venv/bin/ruff`) propre sur tout fichier touché.

---

### Task 1: Raccord des hardcodes saison (deadline 1er août)

**Files:**
- Modify: `backend/app/api/players.py:459,594,680,822` (défauts de paramètres `season`)
- Modify: `backend/app/ingestion/bzzoiro/sync_fixtures_from_bzz.py:233` (stamp `CURRENT_SEASON`)
- Modify: `backend/app/ingestion/bzzoiro/constants.py:58` et `backend/app/worker.py:53` (constante `CURRENT_SEASON`)
- Test: `backend/tests/api/test_players_season_default.py`

**Interfaces:**
- Consumes: `async current_season(session) -> str` de `app.services.season_service` (lot 1).
- Produces: les 4 endpoints joueurs acceptent `season=None` (défaut) et résolvent la saison courante ; les nouvelles fixtures sont stampées avec la saison résolue au moment du sync.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/api/test_players_season_default.py` :

```python
"""Les endpoints joueurs ne hardcodent plus la saison — défaut None résolu via season_service."""

import inspect

from app.api import players


def _season_default(func) -> object:
    param = inspect.signature(func).parameters["season"]
    default = param.default
    # FastAPI Query(...) : la valeur est dans .default de l'objet Query
    return getattr(default, "default", default)


def test_aucun_endpoint_joueur_ne_hardcode_la_saison():
    """Tout paramètre `season` d'un endpoint du module doit avoir None pour défaut."""
    offenders = []
    for name, func in inspect.getmembers(players, inspect.iscoroutinefunction):
        sig = inspect.signature(func)
        if "season" in sig.parameters and _season_default(func) == "2025-2026":
            offenders.append(name)
    assert offenders == [], f"Endpoints avec saison hardcodée: {offenders}"


def test_sync_fixtures_ne_stampe_plus_la_constante():
    import app.ingestion.bzzoiro.sync_fixtures_from_bzz as sf
    src = inspect.getsource(sf)
    assert "season=CURRENT_SEASON" not in src
```

- [ ] **Step 2: Vérifier qu'ils échouent**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/api/test_players_season_default.py -v`
Expected: 2 FAIL.

- [ ] **Step 3: Modifier les 4 endpoints de `players.py`**

Pour chacune des lignes 459, 594, 680, 822, appliquer le même traitement. Exemple pour la ligne 594 :

```python
# avant
    season: str = Query("2025-2026"),
# après
    season: str | None = Query(None),
```

(ligne 459 : `season: str = "2025-2026"` → `season: str | None = None`.)

Puis, en tête du corps de chaque endpoint concerné (avant le premier usage de `season`), ajouter :

```python
    if season is None:
        season = await current_season(db)
```

où `db` est la session async déjà injectée dans l'endpoint (adapter le nom exact du paramètre de session de chaque endpoint — les 4 en ont une). Ajouter l'import en tête de fichier :

```python
from app.services.season_service import current_season
```

Vérifier ligne à ligne que chaque endpoint utilise bien `season` APRÈS la résolution, pas avant.

- [ ] **Step 4: Modifier `sync_fixtures_from_bzz.py`**

Dans la fonction qui contient la ligne 233 : résoudre la saison UNE fois en tête de fonction (pas par fixture) :

```python
    season = await current_season(session)
```

et remplacer `season=CURRENT_SEASON` par `season=season`. Retirer `CURRENT_SEASON` de l'import des constants de ce fichier.

- [ ] **Step 5: Purger la constante si morte**

Run: `grep -rn "CURRENT_SEASON" ~/ev0/backend/app/`

- `backend/app/worker.py:53` : constante locale morte — supprimer.
- Si le grep ne montre plus AUCUN usage vivant de `constants.CURRENT_SEASON` : supprimer la ligne 58 de `constants.py`. S'il reste des usages (ex. fichiers `wc2026*`) : les examiner un par un — un usage wc2026 reste FIGÉ (remplacer par le littéral `"2025-2026"` avec commentaire `# saison club du pricing CDM — figée, tournoi terminé`), tout autre usage vivant se branche sur `current_season`. Documenter chaque cas dans le rapport.

- [ ] **Step 6: Suite complète + lint**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/ -q`
Expected: tout PASS (adapter — sans affaiblir — tout test existant qui reposait sur les défauts "2025-2026").
Run: `.venv/bin/ruff check app/api/players.py app/ingestion/bzzoiro/sync_fixtures_from_bzz.py app/ingestion/bzzoiro/constants.py app/worker.py tests/api/test_players_season_default.py`
Expected: 0 erreur nouvelle (ne pas corriger les erreurs préexistantes hors branche).

- [ ] **Step 7: Commit**

```bash
cd ~/ev0 && git add backend/app/api/players.py backend/app/ingestion/bzzoiro/sync_fixtures_from_bzz.py backend/app/ingestion/bzzoiro/constants.py backend/app/worker.py backend/tests/api/test_players_season_default.py
git commit -m "feat(saison): raccord des derniers hardcodes 2025-2026 — endpoints joueurs + stamp fixtures via season_service"
```

---

### Task 2: Persistance des substitutions (migration 047 + parser)

**Files:**
- Modify: `backend/app/models/match_events.py` (colonne `related_player_name`, commentaire `event_type`)
- Modify: `backend/app/ingestion/bzzoiro/sync_incidents.py` (`_parse_incidents`, `_store_events`)
- Create: `backend/alembic/versions/047_match_events_substitutions.py`
- Test: `backend/tests/ingestion/test_parse_substitutions.py`

**Interfaces:**
- Produces: lignes `match_events` avec `event_type="substitution"`, `player_name=<entrant>`, `related_player_name=<sortant>`, `minute` ; colonne `related_player_name: str | None` sur le modèle `MatchEvent`. Consommées par la tâche 4 (settlement) et la tâche 3 (backfill).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/ingestion/test_parse_substitutions.py` :

```python
"""_parse_incidents émet désormais les substitutions (entrant + sortant)."""

from app.ingestion.bzzoiro.sync_incidents import _parse_incidents
from app.models.match_events import MatchEvent


def test_modele_a_la_colonne_related_player_name():
    assert hasattr(MatchEvent, "related_player_name")


def test_parse_substitution_nominale():
    raw = [{
        "incidentType": "substitution",
        "playerIn": {"name": "Gonçalo Ramos"},
        "playerOut": {"name": "Ousmane Dembélé"},
        "time": 63,
    }]
    rows = _parse_incidents(raw)
    assert rows == [{
        "player_name": "Gonçalo Ramos",
        "event_type": "substitution",
        "minute": 63,
        "related_player_name": "Ousmane Dembélé",
    }]


def test_parse_substitution_cles_alternatives():
    """Certains payloads utilisent player/relatedPlayer — parsing défensif."""
    raw = [{
        "incidentType": "substitution",
        "player": {"name": "Warren Zaïre-Emery"},
        "relatedPlayer": {"shortName": "Vitinha"},
        "minute": 75,
    }]
    rows = _parse_incidents(raw)
    assert rows[0]["player_name"] == "Warren Zaïre-Emery"
    assert rows[0]["related_player_name"] == "Vitinha"
    assert rows[0]["minute"] == 75


def test_substitution_incomplete_ignoree_sans_crash():
    raw = [{"incidentType": "substitution", "playerIn": {"name": "X"}, "time": 80}]
    assert _parse_incidents(raw) == []


def test_les_buts_portent_related_player_name_none():
    raw = [{"incidentType": "goal", "player": {"name": "Mbappé"}, "time": 12}]
    rows = _parse_incidents(raw)
    assert rows[0]["related_player_name"] is None
```

- [ ] **Step 2: Vérifier qu'ils échouent**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/ingestion/test_parse_substitutions.py -v`
Expected: FAIL (colonne absente, substitutions filtrées par le parser actuel).

- [ ] **Step 3: Modifier le modèle**

Dans `backend/app/models/match_events.py`, après la colonne `minute`, ajouter :

```python
    # Substitutions : player_name = entrant, related_player_name = sortant
    related_player_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
```

et mettre à jour le commentaire de `event_type` : `# goal, assist, own_goal, penalty_goal, substitution (+ sentinelles)`.

- [ ] **Step 4: Modifier le parser et le store**

Dans `_parse_incidents` (`sync_incidents.py`) : chaque `rows.append` existant (but, own goal, assist) reçoit la clé supplémentaire `"related_player_name": None`. Après le bloc de filtrage des buts, remplacer le `continue` des non-buts par la gestion des substitutions :

```python
        if inc_type in ("substitution", "sub", "substitutionIn"):
            p_in = inc.get("playerIn") or inc.get("player") or {}
            p_out = inc.get("playerOut") or inc.get("relatedPlayer") or {}
            in_name = p_in.get("name") or p_in.get("shortName") or ""
            out_name = p_out.get("name") or p_out.get("shortName") or ""
            minute = inc.get("time") if inc.get("time") is not None else inc.get("minute")
            if in_name and out_name:
                rows.append({
                    "player_name": in_name,
                    "event_type": "substitution",
                    "minute": minute,
                    "related_player_name": out_name,
                })
            else:
                logger.warning("Substitution incomplète ignorée (in=%r out=%r)", in_name, out_name)
            continue
        if inc_type not in ("goal", "addedGoal"):
            continue
```

(garder la logique buts/assists existante intacte ; seule la clé `related_player_name: None` s'y ajoute). Dans `_store_events`, propager `related_player_name=ev.get("related_player_name")` à la construction des `MatchEvent`. Vérifier que les requêtes sentinelles du fichier (lignes ~129-243) filtrent déjà par `event_type IN ('goal', ...)` — les lignes substitution ne doivent PAS y entrer (ne rien changer si le filtre est déjà explicite ; le confirmer dans le rapport).

- [ ] **Step 5: Migration 047**

Créer `backend/alembic/versions/047_match_events_substitutions.py` :

```python
"""match_events.related_player_name : sortant d'une substitution (settlement avec-sub).

Revision ID: 047
Revises: 046
Create Date: 2026-07-19
"""
import sqlalchemy as sa
from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "match_events",
        sa.Column("related_player_name", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_events", "related_player_name")
```

- [ ] **Step 6: Tests + alembic à sec + lint + commit**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/ -q` → tout PASS.
Run: `.venv/bin/alembic heads` → une seule head `047`.
Run: `.venv/bin/ruff check app/models/match_events.py app/ingestion/bzzoiro/sync_incidents.py alembic/versions/047_match_events_substitutions.py tests/ingestion/test_parse_substitutions.py` → 0 erreur.

```bash
cd ~/ev0 && git add backend/app/models/match_events.py backend/app/ingestion/bzzoiro/sync_incidents.py backend/alembic/versions/047_match_events_substitutions.py backend/tests/ingestion/test_parse_substitutions.py
git commit -m "feat(events): persiste les substitutions dans match_events (migration 047) — socle du settlement avec-sub"
```

---

### Task 3: Script de backfill échelonné des substitutions 2025-26

**Files:**
- Create: `backend/app/scripts/backfill_substitutions.py`

**Interfaces:**
- Consumes: `BzzoiroClient.get_page("/api/v2/events/{api_id}/incidents/")`, `_parse_incidents` et `_store_events` de `sync_incidents` (tâche 2), `TARGET_LEAGUE_API_IDS`.
- Produces: script CLI idempotent `python -m app.scripts.backfill_substitutions [--league KEY] [--limit N] [--sleep S] [--dry-run]`. Sera exécuté sur le VPS après déploiement (hors scope de ce plan).

Pas de TDD complet (script opérationnel) — la logique de parsing/stockage est déjà testée en tâche 2 ; le script est revu sur : idempotence, échelonnement, dry-run.

- [ ] **Step 1: Écrire le script**

Créer `backend/app/scripts/backfill_substitutions.py` :

```python
"""Backfill échelonné des substitutions 2025-26 dans match_events.

Pour chaque match terminé de la saison qui a des buts en base mais aucune ligne
substitution, re-fetch les incidents Bzzoiro et stocke les substitutions.
Idempotent : un match déjà pourvu de substitutions est ignoré.
Échelonné : --limit matchs par run (défaut 300), --sleep secondes entre appels
(défaut 2.0), --league pour cibler une ligue (ordre conseillé = celui de
TARGET_LEAGUE_API_IDS ; hypothèse de priorisation non validée — cf. rapport spike).

Usage : cd backend && python -m app.scripts.backfill_substitutions --league premier_league --limit 300
"""

import argparse
import asyncio
import logging

from sqlalchemy import and_, exists, select

from app.config import settings
from app.db import async_session
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_API_IDS
from app.ingestion.bzzoiro.sync_incidents import _parse_incidents, _store_events
from app.models.bzzoiro import BzzEvent
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent

logger = logging.getLogger(__name__)


def _fixtures_sans_subs_query(league_api_id: int | None, limit: int):
    """Fixtures terminées 2025-26 ayant des buts mais aucune substitution."""
    has_goal = exists().where(
        and_(MatchEvent.fixture_id == Fixture.id, MatchEvent.event_type == "goal")
    )
    has_sub = exists().where(
        and_(MatchEvent.fixture_id == Fixture.id, MatchEvent.event_type == "substitution")
    )
    query = (
        select(Fixture.id, BzzEvent.api_id)
        .join(BzzEvent, BzzEvent.fixture_id == Fixture.id)
        .where(Fixture.season == "2025-2026", has_goal, ~has_sub)
        .order_by(Fixture.kickoff_utc)
        .limit(limit)
    )
    if league_api_id is not None:
        query = query.where(BzzEvent.league_api_id == league_api_id)
    return query


async def run(league_key: str | None, limit: int, sleep_s: float, dry_run: bool) -> None:
    assert settings.bzzoiro_api_key, "BZZOIRO_API_KEY manquante"
    league_api_id = TARGET_LEAGUE_API_IDS[league_key] if league_key else None
    async with async_session() as session:
        rows = (await session.execute(_fixtures_sans_subs_query(league_api_id, limit))).all()
    logger.info("%d matchs sans substitutions à backfiller (league=%s)", len(rows), league_key)
    if dry_run:
        print(f"[dry-run] {len(rows)} matchs seraient traités")
        return

    done = errors = 0
    async with BzzoiroClient(settings.bzzoiro_api_key) as client:
        for fixture_id, bzz_api_id in rows:
            try:
                payload = await client.get_page(f"/api/v2/events/{bzz_api_id}/incidents/")
                incidents = payload.get("incidents") or payload.get("results") or []
                subs = [r for r in _parse_incidents(incidents) if r["event_type"] == "substitution"]
                if subs:
                    async with async_session() as session:
                        await _store_events(session, fixture_id, subs)
                        await session.commit()
                done += 1
            except Exception as exc:
                errors += 1
                logger.error("Fixture %d (bzz %d): %s", fixture_id, bzz_api_id, exc)
            await asyncio.sleep(sleep_s)
    print(f"Backfill terminé: {done} matchs traités, {errors} erreurs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", choices=sorted(TARGET_LEAGUE_API_IDS), default=None)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.league, args.limit, args.sleep, args.dry_run))
```

Avant de committer, VÉRIFIER contre le code réel (et adapter si besoin, en le notant au rapport) : (a) la signature exacte de `_store_events` (ordre/noms des paramètres, gestion du commit) ; (b) le lien `BzzEvent.fixture_id` — si l'association BzzEvent↔Fixture passe par un autre champ (ex. matching par équipes/date), reprendre le mécanisme de jointure utilisé par `sync_incidents` lui-même ; (c) la clé du payload incidents (`incidents` vs `results`) telle que `sync_incidents` la lit.

- [ ] **Step 2: Vérification statique + dry-run local**

Run: `cd ~/ev0/backend && .venv/bin/python -c "import app.scripts.backfill_substitutions"` → import OK.
Run (sans DB locale, l'échec de connexion est acceptable et à noter tel quel) : `.venv/bin/python -m app.scripts.backfill_substitutions --dry-run --limit 5` — si une DB locale existe, vérifier que le dry-run imprime un compte sans écrire.
Run: `.venv/bin/ruff check app/scripts/backfill_substitutions.py` → 0 erreur.
Run: `.venv/bin/python -m pytest tests/ -q` → tout PASS (rien cassé).

- [ ] **Step 3: Commit**

```bash
cd ~/ev0 && git add backend/app/scripts/backfill_substitutions.py
git commit -m "feat(events): script de backfill échelonné des substitutions 2025-26 (idempotent, dry-run)"
```

---

### Task 4: Module de settlement "avec sub"

**Files:**
- Create: `backend/app/evaluation/__init__.py` (vide)
- Create: `backend/app/evaluation/settlement.py`
- Test: `backend/tests/evaluation/test_settlement.py` (+ `backend/tests/evaluation/__init__.py` vide)

**Interfaces:**
- Consumes: `MatchEvent` (avec `related_player_name`, tâche 2) ; `_normalize_name` de `app.ingestion.auto_settle` ; `KNOWN_MARKETS` de `app.pricing.model_registry` (lot 1).
- Produces:
  - `@dataclass FixtureEvents` : `goals: list[str]`, `assists: list[str]`, `subs: list[tuple[str, str]]` (noms normalisés ; subs = liste `(entrant, sortant)` ordonnée par minute) ;
  - `async load_fixture_events(session, fixture_id: int) -> FixtureEvents` ;
  - `replacement_chain(subs: list[tuple[str, str]], player: str) -> set[str]` (transitive) ;
  - `settle(market: str, player_name: str, events: FixtureEvents) -> bool` (ValueError si marché hors `KNOWN_MARKETS`).
  - Consommé par le rejeu d'Alpha (lot 2b).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/evaluation/test_settlement.py` :

```python
"""Settlement avec-sub : chaîne de remplacement transitive + règlement des 4 marchés."""

import pytest

from app.evaluation.settlement import FixtureEvents, replacement_chain, settle


def _events(goals=(), assists=(), subs=()):
    return FixtureEvents(goals=list(goals), assists=list(assists), subs=list(subs))


class TestReplacementChain:
    def test_joueur_jamais_remplace(self):
        assert replacement_chain([("ramos", "dembele")], "mbappe") == set()

    def test_remplacement_simple(self):
        assert replacement_chain([("ramos", "dembele")], "dembele") == {"ramos"}

    def test_chaine_transitive(self):
        # dembele sort pour ramos, puis ramos sort pour barcola
        subs = [("ramos", "dembele"), ("barcola", "ramos")]
        assert replacement_chain(subs, "dembele") == {"ramos", "barcola"}

    def test_deux_subs_independantes_non_melangees(self):
        subs = [("ramos", "dembele"), ("zaire-emery", "vitinha")]
        assert replacement_chain(subs, "dembele") == {"ramos"}


class TestSettle:
    def test_goal_sec_le_joueur_marque(self):
        assert settle("goal", "mbappe", _events(goals=["mbappe"])) is True

    def test_goal_sec_le_remplacant_ne_compte_pas(self):
        ev = _events(goals=["ramos"], subs=[("ramos", "dembele")])
        assert settle("goal", "dembele", ev) is False

    def test_goal_with_sub_le_remplacant_compte(self):
        ev = _events(goals=["ramos"], subs=[("ramos", "dembele")])
        assert settle("goal_with_sub", "dembele", ev) is True

    def test_goal_with_sub_chaine_transitive(self):
        ev = _events(goals=["barcola"], subs=[("ramos", "dembele"), ("barcola", "ramos")])
        assert settle("goal_with_sub", "dembele", ev) is True

    def test_assist_with_sub(self):
        ev = _events(assists=["ramos"], subs=[("ramos", "dembele")])
        assert settle("assist_with_sub", "dembele", ev) is True
        assert settle("assist", "dembele", ev) is False

    def test_normalisation_des_noms(self):
        # settle normalise l'entrée — accents/majuscules indifférents
        assert settle("goal", "Mbappé", _events(goals=["mbappe"])) is True

    def test_marche_inconnu(self):
        with pytest.raises(ValueError, match="first_goal"):
            settle("first_goal", "mbappe", _events())
```

- [ ] **Step 2: Vérifier qu'ils échouent**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/evaluation/test_settlement.py -v`
Expected: FAIL — module inexistant.

- [ ] **Step 3: Implémenter**

Créer `backend/app/evaluation/__init__.py` (vide) et `backend/app/evaluation/settlement.py` :

```python
"""Settlement des tickets joueur en convention "avec sub" (spec 2026-07-18, §3.2).

Un ticket goal_with_sub/assist_with_sub est gagnant si le joueur nommé OU un
joueur de sa chaîne de remplacement (transitive : remplaçant du remplaçant)
réalise l'action. Tous les noms sont normalisés (accents/casse) en entrée.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.auto_settle import _normalize_name
from app.models.match_events import MatchEvent
from app.pricing.model_registry import KNOWN_MARKETS

_GOAL_TYPES = ("goal", "penalty_goal")


@dataclass
class FixtureEvents:
    """Événements d'un match, noms déjà normalisés."""

    goals: list[str]
    assists: list[str]
    subs: list[tuple[str, str]]  # (entrant, sortant), ordonnées par minute


async def load_fixture_events(session: AsyncSession, fixture_id: int) -> FixtureEvents:
    result = await session.execute(
        select(MatchEvent)
        .where(MatchEvent.fixture_id == fixture_id)
        .order_by(MatchEvent.minute)
    )
    goals: list[str] = []
    assists: list[str] = []
    subs: list[tuple[str, str]] = []
    for ev in result.scalars():
        name = _normalize_name(ev.player_name)
        if ev.event_type in _GOAL_TYPES:
            goals.append(name)
        elif ev.event_type == "assist":
            assists.append(name)
        elif ev.event_type == "substitution" and ev.related_player_name:
            subs.append((name, _normalize_name(ev.related_player_name)))
    return FixtureEvents(goals=goals, assists=assists, subs=subs)


def replacement_chain(subs: list[tuple[str, str]], player: str) -> set[str]:
    """Remplaçants transitifs d'un joueur (ordre des subs = ordre chronologique)."""
    chain: set[str] = set()
    targets = {player}
    for entrant, sortant in subs:
        if sortant in targets:
            chain.add(entrant)
            targets.add(entrant)
    return chain


def settle(market: str, player_name: str, events: FixtureEvents) -> bool:
    """Règle un ticket. ValueError si le marché n'est pas dans KNOWN_MARKETS."""
    if market not in KNOWN_MARKETS:
        raise ValueError(f"Marché inconnu: {market!r} (admis: {KNOWN_MARKETS})")
    player = _normalize_name(player_name)
    scorers = set(events.goals) if market.startswith("goal") else set(events.assists)
    if player in scorers:
        return True
    if market.endswith("_with_sub"):
        return bool(replacement_chain(events.subs, player) & scorers)
    return False
```

- [ ] **Step 4: Tests + lint + commit**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/evaluation/test_settlement.py -v` → 11 PASS.
Run: `.venv/bin/python -m pytest tests/ -q` → tout PASS.
Run: `.venv/bin/ruff check app/evaluation/ tests/evaluation/` → 0 erreur.

```bash
cd ~/ev0 && git add backend/app/evaluation/ backend/tests/evaluation/
git commit -m "feat(evaluation): settlement avec-sub — chaîne de remplacement transitive + règlement des 4 marchés"
```

---

### Task 5: Métriques d'évaluation (log-loss, Brier, calibration, delta apparié)

**Files:**
- Create: `backend/app/evaluation/metrics.py`
- Test: `backend/tests/evaluation/test_metrics.py`

**Interfaces:**
- Produces (pures, sans DB — consommées par le rejeu lot 2b et le dashboard #13) :
  - `log_loss(probs: list[float], outcomes: list[bool]) -> float` (clipping eps=1e-12) ;
  - `brier_score(probs, outcomes) -> float` ;
  - `calibration_bins(probs, outcomes, n_bins: int = 10) -> list[CalibrationBin]` avec `@dataclass CalibrationBin(low, high, count, avg_prob, hit_rate)` ;
  - `paired_delta_log_loss(probs_a, probs_b, outcomes) -> PairedDelta` avec `@dataclass PairedDelta(deltas: list[float], mean_delta: float, n: int)` (delta = perte_A − perte_B par ticket ; mean_delta > 0 ⟹ B meilleur).
  - Toutes lèvent ValueError sur listes vides ou longueurs incohérentes — jamais d'échec silencieux.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/evaluation/test_metrics.py` :

```python
"""Métriques d'évaluation — valeurs vérifiées à la main."""

import math

import pytest

from app.evaluation.metrics import (
    brier_score,
    calibration_bins,
    log_loss,
    paired_delta_log_loss,
)


class TestLogLoss:
    def test_prediction_parfaite(self):
        assert log_loss([1.0, 0.0], [True, False]) == pytest.approx(0.0, abs=1e-9)

    def test_valeur_calculee_a_la_main(self):
        # -(ln(0.8) + ln(1-0.4)) / 2
        expected = -(math.log(0.8) + math.log(0.6)) / 2
        assert log_loss([0.8, 0.4], [True, False]) == pytest.approx(expected)

    def test_proba_zero_sur_succes_clippee_pas_infinie(self):
        assert math.isfinite(log_loss([0.0], [True]))

    def test_listes_incoherentes(self):
        with pytest.raises(ValueError):
            log_loss([0.5], [True, False])
        with pytest.raises(ValueError):
            log_loss([], [])


class TestBrier:
    def test_valeur_a_la_main(self):
        # ((0.8-1)^2 + (0.4-0)^2) / 2 = (0.04 + 0.16)/2 = 0.10
        assert brier_score([0.8, 0.4], [True, False]) == pytest.approx(0.10)


class TestCalibrationBins:
    def test_regroupement_et_frequences(self):
        probs = [0.05, 0.15, 0.15, 0.95]
        outcomes = [False, True, False, True]
        bins = calibration_bins(probs, outcomes, n_bins=10)
        b0 = next(b for b in bins if b.count and b.low == pytest.approx(0.0))
        assert b0.count == 1 and b0.hit_rate == 0.0
        b1 = next(b for b in bins if b.count == 2)
        assert b1.low == pytest.approx(0.1)
        assert b1.avg_prob == pytest.approx(0.15)
        assert b1.hit_rate == pytest.approx(0.5)

    def test_proba_1_tombe_dans_le_dernier_bin(self):
        bins = calibration_bins([1.0], [True], n_bins=10)
        assert bins[-1].count == 1


class TestPairedDelta:
    def test_b_meilleur_delta_positif(self):
        # A dit 0.5, B dit 0.9, issue True → perte A > perte B → delta > 0
        res = paired_delta_log_loss([0.5], [0.9], [True])
        assert res.n == 1
        assert res.mean_delta > 0
        assert res.deltas[0] == pytest.approx(-math.log(0.5) + math.log(0.9))

    def test_modeles_identiques_delta_nul(self):
        res = paired_delta_log_loss([0.3, 0.7], [0.3, 0.7], [False, True])
        assert res.mean_delta == pytest.approx(0.0, abs=1e-12)
```

- [ ] **Step 2: Vérifier qu'ils échouent**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/evaluation/test_metrics.py -v`
Expected: FAIL — module inexistant.

- [ ] **Step 3: Implémenter**

Créer `backend/app/evaluation/metrics.py` :

```python
"""Métriques d'évaluation des modèles de pricing (spec 2026-07-18, §3.2).

Fonctions pures — pas d'accès DB. Le delta apparié est la métrique reine du
duel Alpha/Beta : le bruit commun aux deux modèles s'annule ticket par ticket.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-12


def _check(probs: list[float], outcomes: list[bool]) -> None:
    if not probs or len(probs) != len(outcomes):
        raise ValueError(
            f"Listes vides ou incohérentes: {len(probs)} probs vs {len(outcomes)} outcomes"
        )


def _ticket_loss(p: float, won: bool) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return -math.log(p) if won else -math.log(1.0 - p)


def log_loss(probs: list[float], outcomes: list[bool]) -> float:
    _check(probs, outcomes)
    return sum(_ticket_loss(p, o) for p, o in zip(probs, outcomes)) / len(probs)


def brier_score(probs: list[float], outcomes: list[bool]) -> float:
    _check(probs, outcomes)
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


@dataclass
class CalibrationBin:
    low: float
    high: float
    count: int
    avg_prob: float
    hit_rate: float


def calibration_bins(
    probs: list[float], outcomes: list[bool], n_bins: int = 10
) -> list[CalibrationBin]:
    _check(probs, outcomes)
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)  # p=1.0 → dernier bin
        buckets[idx].append((p, o))
    bins: list[CalibrationBin] = []
    for i, bucket in enumerate(buckets):
        count = len(bucket)
        bins.append(
            CalibrationBin(
                low=i / n_bins,
                high=(i + 1) / n_bins,
                count=count,
                avg_prob=sum(p for p, _ in bucket) / count if count else 0.0,
                hit_rate=sum(1 for _, o in bucket if o) / count if count else 0.0,
            )
        )
    return bins


@dataclass
class PairedDelta:
    deltas: list[float]  # perte_A − perte_B par ticket ; > 0 ⟹ B meilleur
    mean_delta: float
    n: int


def paired_delta_log_loss(
    probs_a: list[float], probs_b: list[float], outcomes: list[bool]
) -> PairedDelta:
    _check(probs_a, outcomes)
    _check(probs_b, outcomes)
    deltas = [
        _ticket_loss(pa, o) - _ticket_loss(pb, o)
        for pa, pb, o in zip(probs_a, probs_b, outcomes)
    ]
    return PairedDelta(deltas=deltas, mean_delta=sum(deltas) / len(deltas), n=len(deltas))
```

- [ ] **Step 4: Tests + lint + commit**

Run: `cd ~/ev0/backend && .venv/bin/python -m pytest tests/evaluation/ -v` → tout PASS (settlement + métriques).
Run: `.venv/bin/python -m pytest tests/ -q` → tout PASS.
Run: `.venv/bin/ruff check app/evaluation/metrics.py tests/evaluation/test_metrics.py` → 0 erreur.

```bash
cd ~/ev0 && git add backend/app/evaluation/metrics.py backend/tests/evaluation/test_metrics.py
git commit -m "feat(evaluation): métriques log-loss/Brier/calibration + delta apparié par ticket"
```

---

## Fin de lot

- [ ] Suite complète verte + `alembic heads` = 047 unique.
- [ ] PR `feat/lot2a-raccord-settlement` (base : `main` si #19 mergée, sinon `feat/lot1-fondations-saison`) mentionnant : la deadline 1er août couverte (tâche 1), la migration 047, et le fait que le backfill des substitutions doit être lancé sur le VPS après déploiement (`--league premier_league` d'abord, puis les autres).
- [ ] Le lot 2b (rejeu as-of d'Alpha + baseline chiffrée) fait l'objet de son propre plan — il consommera `settlement`, `metrics` et `model_pricing_snapshots`.
