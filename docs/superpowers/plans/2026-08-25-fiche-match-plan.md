# Fiche match — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplir les colonnes de match restées vides depuis toujours à partir de l'API Bzzoiro v2, offrir une fiche match calquée sur celle de la CDM, et faire enfin arriver des compos officielles jusqu'au pricing.

**Architecture :** Un module d'ingestion interroge les points d'accès v2 et remplit les colonnes JSONB existantes de `bzz_events`. Les compos sont écrites dans `team_lineups` avec le type que le résolveur attend déjà — `official` quand Bzzoiro les confirme, `bzzoiro` sinon — les deux coexistant, ce qui historise sans nouvelle table. La fiche match lit la base et réutilise les composants CDM.

**Tech Stack :** Python 3.13, FastAPI, SQLAlchemy 2 async, PostgreSQL, Alembic, Next.js 14, pytest (`asyncio_mode = "auto"`).

## Global Constraints

- **Ne jamais écrire `@pytest.mark.asyncio`** — `asyncio_mode = "auto"` est actif.
- **Points d'accès v2 vérifiés le 25/08/2026** sur Fulham–Chelsea (`209544`) :
  `/api/v2/events/{id}/`, `/api/v2/events/{id}/lineups/`, `/api/v2/events/{id}/stats/`,
  `/api/v2/events/{id}/incidents/`, `/api/v2/events/{id}/player-stats/`.
  Aucun n'est paginé : utiliser `client.get_page`, jamais `get_all`.
- **`bzz_events` porte déjà** `shotmap`, `incidents`, `momentum`, `average_positions`,
  `lineups` en JSONB. **Aucune nouvelle colonne pour ces blocs.**
- **La hiérarchie des compos existe déjà** dans `app/ingestion/lineup_resolver.py` :
  `PRIORITY = {official: 0, bzzoiro: 1, probable_manual: 2, probable_statshub: 3, last_known: 4}`,
  avec repli sur la dernière compo officielle de l'équipe. **Ne pas la réécrire.**
- **`sync_bzzoiro_lineups` supprime la ligne existante avant d'insérer** (`session.delete`).
  La nouvelle ingestion ne doit jamais supprimer une compo d'un **autre** type :
  c'est la coexistence de `bzzoiro` et `official` qui historise.
- **Périmètre** : championnats `[1, 3, 4, 5, 6]` — `TARGET_LEAGUE_INTERNAL_ID_LIST`
  moins la Ligue des champions (7), exclue jusqu'aux tirages.
- **Un bloc vide ne marque pas le match comme traité.** Écrire du vide sans le
  signaler est ce qui a laissé 8 965 matchs sans compo pendant des mois.
- Les composants CDM (`ShotMap`, `MatchPitch`, `MatchDetailPanel`, `JerseyCard`)
  sont réutilisés tels quels.

---

## File Structure

| Fichier | Rôle |
|---|---|
| `frontend/src/components/Sidebar.tsx` | Modifié — « Matchs » → « Calendrier » |
| `frontend/src/app/dashboard/calendrier/page.tsx` | Déplacé depuis `matches/` |
| `frontend/src/app/dashboard/matches/page.tsx` | Remplacé — redirection puis nouvelle liste |
| `backend/alembic/versions/055_team_lineups_publication.py` | Créé — `lineup_status`, `published_at` |
| `backend/app/models/lineups.py` | Modifié — les deux colonnes |
| `backend/app/ingestion/bzzoiro/sync_match_detail.py` | Créé — ingestion v2 |
| `backend/app/worker.py` | Modifié — deux jobs |
| `backend/app/api/matches.py` | Créé — lecture de la fiche |
| `backend/app/main.py` | Modifié — enregistrement du routeur |
| `frontend/src/app/dashboard/matches/[id]/page.tsx` | Créé — fiche match |
| `backend/tests/ingestion/bzzoiro/test_sync_match_detail.py` | Créé |
| `backend/tests/api/test_matches_api.py` | Créé |

---

### Task 1 : « Matchs » devient « Calendrier »

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx:48`
- Move: `frontend/src/app/dashboard/matches/page.tsx` → `frontend/src/app/dashboard/calendrier/page.tsx`
- Create: `frontend/src/app/dashboard/matches/page.tsx` (redirection temporaire)

**Interfaces:**
- Produit : la route `/dashboard/calendrier`, l'entrée de menu « Calendrier ».

- [ ] **Step 1 : Déplacer la page**

```bash
cd frontend/src/app/dashboard
mkdir -p calendrier
git mv matches/page.tsx calendrier/page.tsx
```

- [ ] **Step 2 : Renommer l'entrée de menu**

Dans `frontend/src/components/Sidebar.tsx`, remplacer la ligne 48 :

```tsx
      { name: 'Matchs', href: '/dashboard/matches', icon: Calendar },
```

par :

```tsx
      { name: 'Calendrier', href: '/dashboard/calendrier', icon: Calendar },
