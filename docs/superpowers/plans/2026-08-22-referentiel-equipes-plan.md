# Référentiel des équipes — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruire le référentiel des équipes sur l'identifiant Bzzoiro qui fonctionne partout, avec le championnat comme donnée explicite et une segmentation stricte à 20/20/20/18/18.

**Architecture :** `canonical_teams` gagne deux colonnes — `league_api_id` et `season` — qui décrivent l'engagement du club pour la saison courante. Un script de reconstruction énumère les engagés via `/api/events/`, contrôle les effectifs réglementaires avant toute écriture, puis remplace les engagements en bloc. Les effectifs sont rechargés club par club via `/api/players/?team=<id>`, ce qui répare aussi `current_team_api_id` et `current_team_name`. Les filtres de championnat cessent de déduire l'appartenance depuis les noms de club et résolvent par identifiant.

**Tech Stack :** Python 3.13, FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic, pytest (`asyncio_mode = "auto"`).

## Global Constraints

- **Ne jamais écrire `@pytest.mark.asyncio`** — `asyncio_mode = "auto"` est actif.
- **L'identifiant de référence est celui rendu par `/api/events/` (`home_team_obj.id`)**, qui est aussi celui de `/api/players/?team=` et de `/api/player-stats/?event=`. Vérifié le 22/08/2026 : 63 = AC Milan, 77 = Inter, 62 = Napoli, 203 = Coventry City.
- **Ne jamais résoudre une équipe ni un championnat par un nom de club stocké.** `bzz_players.current_team_name` est faux pour 886 joueurs sur 2 401 (37 %) et `bzz_teams.name` relève d'un troisième espace d'identifiants.
- **Effectifs réglementaires** : Premier League 20, La Liga 20, Serie A 20, Bundesliga 18, Ligue 1 18 — 96 clubs. Identifiants internes de compétition : 1, 3, 4, 5, 6.
- **Un écart d'effectif interrompt la reconstruction sans rien écrire.** Une segmentation approximative est pire qu'une absence de segmentation.
- **La Ligue des champions (7) est hors périmètre** tant que les tirages de la phase de ligue n'ont pas eu lieu.
- **`season` au format `"NNNN-NNNN"`**, résolu par `app.services.season_service.current_season`.
- Toute liste de compétitions vient de `app/ingestion/bzzoiro/constants.py`, jamais d'un littéral dispersé.

---

## File Structure

| Fichier | Rôle |
|---|---|
| `backend/alembic/versions/054_canonical_teams_league.py` | Créé — colonnes `league_api_id`, `season` |
| `backend/app/models/canonical_teams.py` | Modifié — les deux colonnes |
| `backend/app/ingestion/bzzoiro/constants.py` | Modifié — effectifs réglementaires |
| `backend/app/scripts/rebuild_team_registry.py` | Créé — reconstruction du référentiel |
| `backend/app/ingestion/bzzoiro/sync_players.py` | Modifié — parcours par club |
| `backend/app/api/players.py` | Modifié — filtres par identifiant |
| `backend/tests/scripts/test_rebuild_team_registry.py` | Créé |
| `backend/tests/ingestion/bzzoiro/test_sync_players_par_club.py` | Créé |
| `backend/tests/api/test_players_league_filter.py` | Créé |

---

### Task 1 : Colonnes de championnat sur `canonical_teams`

**Files:**
- Create: `backend/alembic/versions/054_canonical_teams_league.py`
- Modify: `backend/app/models/canonical_teams.py`
- Test: `backend/tests/test_bzzoiro_models.py` (ajout)

**Interfaces:**
- Produit : `CanonicalTeam.league_api_id: int | None`, `CanonicalTeam.season: str | None`.

Les deux colonnes sont **nullables** : un club relégué garde sa ligne et son historique, mais perd son engagement — `league_api_id = NULL` exprime exactement cela.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `backend/tests/test_bzzoiro_models.py` :

```python
def test_canonical_team_porte_son_championnat():
    """Le championnat est une donnee, plus une deduction depuis les noms."""
    from app.models.canonical_teams import CanonicalTeam

    cols = CanonicalTeam.__table__.columns
    assert "league_api_id" in cols
    assert "season" in cols
    # Nullables : un relegue garde sa ligne sans engagement.
    assert cols["league_api_id"].nullable is True
    assert cols["season"].nullable is True
```