```

- [ ] **Step 3 : Rediriger l'ancienne route**

Créer `frontend/src/app/dashboard/matches/page.tsx` :

```tsx
import { redirect } from 'next/navigation'

/** L'ancienne route « Matchs » sert désormais le calendrier.
 *  Redirection plutôt que suppression : les liens et favoris existants
 *  continuent de fonctionner. Cette page sera remplacée par la fiche match
 *  à la tâche 4. */
export default function MatchesRedirect() {
  redirect('/dashboard/calendrier')
}
```

- [ ] **Step 4 : Vérifier**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: compile sans erreur, les routes `/dashboard/calendrier` et `/dashboard/matches` apparaissent

- [ ] **Step 5 : Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/app/dashboard/calendrier frontend/src/app/dashboard/matches
git commit -m "refactor(nav): Matchs devient Calendrier"
```

---

### Task 2 : Traçabilité de publication sur les compos

**Files:**
- Create: `backend/alembic/versions/055_team_lineups_publication.py`
- Modify: `backend/app/models/lineups.py`
- Test: `backend/tests/test_lineup_models.py` (ajout)

**Interfaces:**
- Produit : `TeamLineup.lineup_status: str | None`, `TeamLineup.published_at: datetime | None`.

`lineup_type` dit d'où vient la compo et sert la priorité. `lineup_status`
conserve ce que Bzzoiro déclare (`confirmed`, `probable`…), et `published_at`
l'heure de publication. Sans eux, on ne peut pas répondre à « cette compo
était-elle officielle au moment du calcul ? ».

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `backend/tests/test_lineup_models.py` :

```python
def test_team_lineup_porte_sa_publication():
    """Savoir si une compo etait officielle AU MOMENT du calcul."""
    from app.models.lineups import TeamLineup

    cols = TeamLineup.__table__.columns
    assert "lineup_status" in cols
    assert "published_at" in cols
    # Nullables : les compos manuelles n'ont pas de statut Bzzoiro.
    assert cols["lineup_status"].nullable is True
    assert cols["published_at"].nullable is True
```

- [ ] **Step 2 : Lancer le test et vérifier qu'il échoue**

Run: `cd backend && uv run pytest tests/test_lineup_models.py -k porte_sa_publication -v`
Expected: FAIL — `AssertionError`

- [ ] **Step 3 : Ajouter les colonnes au modèle**

Dans `backend/app/models/lineups.py`, après `created_by` :

```python
    # Ce que Bzzoiro declare : "confirmed" quand la compo est officielle.
    # Nullable : une compo manuelle n'a pas de statut Bzzoiro.
    lineup_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Heure de publication annoncee par Bzzoiro (updated_at).
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

Vérifier que `datetime` et `DateTime` sont importés dans le module ; les
ajouter aux imports existants si nécessaire.

- [ ] **Step 4 : Écrire la migration**

Créer `backend/alembic/versions/055_team_lineups_publication.py` :

```python
"""Ajoute team_lineups.lineup_status et published_at.

lineup_type dit d'ou vient la compo et sert la priorite du resolveur.
lineup_status conserve ce que Bzzoiro declare ("confirmed" pour une compo
officielle) et published_at l'heure de publication -- de quoi savoir apres
coup si un prix a ete calcule sur une compo reelle ou supposee.

Revision ID: 055
Revises: 054
Create Date: 2026-08-25
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "055"
down_revision: str | None = "054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullables : les compos manuelles et les lignes existantes n'ont pas de
    # statut Bzzoiro.
    op.add_column(
        "team_lineups",
        sa.Column("lineup_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "team_lineups",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_lineups", "published_at")
    op.drop_column("team_lineups", "lineup_status")
```

- [ ] **Step 5 : Lancer le test et la migration**

Run: `cd backend && uv run pytest tests/test_lineup_models.py -k porte_sa_publication -v && uv run alembic upgrade head && uv run alembic current`
Expected: PASS, révision `055`

- [ ] **Step 6 : Commit**

```bash
git add backend/alembic/versions/055_team_lineups_publication.py backend/app/models/lineups.py backend/tests/test_lineup_models.py
git commit -m "feat(compos): tracer le statut et l'heure de publication"
```

---

### Task 3 : Ingestion des données de match (v2)

**Files:**
- Create: `backend/app/ingestion/bzzoiro/sync_match_detail.py`
- Test: `backend/tests/ingestion/bzzoiro/test_sync_match_detail.py`

**Interfaces:**
- Consomme : `BzzoiroClient.get_page`, `BzzEvent`, `TeamLineup`, `TeamLineupPlayer`, `Fixture`.
- Produit :
  - `async fetch_lineups(client, event_api_id) -> dict | None`
  - `async fetch_match_stats(client, event_api_id) -> dict | None`
  - `async ecrire_compos(session, fixture_id, equipes, brut) -> int`
  - `async sync_avant_match(session, client, heures=6) -> tuple[int, int]`
  - `async sync_apres_match(session, client, limite=200) -> tuple[int, int]`

**Forme des réponses**, relevées le 25/08/2026 :

`/api/v2/events/209544/lineups/` :

```json
{
  "event_id": 209544,
  "lineup_status": "confirmed",
  "beta": false,
  "updated_at": "2026-08-25T03:04:09Z",
  "lineups": {
    "home": {
      "formation": "4-2-3-1",
      "players": [{"id": 823, "name": "Bernd Leno", "short_name": "B. Leno",
                   "position": "G", "jersey_number": 1, "captain": false,
                   "ai_score": null}],
      "substitutes": []
    },
    "away": {"formation": "3-4-2-1", "players": [], "substitutes": []}
  },
  "unavailable_players": {
    "home": [{"id": 825, "name": "Joachim Andersen", "status": "suspended",
              "reason": "red_card_suspension"}],
    "away": []
  }
}
```

`/api/v2/events/209544/stats/` :

```json
{
  "event_id": 209544,
  "stats": {"home": {...}, "away": {...}, "first_half": {...}, "second_half": {...}},
  "xg_estimated": false,
  "shotmap": [{"pos": {"x": 26.6, "y": 41.8}, "gm": {"x": 0, "y": 50.9, "z": 29.8},
               "xg": 0.0286, "xgot": 0.0489, "min": 90, "type": "save",
               "body": "right-foot", "sit": "regular", "home": true}],
  "momentum": [], "average_positions": [], "xg_per_minute": []
}
```

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/ingestion/bzzoiro/test_sync_match_detail.py` :

```python
"""Ingestion des donnees de match depuis l'API v2.