- [ ] **Step 2 : Lancer le test et vérifier qu'il échoue**

Run: `cd backend && uv run pytest tests/test_bzzoiro_models.py -k canonical_team_porte -v`
Expected: FAIL — `AssertionError` sur `"league_api_id" in cols`

- [ ] **Step 3 : Ajouter les colonnes au modèle**

Dans `backend/app/models/canonical_teams.py`, après `aliases` :

```python
    # Engagement du club pour la saison courante. Nullables : un club relegue
    # garde sa ligne et son historique, il perd seulement son engagement.
    # Le championnat devient une donnee — il etait auparavant deduit en
    # regroupant les joueurs par nom de club, colonne fausse pour 37 % d'entre eux.
    league_api_id = Column(Integer, nullable=True)
    season = Column(String(10), nullable=True)
```

- [ ] **Step 4 : Écrire la migration**

Créer `backend/alembic/versions/054_canonical_teams_league.py` :

```python
"""Ajoute canonical_teams.league_api_id et season (engagement de la saison).

Revision ID: 054
Revises: 053
Create Date: 2026-08-22
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "054"
down_revision: str | None = "053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullables : un club relegue garde sa ligne sans engagement courant.
    op.add_column(
        "canonical_teams",
        sa.Column("league_api_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "canonical_teams",
        sa.Column("season", sa.String(length=10), nullable=True),
    )
    # Un club n'a qu'un engagement par saison — c'est l'invariant de segmentation.
    op.create_index(
        "ix_canonical_teams_league_season",
        "canonical_teams",
        ["league_api_id", "season"],
    )


def downgrade() -> None:
    op.drop_index("ix_canonical_teams_league_season", table_name="canonical_teams")
    op.drop_column("canonical_teams", "season")
    op.drop_column("canonical_teams", "league_api_id")
```

- [ ] **Step 5 : Lancer le test et vérifier qu'il passe**

Run: `cd backend && uv run pytest tests/test_bzzoiro_models.py -k canonical_team_porte -v`
Expected: PASS

- [ ] **Step 6 : Vérifier que la migration s'applique**

Run: `cd backend && uv run alembic upgrade head && uv run alembic current`
Expected: révision `054`

- [ ] **Step 7 : Commit**

```bash
git add backend/alembic/versions/054_canonical_teams_league.py backend/app/models/canonical_teams.py backend/tests/test_bzzoiro_models.py
git commit -m "feat(equipes): le championnat devient une colonne de canonical_teams"
```

---

### Task 2 : Reconstruction du référentiel

**Files:**
- Modify: `backend/app/ingestion/bzzoiro/constants.py`
- Create: `backend/app/scripts/rebuild_team_registry.py`
- Test: `backend/tests/scripts/test_rebuild_team_registry.py`

**Interfaces:**
- Consomme : `BzzoiroClient.get_all`, `CanonicalTeam`, `current_season`.
- Produit :
  - `EFFECTIFS_REGLEMENTAIRES: dict[int, int]`
  - `async enumerer_engages(client, league_api_id, season) -> dict[int, str]`
  - `class SegmentationError(RuntimeError)`
  - `async rebuild(session, client, season=None) -> dict[int, int]`

**Forme de la réponse `/api/events/`**, relevée le 22/08/2026 :

```json
{
  "id": 306044,
  "status": "finished",
  "home_team_obj": {"id": 102, "name": "Le Havre"},
  "away_team_obj": {"id": 114, "name": "Paris Saint-Germain"},
  "league": {"id": 6, "name": "Ligue 1"}
}
```