Les colonnes shotmap/lineups/incidents de bzz_events existent depuis la CDM
mais sont restees vides pour le football de clubs : sync_events lit un champ
que l'API v1 ne renvoie pas. Mesure du 25/08/2026 : 0 compo et 0 carte de
tirs sur 8 965 matchs termines.
"""
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.bzzoiro.sync_match_detail import (
    fetch_lineups,
    fetch_match_stats,
    est_confirmee,
    type_de_compo,
)


def _reponse_compos(status="confirmed"):
    return {
        "event_id": 209544,
        "lineup_status": status,
        "updated_at": "2026-08-25T03:04:09Z",
        "lineups": {
            "home": {"formation": "4-2-3-1", "players": [
                {"id": 823, "name": "Bernd Leno", "position": "G",
                 "jersey_number": 1, "captain": False}], "substitutes": []},
            "away": {"formation": "3-4-2-1", "players": [], "substitutes": []},
        },
        "unavailable_players": {"home": [], "away": []},
    }


async def test_fetch_compos_appelle_le_bon_point_d_acces():
    client = MagicMock()
    client.get_page = AsyncMock(return_value=_reponse_compos())

    res = await fetch_lineups(client, 209544)

    assert res["lineup_status"] == "confirmed"
    client.get_page.assert_called_once_with("/api/v2/events/209544/lineups/")


async def test_fetch_compos_absentes_rend_none():
    """Un match sans compo publiee ne doit pas lever."""
    client = MagicMock()
    client.get_page = AsyncMock(side_effect=Exception("404"))

    assert await fetch_lineups(client, 999) is None


async def test_fetch_stats_appelle_le_bon_point_d_acces():
    client = MagicMock()
    client.get_page = AsyncMock(return_value={"event_id": 1, "shotmap": [{"xg": 0.1}]})

    res = await fetch_match_stats(client, 209544)

    assert len(res["shotmap"]) == 1
    client.get_page.assert_called_once_with("/api/v2/events/209544/stats/")


def test_compo_confirmee():
    assert est_confirmee(_reponse_compos("confirmed")) is True
    assert est_confirmee(_reponse_compos("predicted")) is False
    assert est_confirmee(None) is False
    assert est_confirmee({}) is False


def test_type_de_compo_suit_le_statut():
    """official quand Bzzoiro confirme, bzzoiro sinon.

    Ce sont les deux types que PRIORITY connait deja : le resolveur prefere
    official (0) a bzzoiro (1) sans qu'aucune ligne ne soit a reecrire.
    """
    assert type_de_compo(_reponse_compos("confirmed")) == "official"
    assert type_de_compo(_reponse_compos("predicted")) == "bzzoiro"
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/ingestion/bzzoiro/test_sync_match_detail.py -v`
Expected: FAIL — `ModuleNotFoundError: app.ingestion.bzzoiro.sync_match_detail`

- [ ] **Step 3 : Écrire les fonctions de récupération**

Créer `backend/app/ingestion/bzzoiro/sync_match_detail.py` :

```python
"""Ingestion des donnees de match depuis l'API Bzzoiro v2.

Les colonnes shotmap / incidents / momentum / average_positions / lineups de
bzz_events existent depuis la CDM 2026, mais sont restees vides pour le
football de clubs : sync_events lit `row.get("shotmap")` sur /api/events/,
qui ne renvoie pas ce champ. Mesure du 25/08/2026 sur les cinq championnats :
0 compo et 0 carte de tirs sur 8 965 matchs termines.