Comptes relevés sur la fenêtre 2026-07-01 → 2027-06-30 : 20, 20, 20, 18, 18 — soit exactement les effectifs réglementaires, aucun club engagé dans deux compétitions.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/scripts/test_rebuild_team_registry.py` :

```python
"""La reconstruction se plie strictement aux effectifs reglementaires."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.bzzoiro.constants import EFFECTIFS_REGLEMENTAIRES
from app.scripts.rebuild_team_registry import (
    SegmentationError,
    enumerer_engages,
    rebuild,
)


def _event(home_id, home_nom, away_id, away_nom):
    return {
        "id": 1000 + home_id,
        "home_team_obj": {"id": home_id, "name": home_nom},
        "away_team_obj": {"id": away_id, "name": away_nom},
    }


def _events_pour(n_clubs: int):
    """Fabrique des matchs couvrant exactement n_clubs clubs distincts."""
    clubs = [(i, f"Club {i}") for i in range(1, n_clubs + 1)]
    evs = []
    for i in range(0, n_clubs, 2):
        (hid, hn), (aid, an) = clubs[i], clubs[i + 1]
        evs.append(_event(hid, hn, aid, an))
    return evs


def test_effectifs_reglementaires():
    assert EFFECTIFS_REGLEMENTAIRES == {1: 20, 3: 20, 4: 20, 5: 18, 6: 18}
    assert sum(EFFECTIFS_REGLEMENTAIRES.values()) == 96


async def test_enumeration_rend_les_clubs_distincts():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=_events_pour(18))

    engages = await enumerer_engages(client, 6, "2026-2027")

    assert len(engages) == 18
    assert engages[1] == "Club 1"
    params = client.get_all.call_args.args[1]
    assert params["league"] == 6
    assert params["date_from"] == "2026-07-01"
    assert params["date_to"] == "2027-06-30"
    assert "season" not in params


async def test_ecart_d_effectif_interrompt_sans_rien_ecrire():
    """19 clubs en Premier League : on s'arrete, on ne commet rien."""
    client = MagicMock()
    client.get_all = AsyncMock(return_value=_events_pour(18))  # 18 != 20 attendus
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with pytest.raises(SegmentationError) as exc:
        await rebuild(session, client, season="2026-2027")

    assert "1" in str(exc.value) or "Premier League" in str(exc.value)
    assert "18" in str(exc.value)
    session.commit.assert_not_called()