L'API v2 les fournit toutes. Deux regimes, dictes par la nature de la donnee :

- AVANT match, seules les compos evoluent. On interroge /lineups/ jusqu'a ce
  que lineup_status passe a "confirmed", puis on cesse.
- APRES match, tout devient definitif. On interroge /stats/, /incidents/ et
  /player-stats/ une fois, et on n'y revient plus.

Aucun de ces points d'acces n'est pagine : get_page, jamais get_all.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_IDS

logger = logging.getLogger(__name__)

# Championnats du perimetre : les cinq domestiques. La Ligue des champions (7)
# est exclue jusqu'aux tirages de la phase de ligue.
LEAGUES_FICHE_MATCH: list[int] = [
    v for k, v in TARGET_LEAGUE_INTERNAL_IDS.items() if k != "champions_league"
]


async def fetch_lineups(client: Any, event_api_id: int) -> dict[str, Any] | None:
    """Compos publiees pour ce match, ou None si aucune."""
    try:
        return await client.get_page(f"/api/v2/events/{event_api_id}/lineups/")
    except Exception as exc:
        logger.debug("compos indisponibles pour %s : %s", event_api_id, exc)
        return None


async def fetch_match_stats(client: Any, event_api_id: int) -> dict[str, Any] | None:
    """Statistiques, carte des tirs, momentum et positions moyennes."""
    try:
        return await client.get_page(f"/api/v2/events/{event_api_id}/stats/")
    except Exception as exc:
        logger.debug("stats indisponibles pour %s : %s", event_api_id, exc)
        return None


async def fetch_incidents(client: Any, event_api_id: int) -> list[dict] | None:
    """Buts, cartons et periodes."""
    try:
        data = await client.get_page(f"/api/v2/events/{event_api_id}/incidents/")
    except Exception as exc:
        logger.debug("incidents indisponibles pour %s : %s", event_api_id, exc)
        return None
    return data.get("incidents") if isinstance(data, dict) else data


def est_confirmee(brut: dict[str, Any] | None) -> bool:
    """Vrai si Bzzoiro declare la compo officielle.

    Valeurs observees le 25/08/2026 : "predicted" pour une compo probable
    publiee un a deux jours avant, "confirmed" pour l'officielle.
    """
    return bool(brut) and brut.get("lineup_status") == "confirmed"


def type_de_compo(brut: dict[str, Any] | None) -> str:
    """Type attendu par lineup_resolver.PRIORITY.

    official (0) quand Bzzoiro confirme, bzzoiro (1) sinon. Les deux
    coexistent : c'est ce qui historise la compo probable une fois
    l'officielle publiee, sans table supplementaire.
    """
    return "official" if est_confirmee(brut) else "bzzoiro"
```

- [ ] **Step 4 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/ingestion/bzzoiro/test_sync_match_detail.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5 : Écrire les tests de l'écriture des compos**

Ajouter au même fichier de test :

```python
from app.ingestion.bzzoiro.sync_match_detail import ecrire_compos


def _session():
    s = MagicMock()
    s.execute = AsyncMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    s.delete = AsyncMock()
    return s


async def test_ecrit_une_compo_par_camp():
    session = _session()
    vide = MagicMock()
    vide.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=vide)

    n = await ecrire_compos(
        session, fixture_id=7,
        equipes={"home": "Fulham", "away": "Chelsea"},
        brut=_reponse_compos("confirmed"),
    )

    assert n == 2


async def test_ne_supprime_jamais_une_compo_d_un_autre_type():
    """La coexistence de bzzoiro et official est ce qui historise.

    sync_bzzoiro_lineups supprime la ligne existante avant d'inserer ; ici
    on ne remplace que la ligne du MEME type.
    """
    autre = MagicMock()
    autre.lineup_type = "bzzoiro"
    session = _session()
    trouve = MagicMock()
    trouve.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=trouve)

    await ecrire_compos(
        session, fixture_id=7,
        equipes={"home": "Fulham", "away": "Chelsea"},
        brut=_reponse_compos("confirmed"),
    )

    # aucune suppression : la compo probable precedente reste en base
    session.delete.assert_not_called()


async def test_compo_absente_n_ecrit_rien():
    session = _session()
    assert await ecrire_compos(session, 7, {"home": "A", "away": "B"}, None) == 0
    session.add.assert_not_called()
```

- [ ] **Step 5 bis : Écrire les tests de la règle d'interrogation**

Ajouter au même fichier :

```python
from datetime import UTC, datetime, timedelta

from app.ingestion.bzzoiro.sync_match_detail import doit_interroger


def _session_types(types):
    s = MagicMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = list(types)
    s.execute = AsyncMock(return_value=r)
    return s


async def test_interroge_quand_aucune_compo():
    maintenant = datetime(2026, 8, 25, 12, tzinfo=UTC)
    ko = maintenant + timedelta(hours=40)
    assert await doit_interroger(_session_types([]), 7, ko, maintenant) is True


async def test_n_interroge_plus_une_fois_la_compo_officielle_connue():
    """Elle ne changera plus : inutile d'y revenir."""
    maintenant = datetime(2026, 8, 25, 12, tzinfo=UTC)
    ko = maintenant + timedelta(minutes=30)
    assert await doit_interroger(_session_types(["official"]), 7, ko, maintenant) is False


async def test_probable_en_base_suspend_jusqu_a_90_minutes():
    """Deux jours de requetes pour rien : on s'arrete apres la probable."""
    maintenant = datetime(2026, 8, 25, 12, tzinfo=UTC)
    loin = maintenant + timedelta(hours=40)
    proche = maintenant + timedelta(minutes=80)

    assert await doit_interroger(_session_types(["bzzoiro"]), 7, loin, maintenant) is False
    assert await doit_interroger(_session_types(["bzzoiro"]), 7, proche, maintenant) is True


async def test_reprise_exactement_au_seuil():
    maintenant = datetime(2026, 8, 25, 12, tzinfo=UTC)
    seuil = maintenant + timedelta(minutes=90)
    assert await doit_interroger(_session_types(["bzzoiro"]), 7, seuil, maintenant) is True


async def test_sans_coup_d_envoi_connu_on_interroge():
    """Mieux vaut une requete de trop qu'une compo manquee."""
    maintenant = datetime(2026, 8, 25, 12, tzinfo=UTC)
    assert await doit_interroger(_session_types(["bzzoiro"]), 7, None, maintenant) is True