async def test_un_club_engage_dans_deux_championnats_est_refuse():
    """L'invariant : un club appartient a exactement un championnat."""
    client = MagicMock()

    async def _get_all(path, params=None):
        n = EFFECTIFS_REGLEMENTAIRES[params["league"]]
        evs = _events_pour(n)
        # le club 1 apparait dans toutes les competitions
        evs[0]["home_team_obj"] = {"id": 1, "name": "Club 1"}
        return evs

    client.get_all = AsyncMock(side_effect=_get_all)
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with pytest.raises(SegmentationError) as exc:
        await rebuild(session, client, season="2026-2027")

    assert "deux" in str(exc.value).lower() or "1" in str(exc.value)
    session.commit.assert_not_called()
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/scripts/test_rebuild_team_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'EFFECTIFS_REGLEMENTAIRES'`

- [ ] **Step 3 : Déclarer les effectifs réglementaires**

Ajouter à la fin de `backend/app/ingestion/bzzoiro/constants.py` :

```python
# Effectifs reglementaires par championnat — invariant de segmentation.
# Releve le 22/08/2026 sur /api/events/ : 20, 20, 20, 18, 18, aucun club
# engage dans deux competitions. Un ecart interrompt la reconstruction.
# La Ligue des champions (7) en est absente tant que les tirages de la phase
# de ligue n'ont pas eu lieu.
EFFECTIFS_REGLEMENTAIRES: dict[int, int] = {
    1: 20,  # Premier League
    3: 20,  # La Liga
    4: 20,  # Serie A
    5: 18,  # Bundesliga
    6: 18,  # Ligue 1
}
```

- [ ] **Step 4 : Écrire la reconstruction**

Créer `backend/app/scripts/rebuild_team_registry.py` :

```python
"""Reconstruit le referentiel des equipes sur l'identifiant qui fait foi.

Bzzoiro expose ses equipes sous plusieurs espaces d'identifiants. Un seul
fonctionne partout — celui de /api/events/ (home_team_obj.id), qui sert aussi
a /api/players/?team= et a /api/player-stats/?event=. Verifie le 22/08/2026 :
63 = AC Milan, 77 = Inter, 62 = Napoli, 203 = Coventry City.

La base stockait un autre espace, herite : d'ou 6 clubs reconnus sur 20 en
Serie A, l'absence des promus, et des filtres qui melangeaient les
championnats.

SEGMENTATION STRICTE : chaque championnat doit rendre son effectif
reglementaire exact. Un ecart interrompt la reconstruction sans rien
commettre — une segmentation approximative donne l'illusion d'etre juste.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import EFFECTIFS_REGLEMENTAIRES
from app.services.season_service import current_season, season_end, season_start

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class SegmentationError(RuntimeError):
    """L'effectif d'un championnat ne correspond pas a son format."""


async def enumerer_engages(
    client: Any, league_api_id: int, season: str
) -> dict[int, str]:
    """Rend {identifiant: nom} des clubs engages dans ce championnat.

    L'enumeration passe par une fenetre de dates : le parametre season= de
    l'API est inoperant (il rend 408 110 evenements remontant a 1930).
    """
    events = await client.get_all(
        "/api/events/",
        {
            "league": league_api_id,
            "date_from": season_start(season).isoformat(),
            "date_to": season_end(season).isoformat(),
        },
    )
    engages: dict[int, str] = {}
    for e in events:
        for cle in ("home_team_obj", "away_team_obj"):
            obj = e.get(cle) or {}
            if obj.get("id"):
                engages[obj["id"]] = obj.get("name") or ""
    return engages


async def rebuild(
    session: AsyncSession, client: Any, season: str | None = None
) -> dict[int, int]:
    """Reconstruit le referentiel. Rend {championnat: nombre d'engages}.

    Leve SegmentationError avant toute ecriture si un effectif est hors format
    ou si un club est engage dans deux championnats.
    """
    from app.models.canonical_teams import CanonicalTeam

    if season is None:
        season = await current_season(session)

    # --- Phase 1 : enumerer et CONTROLER, sans rien ecrire ---
    par_championnat: dict[int, dict[int, str]] = {}
    for league_api_id, attendu in EFFECTIFS_REGLEMENTAIRES.items():
        engages = await enumerer_engages(client, league_api_id, season)
        if len(engages) != attendu:
            raise SegmentationError(
                f"championnat {league_api_id} : {len(engages)} clubs engages, "
                f"{attendu} attendus — reconstruction interrompue, rien n'a ete ecrit"
            )
        par_championnat[league_api_id] = engages
        logger.info("championnat %s : %d engages", league_api_id, len(engages))

    vus: dict[int, int] = {}
    for league_api_id, engages in par_championnat.items():
        for club_id in engages:
            if club_id in vus:
                raise SegmentationError(
                    f"club {club_id} engage dans deux championnats "
                    f"({vus[club_id]} et {league_api_id}) — reconstruction interrompue"
                )
            vus[club_id] = league_api_id

    # --- Phase 2 : ecrire ---
    # Remplacement en bloc : un club absent de la nouvelle liste perd son
    # engagement du seul fait de son absence. Sans cela un relegue resterait
    # engage indefiniment et polluerait le filtre de son ancien championnat.
    await session.execute(
        update(CanonicalTeam)
        .where(CanonicalTeam.season == season)
        .values(league_api_id=None, season=None)
    )

    comptes: dict[int, int] = {}
    for league_api_id, engages in par_championnat.items():
        for club_id, nom in engages.items():
            existant = (await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.bzz_team_id == club_id)
            )).scalar_one_or_none()

            if existant is None:
                existant = (await session.execute(
                    select(CanonicalTeam).where(CanonicalTeam.name_fr == nom)
                )).scalar_one_or_none()

            if existant is None:
                session.add(CanonicalTeam(
                    name_fr=nom, name_en=nom, bzz_team_id=club_id,
                    league_api_id=league_api_id, season=season,
                ))
            else:
                existant.bzz_team_id = club_id
                existant.league_api_id = league_api_id
                existant.season = season
        comptes[league_api_id] = len(engages)

    await session.commit()
    logger.info("Referentiel reconstruit : %s", comptes)
    return comptes


async def _main() -> None:
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.client import BzzoiroClient

    async with async_session() as session, BzzoiroClient(
        settings.bzzoiro_api_key
    ) as client:
        await rebuild(session, client)


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 5 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/scripts/test_rebuild_team_registry.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6 : Commit**

```bash
git add backend/app/ingestion/bzzoiro/constants.py backend/app/scripts/rebuild_team_registry.py backend/tests/scripts/test_rebuild_team_registry.py
git commit -m "feat(equipes): reconstruction du referentiel avec segmentation stricte"
```

---

### Task 3 : Effectifs rechargés club par club

**Files:**
- Modify: `backend/app/ingestion/bzzoiro/sync_players.py`
- Test: `backend/tests/ingestion/bzzoiro/test_sync_players_par_club.py` (créer)

**Interfaces:**
- Consomme : `CanonicalTeam.bzz_team_id` renseigné par la tâche 2.
- Produit : `async sync_players_for_team(session, client, team_api_id) -> int`, `async sync_players(session, client) -> int` (signature publique inchangée).

**Contexte — deux défauts cumulés :**

`sync_players` appelle `client.get_all("/api/players/")` **sans filtre**. La base
compte 117 439 joueurs, paginés par 50 : 2 349 pages. Or `get_all` plafonne à
`max_pages=500`, soit **25 000 joueurs**. Les 78 % restants n'ont jamais été
rafraîchis depuis leur première écriture — Bastoni y porte encore
`current_team_api_id = 2697` et `current_team_name = "Gimnástica Torrelavega"`,
alors que l'API rend aujourd'hui `current_team.id = 77`.

Le code lit par ailleurs `team.get("api_id") or team.get("id")`, préférant un
champ dont son propre commentaire dit qu'il relève d'« un autre espace
d'identifiants ». Le champ est aujourd'hui absent de la réponse, mais l'ordre
doit être inversé pour que le bon identifiant l'emporte s'il réapparaît.

Parcourir les 96 clubs du référentiel remplace 2 349 pages par **96 appels**,
et ne rend que des joueurs du périmètre.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/ingestion/bzzoiro/test_sync_players_par_club.py` :

```python
"""Les effectifs se chargent club par club, pas par la liste mondiale.

La liste mondiale compte 117 439 joueurs pagines par 50, soit 2 349 pages,
alors que get_all plafonne a 500 : 78 % des joueurs n'etaient jamais
rafraichis.
"""
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.bzzoiro.sync_players import sync_players_for_team


def _joueur(pid, nom, team_id, team_nom, api_id=None):
    ct = {"id": team_id, "name": team_nom}
    if api_id is not None:
        ct["api_id"] = api_id
    return {"id": pid, "name": nom, "current_team": ct}


def _session():
    s = MagicMock()
    s.execute = AsyncMock()
    s.commit = AsyncMock()
    return s


async def test_charge_l_effectif_d_un_club():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[
        _joueur(1, "Alessandro Bastoni", 77, "Inter"),
        _joueur(2, "Ange-Yoan Bonny", 77, "Inter"),
    ])

    n = await sync_players_for_team(_session(), client, 77)

    assert n == 2
    client.get_all.assert_called_once_with("/api/players/", {"team": 77})


async def test_identifiant_de_l_espace_evenements_prioritaire():
    """current_team.id fait foi ; api_id releve d'un autre espace."""
    ecrits = []
    session = _session()

    async def _capture(stmt):
        ecrits.append(stmt.compile().params)

    session.execute = AsyncMock(side_effect=_capture)

    client = MagicMock()
    client.get_all = AsyncMock(return_value=[
        _joueur(1, "Alessandro Bastoni", 77, "Inter", api_id=2697),
    ])

    await sync_players_for_team(session, client, 77)

    assert ecrits[0]["current_team_api_id"] == 77
    assert ecrits[0]["current_team_name"] == "Inter"


async def test_club_sans_joueur_n_ecrit_rien():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])
    session = _session()

    n = await sync_players_for_team(session, client, 999)

    assert n == 0
    session.commit.assert_not_called()
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/ingestion/bzzoiro/test_sync_players_par_club.py -v`
Expected: FAIL — `ImportError: cannot import name 'sync_players_for_team'`

- [ ] **Step 3 : Extraire l'écriture d'un joueur**

Dans `backend/app/ingestion/bzzoiro/sync_players.py`, extraire le corps de la
boucle existante (lignes 30 à 60 environ) dans une fonction, en **inversant
l'ordre de lecture de l'identifiant d'équipe** :

```python
def build_player_values(row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Construit la ligne bzz_players. Rend None si le joueur n'a pas d'identifiant."""
    api_id = row.get("id") or row.get("api_id")
    if not api_id:
        return None

    team = row.get("current_team") or {}
    nat_team = row.get("national_team") or {}

    return {
        "api_id": api_id,
        "internal_id": api_id,
        "name": row.get("name", ""),
        "short_name": row.get("short_name"),
        "nationality": row.get("nationality"),
        "date_of_birth": _parse_date(row.get("date_of_birth")),
        "height": row.get("height"),
        "jersey_number": row.get("jersey_number"),
        "position": row.get("position"),
        "market_value": row.get("market_value"),
        # current_team.id fait foi : c'est l'espace de /api/events/ et de
        # /api/player-stats/?event=. api_id releve d'un autre espace et a
        # produit des rattachements faux (Bastoni -> "Gimnastica Torrelavega").
        "current_team_api_id": team.get("id") or team.get("api_id"),
        "current_team_name": team.get("name"),
        "national_team_api_id": nat_team.get("id") or nat_team.get("api_id"),
        "synced_at": now,
    }
```

- [ ] **Step 4 : Écrire le chargement par club**

Ajouter dans le même module :

```python
async def sync_players_for_team(
    session: AsyncSession, client: BzzoiroClient, team_api_id: int
) -> int:
    """Charge l'effectif d'un club. Rend le nombre de joueurs ecrits."""
    rows = await client.get_all("/api/players/", {"team": team_api_id})
    if not rows:
        return 0

    now = datetime.now(UTC)
    count = 0
    for row in rows:
        values = build_player_values(row, now)
        if values is None:
            continue
        ins = pg_insert(BzzPlayer).values(**values)
        update_set = {
            k: ins.excluded[k] for k in values if k not in ("api_id", "internal_id")
        }
        # internal_id : ne remplir que s'il est NULL, jamais ecraser.
        update_set["internal_id"] = func.coalesce(
            BzzPlayer.internal_id, ins.excluded["internal_id"]
        )
        await session.execute(
            ins.on_conflict_do_update(index_elements=["api_id"], set_=update_set)
        )
        count += 1

    await session.commit()
    return count
```

Reprendre pour `update_set["internal_id"]` la logique exacte déjà présente dans
`sync_players` (commentaire « Only fill NULL internal_id — never overwrite »),
sans la modifier.

- [ ] **Step 5 : Faire parcourir le référentiel à `sync_players`**

Remplacer le corps de `sync_players` :

```python
async def sync_players(session: AsyncSession, client: BzzoiroClient) -> int:
    """Charge les effectifs des clubs engages, un appel par club.

    La liste mondiale compte 117 439 joueurs pagines par 50, soit 2 349 pages,
    alors que get_all plafonne a 500 : 78 % des joueurs n'etaient jamais
    rafraichis. Le referentiel en compte 96 — 96 appels suffisent, et ne
    ramenent que des joueurs du perimetre.
    """
    from app.models.canonical_teams import CanonicalTeam

    clubs = (await session.execute(
        select(CanonicalTeam.bzz_team_id)
        .where(CanonicalTeam.league_api_id.is_not(None))
        .where(CanonicalTeam.bzz_team_id.is_not(None))
    )).scalars().all()

    if not clubs:
        logger.warning(
            "Aucun club engage dans canonical_teams — "
            "lancer app.scripts.rebuild_team_registry d'abord"
        )
        return 0

    total = 0
    for club_id in clubs:
        try:
            total += await sync_players_for_team(session, client, club_id)
        except Exception as exc:
            logger.warning("Echec effectif club %s : %s", club_id, exc)

    logger.info("Effectifs : %d joueurs sur %d clubs", total, len(clubs))
    return total
```

Vérifier que `select`, `func`, `AsyncSession`, `pg_insert` et `logger` sont
importés dans le module ; les ajouter aux imports existants si nécessaire.

- [ ] **Step 6 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/ingestion/bzzoiro/ -v`
Expected: PASS

- [ ] **Step 7 : Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_players.py backend/tests/ingestion/bzzoiro/test_sync_players_par_club.py
git commit -m "fix(effectifs): charger club par club au lieu de la liste mondiale plafonnee"
```

---

### Task 4 : Les filtres résolvent par identifiant

**Files:**
- Modify: `backend/app/api/players.py` (lignes 298, 510-570, 602, 661, 757)
- Test: `backend/tests/api/test_players_league_filter.py` (créer)

**Interfaces:**
- Consomme : `CanonicalTeam.league_api_id`, `CanonicalTeam.bzz_team_id`.
- Produit : `async team_ids_for_league(session, league_api_id, season) -> list[int]`.
- Supprime : `_get_team_dominant_leagues`, `_MIN_PLAYERS_FOR_TARGET_LEAGUE`.

**Contexte :** `_get_team_dominant_leagues` (`app/api/players.py:510`) groupe les
joueurs **par `current_team_name`** puis retient la compétition majoritaire.
Cette colonne est fausse pour 886 joueurs sur 2 401. D'où le filtre Serie A qui
remonte « Alcione Milano », le filtre Ligue des champions qui remonte « Inter
Club d'Escaldes » (Andorre), et le vrai Milan absent d'Italie.

La fonction existait parce que le championnat n'était stocké nulle part. La
tâche 1 le stocke : elle n'a plus de raison d'être.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/api/test_players_league_filter.py` :

```python
"""Le filtre de championnat resout par identifiant, jamais par nom de club.

_get_team_dominant_leagues groupait les joueurs par current_team_name — faux
pour 886 joueurs sur 2 401 — d'ou des clubs andorrans en Ligue des champions
et le vrai Milan absent d'Italie.
"""
from unittest.mock import AsyncMock, MagicMock

from app.api.players import team_ids_for_league


def _session_rendant(ids):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(ids)
    session.execute = AsyncMock(return_value=result)
    return session


async def test_rend_les_identifiants_du_championnat():
    session = _session_rendant([63, 77, 62])
    assert await team_ids_for_league(session, 4, "2026-2027") == [63, 77, 62]


async def test_championnat_sans_club_rend_une_liste_vide():
    assert await team_ids_for_league(_session_rendant([]), 99, "2026-2027") == []


async def test_la_requete_filtre_sur_le_championnat_et_la_saison():
    session = _session_rendant([63])
    await team_ids_for_league(session, 4, "2026-2027")

    requete = str(session.execute.call_args.args[0])
    assert "canonical_teams" in requete
    assert "league_api_id" in requete
    assert "season" in requete
    # aucune resolution par nom de club
    assert "current_team_name" not in requete


def test_la_deduction_par_nom_a_disparu():
    """La fonction n'avait de raison d'etre qu'en l'absence de colonne."""
    import app.api.players as mod

    assert not hasattr(mod, "_get_team_dominant_leagues")
    assert not hasattr(mod, "_MIN_PLAYERS_FOR_TARGET_LEAGUE")
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/api/test_players_league_filter.py -v`
Expected: FAIL — `ImportError: cannot import name 'team_ids_for_league'`

- [ ] **Step 3 : Écrire la résolution par identifiant**

Dans `backend/app/api/players.py`, remplacer intégralement
`_get_team_dominant_leagues` et la constante `_MIN_PLAYERS_FOR_TARGET_LEAGUE`
par :

```python
async def team_ids_for_league(
    session: AsyncSession, league_api_id: int, season: str
) -> list[int]:
    """Identifiants Bzzoiro des clubs engages dans ce championnat.

    Resolution par identifiant : le championnat est une colonne de
    canonical_teams depuis la migration 054. Il etait auparavant deduit en
    regroupant les joueurs par current_team_name, colonne fausse pour 37 %
    d'entre eux — d'ou des clubs andorrans dans les filtres.
    """
    from app.models.canonical_teams import CanonicalTeam

    result = await session.execute(
        select(CanonicalTeam.bzz_team_id).where(
            CanonicalTeam.league_api_id == league_api_id,
            CanonicalTeam.season == season,
            CanonicalTeam.bzz_team_id.is_not(None),
        )
    )
    return list(result.scalars().all())
```

- [ ] **Step 4 : Rebrancher la liste des équipes (ligne 602)**

Quand un championnat est demandé, la liste vient **directement du référentiel**.
C'est ce qui garantit mécaniquement les 18 ou 20 clubs, sans dépendre de ce que
les statistiques laissent deviner : un club sans aucun joueur en base doit tout
de même apparaître dans son championnat.

Remplacer :

```python
    dominant = await _get_team_dominant_leagues(session, season)

    if league_api_id is not None:
        valid_names: set[str] | None = {n for n, lg in dominant.items() if lg == league_api_id}
    else:
        valid_names = None
```

par une sortie anticipée :

```python
    if league_api_id is not None:
        from app.models.canonical_teams import CanonicalTeam

        clubs = (await session.execute(
            select(CanonicalTeam.bzz_team_id, CanonicalTeam.name_fr)
            .where(
                CanonicalTeam.league_api_id == league_api_id,
                CanonicalTeam.season == season,
                CanonicalTeam.bzz_team_id.is_not(None),
            )
            .order_by(CanonicalTeam.name_fr)
        )).all()
        return [{"api_id": tid, "name": nom} for tid, nom in clubs]
```

La requête `text(...)` existante et sa déduplication par nom restent en place :
elles ne servent plus que le cas `league_api_id is None` (toutes les équipes).
Supprimer alors le filtre devenu mort dans la boucle de sortie :

```python
    output = []
    for api_id, name in rows:
        output.append({"api_id": api_id, "name": name})
    return output
```

- [ ] **Step 5 : Rebrancher les deux autres appelants (lignes 661 et 757)**

Aux deux emplacements, remplacer :

```python
        dominant = await _get_team_dominant_leagues(session, season)
        team_names = [n for n, lg in dominant.items() if lg == league_api_id]
        ...
        eff_team = func.coalesce(BzzPlayer.loan_team_name, BzzPlayer.current_team_name)
        player_id_subq = player_id_subq.where(eff_team.in_(team_names))
```

par :

```python
        team_ids = await team_ids_for_league(session, league_api_id, season)
        if not team_ids:
            return []          # ligne 757 ; a la ligne 661, conserver le
                               # comportement existant du CSV vide
        eff_team = func.coalesce(
            BzzPlayer.loan_team_api_id, BzzPlayer.current_team_api_id
        )
        player_id_subq = player_id_subq.where(eff_team.in_(team_ids))
```

- [ ] **Step 6 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/api/ -v`
Expected: PASS

- [ ] **Step 7 : Vérifier qu'aucun appelant résiduel ne subsiste**

Run: `cd backend && grep -rn "_get_team_dominant_leagues\|_MIN_PLAYERS_FOR_TARGET_LEAGUE" app tests || echo "aucun appelant"`
Expected: `aucun appelant`

- [ ] **Step 8 : Suite complète**

Run: `cd backend && uv run pytest -q`
Expected: PASS

- [ ] **Step 9 : Commit**

```bash
git add backend/app/api/players.py backend/tests/api/test_players_league_filter.py
git commit -m "fix(filtres): resoudre le championnat par identifiant, plus par nom de club"
```

---

## Après la fusion

Deux commandes, dans cet ordre — la seconde dépend du référentiel écrit par la première :

```bash
docker exec ev0-compose-z5hvqt-worker-1 python -m app.scripts.rebuild_team_registry
docker exec ev0-compose-z5hvqt-worker-1 python -c "
import asyncio
from app.config import settings
from app.db import async_session
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.sync_players import sync_players
async def m():
    async with async_session() as s, BzzoiroClient(settings.bzzoiro_api_key) as c:
        print(await sync_players(s, c))
asyncio.run(m())"
```

La reconstruction s'interrompt d'elle-même si un championnat n'a pas son
effectif réglementaire — dans ce cas, rien n'est écrit et le message nomme le
championnat fautif.

**Ne pas fusionner d'autre branche pendant que ces commandes tournent** :
Dokploy recrée le conteneur au déploiement, ce qui tue le processus en cours.

Le déploiement se fait par Dokploy à la fusion sur `main`. Ne pas déployer à la
main en parallèle.