```

- [ ] **Step 6 : Écrire l'écriture des compos**

Ajouter dans `sync_match_detail.py` :

```python
async def ecrire_compos(
    session: AsyncSession,
    fixture_id: int,
    equipes: dict[str, str],
    brut: dict[str, Any] | None,
) -> int:
    """Ecrit une ligne team_lineups par camp. Rend le nombre de compos ecrites.

    Ne remplace que la ligne du MEME type : une compo probable deja en base
    survit a la publication de l'officielle, ce qui historise sans table
    supplementaire.
    """
    from app.models.lineups import TeamLineup, TeamLineupPlayer

    if not brut:
        return 0

    blocs = brut.get("lineups") or {}
    statut = brut.get("lineup_status")
    publie = _parse_date(brut.get("updated_at"))
    type_compo = type_de_compo(brut)

    ecrites = 0
    for cote, nom_equipe in equipes.items():
        bloc = blocs.get(cote) or {}
        joueurs = bloc.get("players") or []
        if not joueurs:
            continue

        existante = (await session.execute(
            select(TeamLineup).where(
                TeamLineup.fixture_id == fixture_id,
                TeamLineup.team == nom_equipe,
                TeamLineup.lineup_type == type_compo,
            )
        )).scalar_one_or_none()

        if existante is not None:
            await session.delete(existante)
            await session.flush()

        compo = TeamLineup(
            fixture_id=fixture_id,
            team=nom_equipe,
            lineup_type=type_compo,
            source="bzzoiro_v2",
            created_by="system",
            lineup_status=statut,
            published_at=publie,
        )
        session.add(compo)
        await session.flush()

        remplacants = bloc.get("substitutes") or []
        tous = [(x, True) for x in joueurs] + [(x, False) for x in remplacants]
        for j, titulaire in tous:
            session.add(TeamLineupPlayer(
                lineup_id=compo.id,
                player_name=j.get("name") or "",
                # L'API rend G/D/M/F, le modele attend GK/DEF/MID/FWD.
                position=_POSITION_MAP.get(j.get("position") or "", "MID"),
                is_starter=titulaire,
                jersey_number=_entier(j.get("jersey_number")),
            ))
        ecrites += 1

    if ecrites:
        await session.commit()
    return ecrites


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
```

`TeamLineupPlayer` porte `player_name`, `position`, `is_starter` et
`jersey_number` — il n'y a **pas** de champ d'ordre. Les remplaçants sont
écrits dans la même compo avec `is_starter=False`.

`_POSITION_MAP` existe déjà dans `sync_bzzoiro_lineups.py` (`G→GK`, `D→DEF`,
`M→MID`, `F→FWD`) : l'importer plutôt que le redéfinir. Ajouter aussi :

```python
def _entier(v: Any) -> int | None:
    """Le numero de maillot est parfois rendu comme une chaine."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 7 : Lancer les tests**

Run: `cd backend && uv run pytest tests/ingestion/bzzoiro/test_sync_match_detail.py -v`
Expected: PASS

- [ ] **Step 8 : Écrire les deux orchestrations**

Ajouter dans `sync_match_detail.py` :

```python
# Une compo officielle parait peu avant le coup d'envoi. Tant qu'on detient
# deja la compo probable, interroger l'API pendant deux jours ne rapporte
# rien : on reprend a partir de ce delai.
REPRISE_AVANT_COUP_ENVOI = timedelta(minutes=90)


async def doit_interroger(
    session: AsyncSession,
    fixture_id: int,
    coup_envoi: datetime | None,
    maintenant: datetime,
) -> bool:
    """Faut-il encore interroger l'API pour ce match ?

    Trois cas :
      - compo officielle deja en base -> non, elle ne changera plus ;
      - aucune compo -> oui, on cherche la probable ;
      - probable en base -> non, jusqu'a 90 minutes du coup d'envoi.

    Contrepartie assumee : une compo probable revisee par Bzzoiro entre sa
    publication et ce delai ne sera pas captee. Elle le sera a la reprise.
    """
    from app.models.lineups import TeamLineup

    types = set((await session.execute(
        select(TeamLineup.lineup_type).where(TeamLineup.fixture_id == fixture_id)
    )).scalars().all())

    if "official" in types:
        return False
    if not types:
        return True
    if coup_envoi is None:
        return True
    return coup_envoi - maintenant <= REPRISE_AVANT_COUP_ENVOI


async def sync_avant_match(
    session: AsyncSession, client: Any, heures: int = 72
) -> tuple[int, int]:
    """Compos des matchs a venir. Rend (matchs vus, compos ecrites).

    Fenetre de 72 h : Bzzoiro publie une compo probable jusqu'a deux jours
    avant le match, ce qui permet de pricer tot. On cesse d'interroger un
    match des que sa compo est confirmee : elle ne changera plus.
    """
    from app.models.bzzoiro import BzzEvent
    from app.models.fixtures import Fixture
    from app.models.lineups import TeamLineup

    maintenant = datetime.now(UTC)
    evenements = (await session.execute(
        select(BzzEvent).where(
            BzzEvent.league_api_id.in_(LEAGUES_FICHE_MATCH),
            BzzEvent.event_date > maintenant,
            BzzEvent.event_date <= maintenant + timedelta(hours=heures),
        )
    )).scalars().all()

    vus = ecrites = 0
    for ev in evenements:
        # Le lien se fait par external_id, pas par une cle etrangere :
        # convention deja utilisee par sync_bzzoiro_lineups.
        fixture = (await session.execute(
            select(Fixture).where(Fixture.external_id == f"bzz_{ev.api_id}")
        )).scalar_one_or_none()
        if fixture is None:
            continue

        if not await doit_interroger(session, fixture.id, ev.event_date, maintenant):
            continue

        vus += 1
        brut = await fetch_lineups(client, ev.api_id)
        ecrites += await ecrire_compos(
            session, fixture.id,
            {"home": fixture.home_team, "away": fixture.away_team},
            brut,
        )

    logger.info("Compos avant match : %d matchs suivis, %d compos ecrites", vus, ecrites)
    return vus, ecrites


async def sync_apres_match(
    session: AsyncSession, client: Any, limite: int = 200
) -> tuple[int, int]:
    """Stats et carte des tirs des matchs termines. Rend (traites, incomplets).

    Un match dont un bloc revient vide n'est PAS marque traite : il sera
    retente. Ecrire du vide sans le signaler est ce qui a laisse 8 965 matchs
    sans donnees pendant des mois.
    """
    from app.models.bzzoiro import BzzEvent

    evenements = (await session.execute(
        select(BzzEvent).where(
            BzzEvent.league_api_id.in_(LEAGUES_FICHE_MATCH),
            BzzEvent.status == "finished",
            BzzEvent.shotmap.is_(None),
        ).order_by(BzzEvent.event_date.desc()).limit(limite)
    )).scalars().all()

    traites = incomplets = 0
    for ev in evenements:
        stats = await fetch_match_stats(client, ev.api_id)
        if not stats or not stats.get("shotmap"):
            incomplets += 1
            continue

        ev.shotmap = stats.get("shotmap")
        ev.momentum = stats.get("momentum")
        ev.average_positions = stats.get("average_positions")
        incidents = await fetch_incidents(client, ev.api_id)
        if incidents:
            ev.incidents = incidents
        traites += 1

    if traites:
        await session.commit()
    logger.info("Apres match : %d traites, %d incomplets", traites, incomplets)
    return traites, incomplets
```

`Fixture` n'a **pas** de clé étrangère vers `BzzEvent` : le lien se fait par
`Fixture.external_id == f"bzz_{event.api_id}"`, convention déjà utilisée par
`sync_bzzoiro_lineups.py:178`.

- [ ] **Step 9 : Suite complète**

Run: `cd backend && uv run pytest -q`
Expected: PASS

- [ ] **Step 10 : Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_match_detail.py backend/tests/ingestion/bzzoiro/test_sync_match_detail.py
git commit -m "feat(match): ingerer compos, tirs et stats depuis l'API v2"
```

---

### Task 4 : Planification des deux jobs

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_bzzoiro_worker_jobs.py` (ajout)

**Interfaces:**
- Consomme : `sync_avant_match`, `sync_apres_match` de la tâche 3.
- Produit : `job_sync_compos_avant_match`, `job_sync_donnees_apres_match`.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à `backend/tests/test_bzzoiro_worker_jobs.py` :

```python
async def test_job_compos_avant_match_sans_cle_ne_fait_rien():
    from app.worker import job_sync_compos_avant_match

    with patch("app.worker.settings") as s, patch(
        "app.worker.sync_avant_match", new=AsyncMock()
    ) as m:
        s.bzzoiro_api_key = None
        await job_sync_compos_avant_match()
    m.assert_not_called()


async def test_job_apres_match_sans_cle_ne_fait_rien():
    from app.worker import job_sync_donnees_apres_match

    with patch("app.worker.settings") as s, patch(
        "app.worker.sync_apres_match", new=AsyncMock()
    ) as m:
        s.bzzoiro_api_key = None
        await job_sync_donnees_apres_match()
    m.assert_not_called()
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/test_bzzoiro_worker_jobs.py -k "avant_match or apres_match" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3 : Écrire les deux jobs**

Dans `backend/app/worker.py`, ajouter l'import en tête :

```python
from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match, sync_avant_match
```

Puis, près de `job_sync_bzzoiro_lineups` :

```python
async def job_sync_compos_avant_match() -> None:
    """Toutes les 5 min : suit les compos des matchs a venir.

    Bzzoiro publie une compo "predicted" un a deux jours avant le match
    (mesure du 25/08/2026 : Real Madrid-Real Sociedad a J-23h, Barcelone-
    Athletic a J-47h), puis la passe a "confirmed" peu avant le coup d'envoi.
    On cesse de suivre un match des qu'elle est confirmee : elle ne changera
    plus.
    """
    if not settings.bzzoiro_api_key:
        return
    try:
        async with async_session() as session, BzzoiroClient(
            settings.bzzoiro_api_key
        ) as client:
            await sync_avant_match(session, client)
    except Exception as exc:
        logger.error("Echec du suivi des compos : %s", exc, exc_info=True)


async def job_sync_donnees_apres_match() -> None:
    """Toutes les heures : carte des tirs et statistiques des matchs termines.

    Ces donnees sont definitives : un match traite ne l'est qu'une fois.
    """
    if not settings.bzzoiro_api_key:
        return
    try:
        async with async_session() as session, BzzoiroClient(
            settings.bzzoiro_api_key
        ) as client:
            await sync_apres_match(session, client)
    except Exception as exc:
        logger.error("Echec des donnees d'apres match : %s", exc, exc_info=True)
```

- [ ] **Step 4 : Planifier**

Dans la fonction de planification, après le bloc `sync_bzzoiro_lineups` :

```python
    # Compos des matchs a venir. 5 minutes : c'est le delai maximal entre la
    # publication de la compo officielle par Bzzoiro et sa disponibilite chez
    # nous. Le job sort immediatement s'il n'y a aucun match dans la fenetre.
    scheduler.add_job(
        job_sync_compos_avant_match,
        IntervalTrigger(minutes=5),
        id="sync_compos_avant_match",
        name="Compos Bzzoiro — matchs a venir",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Donnees d'apres match — definitives, une passe horaire suffit.
    scheduler.add_job(
        job_sync_donnees_apres_match,
        IntervalTrigger(hours=1),
        id="sync_donnees_apres_match",
        name="Tirs et stats Bzzoiro — matchs termines",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 5 : Lancer les tests**

Run: `cd backend && uv run pytest tests/test_bzzoiro_worker_jobs.py -v && uv run pytest -q`
Expected: PASS

- [ ] **Step 6 : Commit**

```bash
git add backend/app/worker.py backend/tests/test_bzzoiro_worker_jobs.py
git commit -m "feat(match): planifier le suivi des compos et des donnees d'apres match"
```

---

### Task 5 : La fiche match

**Files:**
- Create: `backend/app/api/matches.py`
- Modify: `backend/app/main.py`
- Create: `frontend/src/app/dashboard/matches/[id]/page.tsx`
- Modify: `frontend/src/app/dashboard/matches/page.tsx` (liste au lieu de la redirection)
- Test: `backend/tests/api/test_matches_api.py`

**Interfaces:**
- Consomme : `BzzEvent`, `TeamLineup`, `BzzPlayerMatchStat`, et les composants
  CDM `ShotMap`, `MatchPitch`, `MatchDetailPanel`.
- Produit : `GET /api/v1/matches` (liste), `GET /api/v1/matches/{event_api_id}` (fiche).

**Contexte :** `app/api/wc2026_matches.py` sert le même besoin pour la CDM.
Ses modèles de sortie (`ShotPoint`, `MatchDetail`) et son `_parse_shotmap`
sont réutilisables tels quels — les copier plutôt que les importer serait une
duplication à éviter ; les extraire dans un module partagé si nécessaire.

**Différence essentielle avec la CDM :** la fiche club lit **exclusivement la
base**. Elle n'appelle jamais l'API Bzzoiro, contrairement à
`get_match_detail` qui interroge en direct quand le cache est vide. La page
montre ce qui est archivé, comme le Sanctuaire.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/api/test_matches_api.py` :

```python
"""Fiche match : lecture de la base, jamais de l'API."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.matches import get_match_detail


def _event(**kw):
    e = MagicMock()
    e.api_id = 209544
    e.home_team, e.away_team = "Fulham", "Chelsea"
    e.home_score, e.away_score = 2, 3
    e.status = "finished"
    e.shotmap = [{"pos": {"x": 1, "y": 2}, "xg": 0.1, "type": "goal", "home": True}]
    e.incidents = []
    e.momentum = None
    e.average_positions = None
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def _session(event):
    s = MagicMock()
    r = MagicMock()
    r.scalar_one_or_none.return_value = event
    r.scalars.return_value.all.return_value = []
    s.execute = AsyncMock(return_value=r)
    return s


async def test_fiche_rend_les_blocs_presents():
    d = await get_match_detail(209544, session=_session(_event()))
    assert d["home_team"] == "Fulham"
    assert len(d["shotmap"]) == 1


async def test_match_inconnu_rend_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_match_detail(1, session=_session(None))
    assert exc.value.status_code == 404


async def test_bloc_absent_est_signale_pas_masque():
    """Une carte des tirs absente doit se voir, pas se confondre avec zero tir."""
    d = await get_match_detail(209544, session=_session(_event(shotmap=None)))
    assert d["shotmap"] == []
    assert d["blocs_manquants"] == ["shotmap"]
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/api/test_matches_api.py -v`
Expected: FAIL — `ModuleNotFoundError: app.api.matches`

- [ ] **Step 3 : Écrire le point d'accès**

Créer `backend/app/api/matches.py` avec :

- `GET /matches` — liste paginée des matchs du périmètre, triés par date
  décroissante, avec filtres `league_api_id` et `team`.
- `GET /matches/{event_api_id}` — fiche complète.

La fiche rend un dictionnaire portant : identité du match et score, `shotmap`
(liste, vide si absente), `incidents`, `momentum`, `average_positions`, les
deux compos avec leur `lineup_type`, `lineup_status` et `published_at`, les
statistiques joueur, et **`blocs_manquants`** — la liste des blocs absents.

Ce dernier champ est le point important : une carte des tirs vide et une
carte des tirs absente ne veulent pas dire la même chose, et l'interface doit
pouvoir les distinguer.

Enregistrer le routeur dans `backend/app/main.py`, à côté des autres :

```python
app.include_router(matches_api.router, prefix="/api/v1", tags=["matches"])
```

- [ ] **Step 4 : Lancer les tests**

Run: `cd backend && uv run pytest tests/api/test_matches_api.py -v && uv run pytest -q`
Expected: PASS

- [ ] **Step 5 : Écrire la liste côté page**

Remplacer `frontend/src/app/dashboard/matches/page.tsx` (la redirection de la
tâche 1) par une liste des matchs : date, équipes, score, et un lien vers la
fiche. Filtres championnat et équipe, alimentés par les points d'accès
existants.

- [ ] **Step 6 : Écrire la fiche**

Créer `frontend/src/app/dashboard/matches/[id]/page.tsx`, organisée comme la
fiche CDM : Résumé, Compos, Stats, Shot map, Stats joueur.

Réutiliser `ShotMap`, `MatchPitch` et `JerseyCard` depuis
`@/components/wc2026/`.

**La compo porte son statut à l'écran.** Une compo `official` est présentée
comme officielle avec son heure de publication ; une compo `bzzoiro` comme
probable ; une compo `last_known` indique explicitement qu'elle provient d'un
match précédent. Pricer sur la dernière compo connue n'est pas pricer sur la
compo du jour, et cela doit se voir.

Un bloc listé dans `blocs_manquants` affiche une mention discrète plutôt
qu'une zone vide.

- [ ] **Step 7 : Vérifier le frontend**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: compile sans erreur

- [ ] **Step 8 : Commit**

```bash
git add backend/app/api/matches.py backend/app/main.py backend/tests/api/test_matches_api.py frontend/src/app/dashboard/matches
git commit -m "feat(match): fiche match lue depuis la base"
```

---

## Après la fusion

Le rattrapage des matchs terminés ne s'exécute pas tout seul. Une fois le
déploiement **terminé** — vérifier que le déploiement Dokploy est en `done`
avant de lancer quoi que ce soit :

```bash
docker exec ev0-compose-z5hvqt-worker-1 python -c "
import asyncio
from app.config import settings
from app.db import async_session
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match
async def m():
    async with async_session() as s, BzzoiroClient(settings.bzzoiro_api_key) as c:
        while True:
            traites, _ = await sync_apres_match(s, c, limite=500)
            if not traites:
                break
asyncio.run(m())"
```

Compter environ 9 000 matchs, soit deux appels chacun.

**Ne fusionner aucune autre PR pendant ce temps** : Dokploy recrée le
conteneur au déploiement, ce qui tue le processus en cours.
