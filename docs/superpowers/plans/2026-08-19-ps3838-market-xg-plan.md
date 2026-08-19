# xG d'équipe depuis PS3838 — plan d'implémentation

> **Pour les workers agentiques :** SUB-SKILL requise — `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Les étapes utilisent des cases `- [ ]`.

**Goal :** faire du xG d'équipe une donnée fiable, dérivée d'une source unique (PS3838) rattachée aux matchs par identifiant et non par nom, et archiver l'ouverture et le closing de chaque match.

**Architecture :** un client PS3838 lit la catégorie football en deux appels et retourne des événements typés ; chaque fixture est ancrée une fois à un `ps3838_event_id` (équipes **et** date vérifiées) ; les cotes sont ensuite récupérées par identifiant et stockées dans `match_odds_snapshots` sous `bookmaker='ps3838'` ; `MarketXgService` ne lit plus que cette source ; deux lignes par match (ouverture, closing) sont archivées définitivement dans `team_xg_estimates`.

**Tech Stack :** Python 3.13, httpx, SQLAlchemy 2 async, Alembic, APScheduler, scipy (`brentq`), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

## Global Constraints

- Spec de référence : `docs/superpowers/specs/2026-08-19-ps3838-market-xg-design.md`.
- Tests depuis `backend/` : `cd backend && uv run pytest …`. `asyncio_mode = "auto"` — pas de `@pytest.mark.asyncio`.
- Migrations : la tête actuelle est **050**. Ce plan ajoute **051** puis **052**.
- **Aucune cadence de scraping n'est créée ni modifiée.** PS3838 est appelé depuis `job_odds_scheduler_tick` (60 s) et hérite des seuils existants d'`odds_scheduler.py` : > 6 h → 2 h, 2–6 h → 30 min, < 2 h → 2 min, arrêt à KO−5 min.
- **Jamais de rattachement par nom au moment du scraping.** Le nom ne sert qu'à la résolution initiale de l'identifiant, avec date concordante.
- **Aucun repli silencieux.** Un match non ancré n'est pas pricé ; il est signalé sur le canal `incidents` s'il est à moins de 7 jours.
- L'ordre du 1X2 PS3838 est `[extérieur, domicile, nul]`. Ne jamais l'écrire autrement.
- `team_xg_estimates` ne doit jamais être ajoutée à `job_purge_old_snapshots`.
- Les canaux d'alerte sont `value` / `incidents` / `autopilot` ; `send_alert(message, channel)` exige le canal.

## Structure des fichiers

| Fichier | Responsabilité | Tâches |
|---|---|---|
| `app/ingestion/ps3838/__init__.py` | paquet | 1 |
| `app/ingestion/ps3838/client.py` | HTTP + parsing du flux football → événements typés | 1 |
| `app/ingestion/ps3838/anchor.py` | résolution fixture ↔ `ps3838_event_id` | 3 |
| `app/ingestion/ps3838/scraper.py` | événements ancrés → `MatchScrapeResult` | 4 |
| `alembic/versions/051_fixtures_ps3838_event_id.py` | colonne d'ancrage | 2 |
| `alembic/versions/052_team_xg_estimates_phase.py` | colonne `phase` + unicité | 6 |
| `app/models/fixtures.py` | champ ORM `ps3838_event_id` | 2 |
| `app/models/team_xg.py` | champ ORM `phase` | 6 |
| `app/services/market_xg.py` | lecture `ps3838` seule, ligne de totals généralisée | 5 |
| `app/services/xg_library.py` | capture ouverture + closing | 6 |
| `app/ingestion/odds_scheduler.py` | appel du scraper PS3838 dans le tick | 4 |
| `app/worker.py` | jobs bibliothèque + alerte 7 jours | 6, 7 |
| `tests/ingestion/ps3838/` | tests client, ancrage, scraper | 1, 3, 4 |
| `tests/services/test_totals_line.py` | solveur multi-lignes | 5 |
| `tests/services/test_xg_library.py` | ouverture/closing idempotents | 6 |
| `tests/test_ps3838_anchor_alert.py` | alerte 7 jours | 7 |

---

### Task 1 : Client PS3838 — lecture et parsing

**Files:**
- Create: `backend/app/ingestion/ps3838/__init__.py`
- Create: `backend/app/ingestion/ps3838/client.py`
- Create: `backend/tests/ingestion/ps3838/__init__.py`
- Test: `backend/tests/ingestion/ps3838/test_ps3838_client.py`
- Fixture: `backend/tests/fixtures/ps3838_events.json`

**Interfaces:**
- Consumes: rien.
- Produces :
  - `@dataclass Ps3838Event` — champs : `event_id: int`, `home: str`, `away: str`, `kickoff_utc: datetime`, `league: str`, `h2h: dict[str, float] | None` (clés `home`/`draw`/`away`), `totals: dict[str, float] | None` (clés `over_<ligne>`/`under_<ligne>`, ex. `over_3.0`), `total_line: float | None`
  - `parse_events(payload: dict) -> list[Ps3838Event]`
  - `async fetch_events() -> list[Ps3838Event]` — fusionne les deux appels, l'événement du flux imminent prime en cas de doublon

**Contexte.** PS3838 n'a pas de page par compétition : tout passe par la catégorie football (`sp=29`). Deux appels sont nécessaires — le premier renvoie les matchs imminents (~2 h), le second ceux à partir du lendemain — et un match à 3 h du coup d'envoi peut n'être dans aucun des deux. Le piège principal est l'ordre du 1X2, qui est `[extérieur, domicile, nul]` et non l'inverse.

- [ ] **Étape 1 : enregistrer un fixture réel**

```bash
cd backend && mkdir -p tests/fixtures tests/ingestion/ps3838 && touch tests/ingestion/ps3838/__init__.py
curl -s --max-time 60 \
  -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36" \
  -H "Accept: application/json" \
  "https://www.ps3838.com/sports-service/sv/compact/events?sp=29&mk=0&pa=0" \
  -o tests/fixtures/ps3838_events.json
python3 -c "import json; d=json.load(open('tests/fixtures/ps3838_events.json')); print('blocs:', [k for k in d if d.get(k)])"
```

Attendu : au moins un bloc non vide (typiquement `n`).

- [ ] **Étape 2 : écrire les tests qui échouent**

Créer `backend/tests/ingestion/ps3838/test_ps3838_client.py` :

```python
import json
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.ps3838.client import Ps3838Event, parse_events

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ps3838_events.json"


def _payload():
    return json.loads(FIXTURE.read_text())


def test_parse_returns_events():
    evs = parse_events(_payload())
    assert len(evs) > 50
    assert all(isinstance(e, Ps3838Event) for e in evs)


def test_event_fields_are_typed():
    e = next(e for e in parse_events(_payload()) if e.h2h)
    assert isinstance(e.event_id, int)
    assert isinstance(e.home, str) and e.home
    assert isinstance(e.away, str) and e.away
    assert isinstance(e.kickoff_utc, datetime)
    assert e.kickoff_utc.tzinfo is not None


def test_h2h_order_is_away_home_draw():
    """PS3838 range le 1X2 en [exterieur, domicile, nul]. Inverser dom/ext est
    exactement la classe de bug que ce chantier elimine."""
    raw = ["7.130", "1.386", "5.110"]
    from app.ingestion.ps3838.client import _parse_h2h

    assert _parse_h2h(raw) == {"home": 1.386, "draw": 5.11, "away": 7.13}


def test_h2h_missing_or_empty_returns_none():
    from app.ingestion.ps3838.client import _parse_h2h

    assert _parse_h2h(None) is None
    assert _parse_h2h(["", "", None]) is None
    assert _parse_h2h(["1.5"]) is None


def test_totals_uses_line_closest_to_2_5_and_ignores_quarter_lines():
    from app.ingestion.ps3838.client import _parse_totals

    raw = [["3-3.5", 3.25, "2.090", "1.793"], ["3.0", 3.0, "1.854", "2.040"]]
    line, totals = _parse_totals(raw)
    assert line == 3.0
    assert totals == {"over_3.0": 1.854, "under_3.0": 2.04}


def test_totals_prefers_half_integer_when_available():
    from app.ingestion.ps3838.client import _parse_totals

    raw = [["3.0", 3.0, "1.85", "2.04"], ["2.5", 2.5, "1.60", "2.35"]]
    line, totals = _parse_totals(raw)
    assert line == 2.5
    assert totals == {"over_2.5": 1.6, "under_2.5": 2.35}


def test_totals_empty_returns_none():
    from app.ingestion.ps3838.client import _parse_totals

    assert _parse_totals([]) == (None, None)
    assert _parse_totals(None) == (None, None)


def test_event_ids_are_unique():
    evs = parse_events(_payload())
    ids = [e.event_id for e in evs]
    assert len(ids) == len(set(ids))
```

- [ ] **Étape 3 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/ingestion/ps3838/test_ps3838_client.py -q
```
Attendu : ÉCHEC (`ModuleNotFoundError: app.ingestion.ps3838`).

- [ ] **Étape 4 : implémenter le client**

Créer `backend/app/ingestion/ps3838/__init__.py` (vide) puis `backend/app/ingestion/ps3838/client.py` :

```python
"""Client PS3838 — cotes 1X2 et totals depuis la categorie football.

PS3838 est une declinaison de la plateforme Pinnacle : memes identifiants
d'evenements, memes prix, mais joignable depuis le VPS la ou
guest.api.arcadia.pinnacle.com repond 403.

Pas de page par competition : tout passe par sportId=29.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://www.ps3838.com/sports-service/sv/compact/events"
_SOCCER = 29
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
_TIMEOUT = 30.0

# Le premier renvoie les matchs imminents (~2h), le second ceux a partir du
# lendemain. Un match a 3h du coup d'envoi peut n'etre dans aucun des deux :
# les deux appels sont faits a chaque cycle et fusionnes.
_QUERIES = ({"sp": _SOCCER}, {"sp": _SOCCER, "mk": 0, "pa": 0})


@dataclass
class Ps3838Event:
    event_id: int
    home: str
    away: str
    kickoff_utc: datetime
    league: str
    h2h: dict[str, float] | None = None
    totals: dict[str, float] | None = None
    total_line: float | None = None


def _f(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 1.0 else None


def _parse_h2h(raw) -> dict[str, float] | None:
    """PS3838 range le 1X2 en [exterieur, domicile, nul]."""
    if not raw or len(raw) < 3:
        return None
    away, home, draw = _f(raw[0]), _f(raw[1]), _f(raw[2])
    if home is None or draw is None or away is None:
        return None
    return {"home": home, "draw": draw, "away": away}


def _parse_totals(raw) -> tuple[float | None, dict[str, float] | None]:
    """Retient la ligne principale la plus proche de 2.5.

    Les lignes quart (label contenant '-', ex. '3-3.5') sont ignorees : elles
    ne se resolvent pas par un simple over/under.
    """
    if not raw:
        return None, None

    candidates: list[tuple[float, float, float]] = []
    for entry in raw:
        if not entry or len(entry) < 4:
            continue
        label, line, over, under = entry[0], entry[1], _f(entry[2]), _f(entry[3])
        if isinstance(label, str) and "-" in label:
            continue
        try:
            line = float(line)
        except (TypeError, ValueError):
            continue
        if over is None or under is None:
            continue
        candidates.append((line, over, under))

    if not candidates:
        return None, None

    # Ligne demi-entiere privilegiee (pas de push), puis proximite a 2.5.
    line, over, under = min(
        candidates, key=lambda c: (abs(c[0] - round(c[0])) < 1e-9, abs(c[0] - 2.5))
    )
    key = f"{line:g}" if line != int(line) else f"{line:.1f}"
    return line, {f"over_{key}": over, f"under_{key}": under}


def parse_events(payload: dict) -> list[Ps3838Event]:
    """Extrait les evenements de tous les blocs exploitables du payload."""
    out: dict[int, Ps3838Event] = {}
    for block in (payload or {}).values():
        if not isinstance(block, list):
            continue
        for sport in block:
            if not (isinstance(sport, list) and len(sport) > 2 and isinstance(sport[2], list)):
                continue
            for league in sport[2]:
                if not (isinstance(league, list) and len(league) > 2 and isinstance(league[2], list)):
                    continue
                league_name = league[1] if isinstance(league[1], str) else ""
                for ev in league[2]:
                    if not (isinstance(ev, list) and len(ev) > 8 and isinstance(ev[0], int)):
                        continue
                    periods = ev[8] if isinstance(ev[8], dict) else {}
                    full = periods.get("0") or []
                    h2h = _parse_h2h(full[2] if len(full) > 2 else None)
                    line, totals = _parse_totals(full[1] if len(full) > 1 else None)
                    try:
                        ko = datetime.fromtimestamp(ev[4] / 1000, UTC)
                    except (TypeError, ValueError, OSError):
                        continue
                    out[ev[0]] = Ps3838Event(
                        event_id=ev[0],
                        home=str(ev[1]),
                        away=str(ev[2]),
                        kickoff_utc=ko,
                        league=league_name,
                        h2h=h2h,
                        totals=totals,
                        total_line=line,
                    )
    return list(out.values())


async def fetch_events() -> list[Ps3838Event]:
    """Les deux flux, fusionnes. L'imminent prime en cas de doublon."""
    merged: dict[int, Ps3838Event] = {}
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        for params in reversed(_QUERIES):  # le flux imminent ecrase l'autre
            try:
                r = await client.get(_BASE, params=params)
                r.raise_for_status()
                for ev in parse_events(r.json()):
                    merged[ev.event_id] = ev
            except Exception as exc:
                logger.warning("PS3838: appel %s echoue: %s", params, exc)
    logger.info("PS3838: %d evenements", len(merged))
    return list(merged.values())
```

- [ ] **Étape 5 : lancer les tests**

```bash
cd backend && uv run pytest tests/ingestion/ps3838/test_ps3838_client.py -v
```
Attendu : SUCCÈS.

- [ ] **Étape 6 : commit**

```bash
git add backend/app/ingestion/ps3838/ backend/tests/ingestion/ps3838/ backend/tests/fixtures/ps3838_events.json
git commit -m "feat(ps3838): client de lecture du flux football (1X2 + totals)"
```

---

### Task 2 : Migration 051 — colonne d'ancrage

**Files:**
- Create: `backend/alembic/versions/051_fixtures_ps3838_event_id.py`
- Modify: `backend/app/models/fixtures.py`
- Test: `backend/tests/test_ps3838_anchor_migration.py`

**Interfaces:**
- Consumes: rien.
- Produces: `Fixture.ps3838_event_id: Mapped[int | None]` — identifiant d'événement PS3838, `None` tant que non résolu.

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `backend/tests/test_ps3838_anchor_migration.py` :

```python
import importlib.util
from pathlib import Path

from app.models.fixtures import Fixture


def test_fixture_has_ps3838_event_id():
    col = Fixture.__table__.columns.get("ps3838_event_id")
    assert col is not None, "colonne ps3838_event_id absente du modele"
    assert col.nullable is True


def test_migration_051_follows_050():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "051_fixtures_ps3838_event_id.py"
    )
    spec = importlib.util.spec_from_file_location("m051", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "051"
    assert module.down_revision == "050"
    assert hasattr(module, "upgrade") and hasattr(module, "downgrade")
```

- [ ] **Étape 2 : lancer le test, vérifier qu'il échoue**

```bash
cd backend && uv run pytest tests/test_ps3838_anchor_migration.py -q
```
Attendu : ÉCHEC (colonne absente, puis `FileNotFoundError`).

- [ ] **Étape 3 : écrire la migration**

Créer `backend/alembic/versions/051_fixtures_ps3838_event_id.py` :

```python
"""Add fixtures.ps3838_event_id (ancrage des cotes par identifiant).

Revision ID: 051
Revises: 050
Create Date: 2026-08-19
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixtures", sa.Column("ps3838_event_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_fixtures_ps3838_event_id", "fixtures", ["ps3838_event_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_fixtures_ps3838_event_id", table_name="fixtures")
    op.drop_column("fixtures", "ps3838_event_id")
```

- [ ] **Étape 4 : ajouter le champ au modèle**

Dans `backend/app/models/fixtures.py`, à côté des autres identifiants externes :

```python
    # Ancrage PS3838 : resolu une seule fois (equipes + date), puis les cotes
    # sont recuperees par cet identifiant, jamais par nom.
    ps3838_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
```

Vérifier que `Integer` est bien importé depuis `sqlalchemy` en tête du fichier ; l'ajouter à l'import existant sinon.

- [ ] **Étape 5 : lancer les tests**

```bash
cd backend && uv run pytest tests/test_ps3838_anchor_migration.py -v && uv run pytest tests/ -q
```
Attendu : SUCCÈS, aucune régression.

- [ ] **Étape 6 : commit**

```bash
git add backend/alembic/versions/051_fixtures_ps3838_event_id.py backend/app/models/fixtures.py backend/tests/test_ps3838_anchor_migration.py
git commit -m "feat(db): migration 051 — fixtures.ps3838_event_id"
```

---

### Task 3 : Résolution des identifiants

**Files:**
- Create: `backend/app/ingestion/ps3838/anchor.py`
- Test: `backend/tests/ingestion/ps3838/test_ps3838_anchor.py`

**Interfaces:**
- Consumes: `Ps3838Event` (Task 1), `Fixture.ps3838_event_id` (Task 2).
- Produces :
  - `norm_team(name: str) -> set[str]` — tokens normalisés (accents pliés, suffixes de club retirés)
  - `match_event(fixture, events: list[Ps3838Event]) -> Ps3838Event | None`
  - `async resolve_anchors(session, events: list[Ps3838Event]) -> tuple[int, list[str]]` — `(nb_resolus, libellés_non_resolus)`

**Contexte.** C'est le seul endroit où un nom d'équipe est comparé. La double vérification (équipes **et** coup d'envoi à ±2 h) est ce qui empêche d'ancrer le mauvais match. Un seul critère ne suffit pas : « Málaga » joue plusieurs matchs, et plusieurs matchs ont lieu à 19 h.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/ingestion/ps3838/test_ps3838_anchor.py` :

```python
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.ingestion.ps3838.anchor import match_event, norm_team
from app.ingestion.ps3838.client import Ps3838Event

KO = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)


def _ev(eid, home, away, ko=KO):
    return Ps3838Event(eid, home, away, ko, "Spain - La Liga", {"home": 1.4, "draw": 5.0, "away": 8.0}, {"over_3.0": 1.8, "under_3.0": 2.0}, 3.0)


def _fx(home, away, ko=KO):
    return SimpleNamespace(id=1, home_team=home, away_team=away, kickoff_utc=ko)


def test_norm_folds_accents_and_strips_club_suffixes():
    assert norm_team("Atlético Madrid") == norm_team("Atletico Madrid")
    assert norm_team("Málaga CF") == norm_team("Malaga")
    assert "madrid" in norm_team("Real Madrid CF")


def test_exact_match_resolves():
    evs = [_ev(111, "Atletico Madrid", "Malaga")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs).event_id == 111


def test_same_teams_different_day_does_not_resolve():
    evs = [_ev(111, "Atletico Madrid", "Malaga", KO + timedelta(days=1))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_two_hour_tolerance():
    evs = [_ev(111, "Atletico Madrid", "Malaga", KO + timedelta(hours=1, minutes=59))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs).event_id == 111
    evs = [_ev(222, "Atletico Madrid", "Malaga", KO + timedelta(hours=2, minutes=1))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_reversed_teams_do_not_resolve():
    """Domicile et exterieur inverses : ce n'est pas le meme match."""
    evs = [_ev(111, "Malaga", "Atletico Madrid")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_ambiguous_candidates_do_not_resolve():
    """Deux evenements plausibles a la meme heure : on ne devine pas."""
    evs = [_ev(111, "Atletico Madrid", "Malaga"), _ev(222, "Atletico Madrid", "Malaga")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_partial_team_overlap_is_not_enough():
    """'Real Madrid' vs 'Real Sociedad' partagent un token : insuffisant."""
    evs = [_ev(111, "Real Sociedad", "Malaga")]
    assert match_event(_fx("Real Madrid", "Málaga CF"), evs) is None
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/ingestion/ps3838/test_ps3838_anchor.py -q
```
Attendu : ÉCHEC (`ModuleNotFoundError: app.ingestion.ps3838.anchor`).

- [ ] **Étape 3 : implémenter la résolution**

Créer `backend/app/ingestion/ps3838/anchor.py` :

```python
"""Resolution fixture <-> evenement PS3838.

SEUL endroit du chantier ou un nom d'equipe est compare. Une fois l'identifiant
pose, les cotes sont recuperees par identifiant : plus aucun rapprochement
approximatif au moment du scraping.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.ps3838.client import Ps3838Event

logger = logging.getLogger(__name__)

MAX_KICKOFF_DELTA = timedelta(hours=2)

_STOP = {
    "fc", "cf", "sc", "ac", "cd", "ud", "sd", "as", "ss", "ssc", "afc", "rc",
    "club", "de", "la", "le", "los", "el", "sk", "nk", "gnk", "calcio", "cfr",
}


def norm_team(name: str) -> set[str]:
    """Tokens normalises : accents plies, suffixes de club retires."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {t for t in s.split() if t and t not in _STOP and len(t) > 2}


def _same_team(a: str, b: str) -> bool:
    ta, tb = norm_team(a), norm_team(b)
    if not ta or not tb:
        return False
    # Un cote doit etre entierement contenu dans l'autre : 'Real Madrid' et
    # 'Real Sociedad' partagent 'real' mais ne se contiennent pas.
    return ta <= tb or tb <= ta


def match_event(fixture, events: list[Ps3838Event]) -> Ps3838Event | None:
    """Evenement correspondant, ou None. Ne devine jamais.

    Exige les deux equipes dans le bon sens ET un coup d'envoi a +/- 2 h.
    Renvoie None si plusieurs candidats subsistent.
    """
    ko = fixture.kickoff_utc
    if ko is None:
        return None
    if ko.tzinfo is None:
        ko = ko.replace(tzinfo=UTC)

    hits = [
        ev for ev in events
        if abs(ev.kickoff_utc - ko) <= MAX_KICKOFF_DELTA
        and _same_team(fixture.home_team, ev.home)
        and _same_team(fixture.away_team, ev.away)
    ]
    return hits[0] if len(hits) == 1 else None


async def resolve_anchors(
    session: AsyncSession, events: list[Ps3838Event]
) -> tuple[int, list[str]]:
    """Pose ps3838_event_id sur les fixtures a venir non encore ancrees.

    Retourne (nb_resolus, libelles_non_resolus). Les non-resolus sont retournes
    pour surfacage, jamais devines.
    """
    from datetime import datetime

    from app.models.fixtures import Fixture

    now = datetime.now(UTC)
    rows = (await session.execute(
        select(Fixture).where(
            Fixture.ps3838_event_id.is_(None),
            Fixture.kickoff_utc > now,
            Fixture.status.notin_(["finished", "cancelled", "postponed"]),
        )
    )).scalars().all()

    taken = set(
        (await session.execute(
            select(Fixture.ps3838_event_id).where(Fixture.ps3838_event_id.isnot(None))
        )).scalars().all()
    )

    resolved = 0
    unresolved: list[str] = []
    for fx in rows:
        ev = match_event(fx, events)
        if ev is None or ev.event_id in taken:
            unresolved.append(f"{fx.home_team} - {fx.away_team} ({fx.kickoff_utc:%d/%m %H:%M})")
            continue
        fx.ps3838_event_id = ev.event_id
        taken.add(ev.event_id)
        resolved += 1

    await session.commit()
    logger.info("PS3838 anchor: %d resolus, %d non resolus", resolved, len(unresolved))
    return resolved, unresolved
```

- [ ] **Étape 4 : lancer les tests**

```bash
cd backend && uv run pytest tests/ingestion/ps3838/ -v
```
Attendu : SUCCÈS.

- [ ] **Étape 5 : commit**

```bash
git add backend/app/ingestion/ps3838/anchor.py backend/tests/ingestion/ps3838/test_ps3838_anchor.py
git commit -m "feat(ps3838): ancrage fixture <-> event_id (equipes + date, jamais devine)"
```

---

### Task 4 : Scraper PS3838 branché sur le tick existant

**Files:**
- Create: `backend/app/ingestion/ps3838/scraper.py`
- Modify: `backend/app/ingestion/odds_scheduler.py` (méthode `tick`)
- Test: `backend/tests/ingestion/ps3838/test_ps3838_scraper.py`

**Interfaces:**
- Consumes: `fetch_events()` et `Ps3838Event` (Task 1), `Fixture.ps3838_event_id` (Task 2).
- Produces: `async scrape_ps3838(session, fixture_ids: list[int] | None = None) -> list[MatchScrapeResult]` — un `MatchScrapeResult` par fixture ancrée disposant de cotes, `bookmaker="ps3838"`.

**Contexte.** `MatchScrapeResult` est le contrat commun à tous les scrapers (`app/ingestion/scrape_result.py`) ; `store_match_scrape_result` sait déjà l'écrire dans `match_odds_snapshots`. **Aucune cadence n'est créée** : le scraper est appelé depuis `OddsScheduler.tick`, aux côtés de Betclic, Unibet et PMU, et hérite des intervalles adaptatifs existants.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/ingestion/ps3838/test_ps3838_scraper.py` :

```python
from datetime import UTC, datetime
from types import SimpleNamespace

from app.ingestion.ps3838.client import Ps3838Event
from app.ingestion.ps3838.scraper import build_results

KO = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)


def _fx(fid, eid, home="Atlético Madrid", away="Málaga CF"):
    return SimpleNamespace(
        id=fid, ps3838_event_id=eid, home_team=home, away_team=away,
        kickoff_utc=KO, league="la_liga",
    )


def _ev(eid, h2h=None, totals=None):
    return Ps3838Event(
        eid, "Atletico Madrid", "Malaga", KO, "Spain - La Liga",
        h2h if h2h is not None else {"home": 1.36, "draw": 5.26, "away": 8.99},
        totals if totals is not None else {"over_3.0": 1.85, "under_3.0": 2.04},
        3.0,
    )


def test_result_is_built_from_anchored_id_only():
    res = build_results([_fx(1, 111)], [_ev(111), _ev(222)])
    assert len(res) == 1
    r = res[0]
    assert r.fixture_id == 1
    assert r.bookmaker == "ps3838"
    assert r.h2h == {"home": 1.36, "draw": 5.26, "away": 8.99}
    assert r.totals == {"over_3.0": 1.85, "under_3.0": 2.04}
    assert r.btts is None


def test_unanchored_fixture_is_skipped():
    assert build_results([_fx(1, None)], [_ev(111)]) == []


def test_event_absent_from_feed_is_skipped_without_error():
    assert build_results([_fx(1, 999)], [_ev(111)]) == []


def test_event_without_h2h_is_skipped():
    assert build_results([_fx(1, 111)], [_ev(111, h2h=None)]) == []


def test_event_without_totals_is_skipped():
    assert build_results([_fx(1, 111)], [_ev(111, totals=None)]) == []


def test_names_never_used_for_matching():
    """L'evenement porte des noms differents : seul l'identifiant compte."""
    ev = _ev(111)
    ev.home, ev.away = "Equipe Inconnue A", "Equipe Inconnue B"
    res = build_results([_fx(1, 111)], [ev])
    assert len(res) == 1 and res[0].fixture_id == 1
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/ingestion/ps3838/test_ps3838_scraper.py -q
```
Attendu : ÉCHEC (`ModuleNotFoundError: app.ingestion.ps3838.scraper`).

- [ ] **Étape 3 : implémenter le scraper**

Créer `backend/app/ingestion/ps3838/scraper.py` :

```python
"""Scraper PS3838 -> MatchScrapeResult.

Le rattachement se fait EXCLUSIVEMENT par fixtures.ps3838_event_id. Aucun nom
d'equipe n'intervient ici : c'est ce qui rend le mauvais rattachement impossible.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.ps3838.client import Ps3838Event, fetch_events
from app.ingestion.scrape_result import MatchScrapeResult

logger = logging.getLogger(__name__)

BOOKMAKER = "ps3838"


def build_results(fixtures, events: list[Ps3838Event]) -> list[MatchScrapeResult]:
    """Un resultat par fixture ancree dont l'evenement porte 1X2 ET totals."""
    by_id = {ev.event_id: ev for ev in events}
    now = datetime.now(UTC)
    out: list[MatchScrapeResult] = []

    for fx in fixtures:
        eid = getattr(fx, "ps3838_event_id", None)
        if eid is None:
            continue
        ev = by_id.get(eid)
        if ev is None or not ev.h2h or not ev.totals:
            continue
        out.append(
            MatchScrapeResult(
                fixture_id=fx.id,
                home_team=fx.home_team,
                away_team=fx.away_team,
                kickoff_utc=fx.kickoff_utc,
                league=fx.league,
                bookmaker=BOOKMAKER,
                scraped_at=now,
                h2h=dict(ev.h2h),
                totals=dict(ev.totals),
                btts=None,  # PS3838 n'expose pas ce marche dans ce flux
            )
        )
    return out


async def scrape_ps3838(
    session: AsyncSession, fixture_ids: list[int] | None = None
) -> list[MatchScrapeResult]:
    """Lit le flux PS3838 et produit les resultats des fixtures ancrees."""
    from app.models.fixtures import Fixture

    stmt = select(Fixture).where(Fixture.ps3838_event_id.isnot(None))
    if fixture_ids:
        stmt = stmt.where(Fixture.id.in_(fixture_ids))
    fixtures = (await session.execute(stmt)).scalars().all()
    if not fixtures:
        return []

    events = await fetch_events()
    results = build_results(fixtures, events)
    logger.info("PS3838: %d resultats pour %d fixtures ancrees", len(results), len(fixtures))
    return results
```

- [ ] **Étape 4 : brancher sur le tick existant**

Dans `backend/app/ingestion/odds_scheduler.py`, méthode `tick`, ajouter l'import à côté des trois autres scrapers :

```python
        from app.ingestion.ps3838.scraper import scrape_ps3838
```

Puis, juste après le bloc `asyncio.gather` qui rassemble `betclic_results`, `unibet_results` et `pmu_results` et étend `all_results`, ajouter :

```python
        # PS3838 — source unique du xG d'equipe. Rattachement par identifiant,
        # donc restreint aux fixtures effectivement dues sur ce tick.
        try:
            all_results.extend(await scrape_ps3838(session, [f.id for f in due]))
        except Exception as exc:
            logger.error("OddsScheduler: ps3838 scrape failed: %s", exc, exc_info=exc)
```

Si la liste des fixtures dues porte un autre nom que `due` dans cette méthode, utiliser le nom réel : c'est la liste sur laquelle `scrape_interval_seconds` a déjà été évalué. **Ne modifier aucun seuil ni aucun intervalle.**

- [ ] **Étape 5 : lancer les tests**

```bash
cd backend && uv run pytest tests/ingestion/ps3838/ -v && uv run python -c "import app.ingestion.odds_scheduler; print('import OK')" && uv run pytest tests/ -q
```
Attendu : SUCCÈS, `import OK`, aucune régression.

- [ ] **Étape 6 : commit**

```bash
git add backend/app/ingestion/ps3838/scraper.py backend/app/ingestion/odds_scheduler.py backend/tests/ingestion/ps3838/test_ps3838_scraper.py
git commit -m "feat(ps3838): scraper branche sur le tick existant, rattachement par identifiant"
```

---

### Task 5 : xG calculé depuis PS3838 seul, ligne de totals généralisée

**Files:**
- Modify: `backend/app/services/market_xg.py`
- Test: `backend/tests/services/test_totals_line.py`

**Interfaces:**
- Consumes: snapshots `bookmaker='ps3838'` (Task 4).
- Produces :
  - `solve_lambda_t_from_line(p_over: float, line: float) -> float`
  - `XG_BOOKMAKER = "ps3838"` — seule source lue par `MarketXgService`

**Contexte.** Deux changements. D'abord `MarketXgService` ne lit plus que PS3838 : `_preferred_bookmaker` disparaît du chemin xG, avec lui la possibilité qu'un book fautif soit retenu. Ensuite la ligne de totals se généralise — le code cherche `over_2.5` en dur, PS3838 propose la ligne réellement cotée (3.0 sur Marseille–Strasbourg).

**Le cas des lignes entières demande une attention particulière.** Sur une ligne 2.5, « over » gagne si le total est ≥ 3. Sur une ligne 3.0, « over » gagne si le total est ≥ 4, **le total exact de 3 est remboursé**, et « under » gagne si ≤ 2. Le devig à deux issues donne donc P(over sachant qu'il n'y a pas remboursement), et l'équation à résoudre change.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/services/test_totals_line.py` :

```python
import math

import pytest

from app.services.market_xg import (
    _p_poisson_over_2_5,
    solve_lambda_t,
    solve_lambda_t_from_line,
)


def _poisson_cdf(k: int, lam: float) -> float:
    return sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k + 1))


def test_half_integer_line_matches_legacy_solver():
    """Sur 2.5, le nouveau solveur doit redonner exactement l'ancien."""
    for p in (0.35, 0.50, 0.62):
        assert solve_lambda_t_from_line(p, 2.5) == pytest.approx(solve_lambda_t(p), abs=1e-6)


def test_half_integer_line_3_5():
    lam = solve_lambda_t_from_line(0.40, 3.5)
    assert 1 - _poisson_cdf(3, lam) == pytest.approx(0.40, abs=1e-6)


def test_integer_line_excludes_the_push():
    """Ligne 3.0 : over = total >= 4, under = total <= 2, total == 3 rembourse.
    Le devig a deux issues donne P(over | pas de remboursement)."""
    lam = solve_lambda_t_from_line(0.45, 3.0)
    p_hi = 1 - _poisson_cdf(3, lam)
    p_lo = _poisson_cdf(2, lam)
    assert p_hi / (p_hi + p_lo) == pytest.approx(0.45, abs=1e-6)


def test_integer_line_differs_from_naive_half_integer_treatment():
    """Traiter 3.0 comme 2.5 donnerait un lambda sensiblement different."""
    assert solve_lambda_t_from_line(0.45, 3.0) != pytest.approx(
        solve_lambda_t_from_line(0.45, 2.5), abs=0.05
    )


def test_cross_validation_uses_the_actual_line():
    """Un ajustement correct sur une ligne 3.0 ne doit pas etre signale a tort."""
    from app.services.market_xg import cross_validate_line, p_over_model

    lam = solve_lambda_t_from_line(0.45, 3.0)
    lh = lam * 0.6
    la = lam - lh
    p_home = __import__("app.services.market_xg", fromlist=["_poisson_home_win"])._poisson_home_win(lh, la)
    ok, reason = cross_validate_line(lh, la, 0.45, p_home, 3.0)
    assert ok, reason
    # La prediction doit bien etre calculee dans la convention de la ligne 3.0
    assert p_over_model(lam, lh, la, 3.0) == pytest.approx(0.45, abs=1e-6)


def test_unreachable_probability_raises():
    with pytest.raises(ValueError):
        solve_lambda_t_from_line(0.999999, 2.5)


def test_regression_atletico_malaga_real_odds():
    """Les vraies cotes Pinnacle du 19/08 doivent redonner un Atletico tres
    favori — et surtout pas le 1.07 / 1.02 produit par les cotes Betclic
    erronees."""
    from app.services.market_xg import (
        multiplicative_devig,
        solve_lambda_home_from_h2h,
    )

    p_home, _, _ = multiplicative_devig([1.347, 5.35, 9.46])
    p_over = multiplicative_devig([1.854, 2.04])[0]
    lt = solve_lambda_t_from_line(p_over, 3.0)
    lh = solve_lambda_home_from_h2h(lt, p_home)
    la = lt - lh
    assert lh > 1.6, f"lambda domicile trop faible: {lh}"
    assert la < 0.9, f"lambda exterieur trop eleve: {la}"
    assert lh / la > 2.0
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/services/test_totals_line.py -q
```
Attendu : ÉCHEC (`ImportError: cannot import name 'solve_lambda_t_from_line'`).

- [ ] **Étape 3 : implémenter le solveur généralisé**

Dans `backend/app/services/market_xg.py`, juste après `solve_lambda_t` :

```python
def _poisson_cdf(k: int, lam: float) -> float:
    return sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))


def solve_lambda_t_from_line(p_over: float, line: float) -> float:
    """Resout lambda_total depuis P(over) sur une ligne quelconque.

    Ligne demi-entiere (2.5) : over = total >= 3, pas de remboursement.
    Ligne entiere (3.0)      : over = total >= 4, under = total <= 2, et le
                               total exact de 3 est rembourse. Le devig a deux
                               issues donne donc P(over | pas de remboursement).

    Leve ValueError si aucune racine dans [0.1, 10].
    """
    is_integer = abs(line - round(line)) < 1e-9

    if is_integer:
        k = int(round(line))

        def f(lam: float) -> float:
            p_hi = 1.0 - _poisson_cdf(k, lam)
            p_lo = _poisson_cdf(k - 1, lam)
            total = p_hi + p_lo
            if total <= 0:
                return -p_over
            return p_hi / total - p_over
    else:
        k = math.ceil(line)

        def f(lam: float) -> float:
            return (1.0 - _poisson_cdf(k - 1, lam)) - p_over

    return brentq(f, 0.1, 10.0)


def p_over_model(lambda_t: float, lambda_h: float, lambda_a: float, line: float) -> float:
    """P(over) predite par le modele, dans la MEME convention que le solveur.

    Indispensable pour la validation croisee : comparer une prediction calculee
    sur 2.5 a une probabilite de marche issue d'une ligne 3.0 signalerait a tort
    des calculs corrects.
    """
    if abs(line - round(line)) < 1e-9:
        k = int(round(line))
        p_hi = 1.0 - _poisson_cdf(k, lambda_t)
        p_lo = _poisson_cdf(k - 1, lambda_t)
        return p_hi / (p_hi + p_lo) if (p_hi + p_lo) > 0 else 0.0
    return 1.0 - _poisson_cdf(math.ceil(line) - 1, lambda_t)


def cross_validate_line(
    lambda_h: float, lambda_a: float, p_over_true: float, p_home_true: float, line: float
) -> tuple[bool, str | None]:
    """Comme cross_validate_h2h, mais sur la ligne de totals reellement cotee.

    Meme seuil de 8 % d'erreur absolue.
    """
    lt = lambda_h + lambda_a
    pred_over = p_over_model(lt, lambda_h, lambda_a, line)
    pred_home = _poisson_home_win(lambda_h, lambda_a)
    if abs(pred_over - p_over_true) > 0.08:
        return False, f"Over {line:g} ecart {abs(pred_over - p_over_true):.3f} > 0.08"
    if abs(pred_home - p_home_true) > 0.08:
        return False, f"H2H ecart {abs(pred_home - p_home_true):.3f} > 0.08"
    return True, None
```

`cross_validate_h2h` reste en place pour les appelants existants, mais n'est plus
utilisee sur le chemin PS3838 : sa prediction est calculee sur 2.5 en dur.

- [ ] **Étape 4 : restreindre la lecture à PS3838 et utiliser la ligne réelle**

Dans le même fichier, ajouter près des constantes de tête :

```python
# Source unique du xG d'equipe. Les books FR restent utilises pour les cotes
# joueur, jamais pour ce calcul : ils rattachent parfois les cotes au mauvais
# match (cf. spec 2026-08-19-ps3838-market-xg-design.md).
XG_BOOKMAKER = "ps3838"
```

Dans `_try_market_implied`, restreindre les deux requêtes de snapshots :

```python
            .where(MatchOddsSnapshot.bookmaker == XG_BOOKMAKER)
```

— à ajouter aussi bien sur la requête qui cherche `freshest_snapshot_utc` que sur celle qui charge les lignes.

Remplacer ensuite la sélection du bookmaker et la lecture en dur de `over_2.5` :

```python
        # Plus de choix de bookmaker : une seule source est lue.
        totals_outcomes = markets["totals"].get(XG_BOOKMAKER)
        h2h_outcomes = markets["h2h"].get(XG_BOOKMAKER)
        if not totals_outcomes or not h2h_outcomes:
            logger.info(
                "market_xg: pas de cotes %s pour fixture %s", XG_BOOKMAKER, fixture_id
            )
            return None

        over_key = next((k for k in totals_outcomes if k.startswith("over_")), None)
        if over_key is None:
            logger.info("market_xg: pas de ligne totals pour fixture %s", fixture_id)
            return None
        total_line = float(over_key.removeprefix("over_"))
        under_key = f"under_{over_key.removeprefix('over_')}"
        over_odds = totals_outcomes.get(over_key)
        under_odds = totals_outcomes.get(under_key)
        if over_odds is None or under_odds is None:
            logger.info("market_xg: ligne totals incomplete pour fixture %s", fixture_id)
            return None
        p_over, _ = multiplicative_devig([over_odds, under_odds])
        data_source = XG_BOOKMAKER
```

Le bloc BTTS devient inatteignable (PS3838 ne l'expose pas) : forcer le chemin à deux contraintes en remplaçant la branche `if p_btts_yes is not None:` par un appel direct :

```python
        try:
            lambda_t = solve_lambda_t_from_line(p_over, total_line)
            lambda_h = solve_lambda_home_from_h2h(lambda_t, p_home_win)
            lambda_a = lambda_t - lambda_h
            ok, reason = cross_validate_line(
                lambda_h, lambda_a, p_over, p_home_win, total_line
            )
            fit_residual = 0.0 if ok else FIT_RESIDUAL_FLAG_THRESHOLD + 0.01
        except ValueError as exc:
            logger.warning(
                "market_xg: solveur brentq echoue pour fixture %s: %s", fixture_id, exc
            )
            return None
```

Supprimer le calcul de `p_btts_yes` et l'appel à `_fit_lambdas` devenus morts, ainsi que `_preferred_bookmaker` si plus aucun appelant ne subsiste (le vérifier par recherche avant de supprimer).

- [ ] **Étape 5 : lancer les tests**

```bash
cd backend && uv run pytest tests/services/test_totals_line.py -v && uv run pytest tests/ -q
```
Attendu : SUCCÈS. Les tests qui simulaient `MarketXgService.compute` par mock (`test_pricing_integration.py`, `test_pricing_market_xg_integration.py`) continuent de passer : ils patchent le service entier, pas ses entrées. Les tests qui construisaient des snapshots Betclic/Unibet pour vérifier le chemin market-implied doivent être mis à jour pour utiliser `bookmaker="ps3838"` — les identifier par :

```bash
cd backend && grep -rln --include='*.py' "market_implied" tests
```

- [ ] **Étape 6 : commit**

```bash
git add backend/app/services/market_xg.py backend/tests/services/test_totals_line.py backend/tests/
git commit -m "feat(xg): source unique ps3838 + ligne de totals generalisee (push des lignes entieres)"
```

---

### Task 6 : Bibliothèque des xG de référence (ouverture + closing)

**Files:**
- Create: `backend/alembic/versions/052_team_xg_estimates_phase.py`
- Create: `backend/app/services/xg_library.py`
- Modify: `backend/app/models/team_xg.py`
- Modify: `backend/app/worker.py` (deux jobs)
- Test: `backend/tests/services/test_xg_library.py`

**Interfaces:**
- Consumes: `solve_lambda_t_from_line` et `XG_BOOKMAKER` (Task 5).
- Produces :
  - `TeamXgEstimate.phase: Mapped[str]` — `"opening"` | `"closing"`
  - `async capture_opening(session) -> int` — nombre de lignes écrites
  - `async capture_closing(session) -> int`

**Contexte.** `team_xg_estimates` existe déjà, documentée « append-only time series », avec les bonnes colonnes — et **n'a jamais reçu une ligne**. On la branche. La purge efface `match_odds_snapshots` au-delà de 45 jours : passé ce délai le closing n'est plus recalculable, d'où l'archivage. Cette table ne doit **jamais** rejoindre la liste des tables purgées.

Le closing est calculé **après** le coup d'envoi, à partir du dernier snapshot antérieur : pas de course contre la montre, et l'opération est rejouable.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/services/test_xg_library.py` :

```python
import importlib.util
from pathlib import Path

from app.models.team_xg import TeamXgEstimate


def test_phase_column_exists():
    col = TeamXgEstimate.__table__.columns.get("phase")
    assert col is not None, "colonne phase absente du modele"
    assert col.nullable is False


def test_unique_constraint_on_fixture_and_phase():
    names = {c.name for c in TeamXgEstimate.__table__.constraints}
    cols = {
        tuple(sorted(c.columns.keys()))
        for c in TeamXgEstimate.__table__.constraints
        if hasattr(c, "columns")
    }
    assert ("fixture_id", "phase") in cols, f"contrainte absente, presentes: {cols} {names}"


def test_migration_052_follows_051():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "052_team_xg_estimates_phase.py"
    )
    spec = importlib.util.spec_from_file_location("m052", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "052"
    assert module.down_revision == "051"


def test_library_is_never_purged():
    """job_purge_old_snapshots ne doit toucher que les deux tables de snapshots."""
    src = (Path(__file__).resolve().parents[2] / "app" / "worker.py").read_text()
    start = src.index("async def job_purge_old_snapshots")
    body = src[start:start + 2500]
    assert "team_xg_estimates" not in body
    assert "match_odds_snapshots" in body
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/services/test_xg_library.py -q
```
Attendu : ÉCHEC (colonne `phase` absente).

- [ ] **Étape 3 : écrire la migration**

Créer `backend/alembic/versions/052_team_xg_estimates_phase.py` :

```python
"""Add team_xg_estimates.phase (bibliotheque ouverture / closing).

Revision ID: 052
Revises: 051
Create Date: 2026-08-19
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # La table est vide depuis sa creation : server_default sans backfill.
    op.add_column(
        "team_xg_estimates",
        sa.Column("phase", sa.String(10), nullable=False, server_default="closing"),
    )
    op.create_unique_constraint(
        "uq_team_xg_estimates_fixture_phase", "team_xg_estimates", ["fixture_id", "phase"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_team_xg_estimates_fixture_phase", "team_xg_estimates", type_="unique"
    )
    op.drop_column("team_xg_estimates", "phase")
```

- [ ] **Étape 4 : ajouter le champ et la contrainte au modèle**

Dans `backend/app/models/team_xg.py`, ajouter le champ après `fixture_id` :

```python
    # "opening" = premiere estimation publiee, "closing" = derniere avant le
    # coup d'envoi. Une ligne de chaque par match, archivee definitivement.
    phase: Mapped[str] = mapped_column(String(10), nullable=False, server_default="closing")
```

et la contrainte en fin de classe :

```python
    __table_args__ = (
        UniqueConstraint("fixture_id", "phase", name="uq_team_xg_estimates_fixture_phase"),
    )
```

Compléter l'import SQLAlchemy en tête : `UniqueConstraint` s'ajoute à la liste existante.

- [ ] **Étape 5 : implémenter la capture**

Créer `backend/app/services/xg_library.py` :

```python
"""Bibliotheque des xG de reference : ouverture et closing.

Le closing est l'estimation la plus affutee que le marche produise : c'est la
reference contre laquelle un modele se juge. L'ouverture, comparee au closing,
mesure de combien le marche a bouge.

La purge efface match_odds_snapshots au-dela de 45 jours : passe ce delai, plus
aucun moyen de recalculer un closing. D'ou l'archivage definitif ici.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.market_xg import (
    XG_BOOKMAKER,
    cross_validate_line,
    multiplicative_devig,
    solve_lambda_home_from_h2h,
    solve_lambda_t_from_line,
)

logger = logging.getLogger(__name__)


async def _snapshot_group(session: AsyncSession, fixture_id: int, snapshot_utc):
    """Cotes PS3838 d'un instant precis, groupees par marche."""
    from app.models.match_odds import MatchOddsSnapshot

    rows = (await session.execute(
        select(MatchOddsSnapshot).where(
            MatchOddsSnapshot.fixture_id == fixture_id,
            MatchOddsSnapshot.bookmaker == XG_BOOKMAKER,
            MatchOddsSnapshot.snapshot_utc == snapshot_utc,
        )
    )).scalars().all()

    markets: dict[str, dict[str, float]] = {}
    ids: list[int] = []
    for r in rows:
        markets.setdefault(r.market_type, {})[r.outcome] = r.odds
        ids.append(r.id)
    return markets, ids


def _solve(markets: dict) -> tuple[float, float, float] | None:
    """(lambda_dom, lambda_ext, residu) ou None si les cotes sont inexploitables."""
    h2h, totals = markets.get("h2h"), markets.get("totals")
    if not h2h or not totals:
        return None
    if not all(k in h2h for k in ("home", "draw", "away")):
        return None

    over_key = next((k for k in totals if k.startswith("over_")), None)
    if over_key is None:
        return None
    suffix = over_key.removeprefix("over_")
    under_key = f"under_{suffix}"
    if under_key not in totals:
        return None

    try:
        p_home, _, _ = multiplicative_devig([h2h["home"], h2h["draw"], h2h["away"]])
        p_over, _ = multiplicative_devig([totals[over_key], totals[under_key]])
        lt = solve_lambda_t_from_line(p_over, float(suffix))
        lh = solve_lambda_home_from_h2h(lt, p_home)
        la = lt - lh
        ok, _ = cross_validate_line(lh, la, p_over, p_home, float(suffix))
        return max(0.05, lh), max(0.05, la), 0.0 if ok else 0.07
    except (ValueError, ZeroDivisionError) as exc:
        logger.warning("xg_library: solveur echoue: %s", exc)
        return None


async def _archive(session: AsyncSession, fixture_id: int, phase: str, snapshot_utc) -> bool:
    """Ecrit une ligne. Idempotent : un doublon (fixture, phase) est ignore."""
    from app.models.team_xg import TeamXgEstimate

    markets, ids = await _snapshot_group(session, fixture_id, snapshot_utc)
    solved = _solve(markets)
    if solved is None:
        return False
    lh, la, residual = solved

    stmt = (
        pg_insert(TeamXgEstimate)
        .values(
            fixture_id=fixture_id,
            phase=phase,
            as_of_utc=snapshot_utc,
            lambda_home=round(lh, 4),
            lambda_away=round(la, 4),
            fit_residual=residual,
            flagged=residual > 0.06,
            data_source=XG_BOOKMAKER,
            fallback_used=False,
            input_snapshot_ids=ids,
        )
        .on_conflict_do_nothing(constraint="uq_team_xg_estimates_fixture_phase")
    )
    res = await session.execute(stmt)
    return bool(res.rowcount)


async def capture_opening(session: AsyncSession) -> int:
    """Premiere estimation publiee, pour toute fixture qui n'en a pas encore."""
    from app.models.match_odds import MatchOddsSnapshot
    from app.models.team_xg import TeamXgEstimate

    done = set((await session.execute(
        select(TeamXgEstimate.fixture_id).where(TeamXgEstimate.phase == "opening")
    )).scalars().all())

    rows = (await session.execute(
        select(
            MatchOddsSnapshot.fixture_id,
            func.min(MatchOddsSnapshot.snapshot_utc).label("first_utc"),
        )
        .where(MatchOddsSnapshot.bookmaker == XG_BOOKMAKER)
        .group_by(MatchOddsSnapshot.fixture_id)
    )).all()

    written = 0
    for fixture_id, first_utc in rows:
        if fixture_id in done:
            continue
        if await _archive(session, fixture_id, "opening", first_utc):
            written += 1
    await session.commit()
    logger.info("xg_library: %d ouverture(s) archivee(s)", written)
    return written


async def capture_closing(session: AsyncSession) -> int:
    """Derniere estimation avant le coup d'envoi, pour les matchs commences."""
    from app.models.fixtures import Fixture
    from app.models.match_odds import MatchOddsSnapshot
    from app.models.team_xg import TeamXgEstimate

    now = datetime.now(UTC)
    done = set((await session.execute(
        select(TeamXgEstimate.fixture_id).where(TeamXgEstimate.phase == "closing")
    )).scalars().all())

    fixtures = (await session.execute(
        select(Fixture.id, Fixture.kickoff_utc).where(
            Fixture.kickoff_utc.isnot(None),
            Fixture.kickoff_utc < now,
            Fixture.ps3838_event_id.isnot(None),
        )
    )).all()

    written = 0
    for fixture_id, kickoff in fixtures:
        if fixture_id in done:
            continue
        last_utc = (await session.execute(
            select(func.max(MatchOddsSnapshot.snapshot_utc)).where(
                MatchOddsSnapshot.fixture_id == fixture_id,
                MatchOddsSnapshot.bookmaker == XG_BOOKMAKER,
                MatchOddsSnapshot.snapshot_utc < kickoff,
            )
        )).scalar_one_or_none()
        if last_utc is None:
            continue
        if await _archive(session, fixture_id, "closing", last_utc):
            written += 1
    await session.commit()
    logger.info("xg_library: %d closing(s) archive(s)", written)
    return written
```

Ajouter `from sqlalchemy import func, select` en tête du fichier (les deux sont utilisés).

- [ ] **Étape 6 : enregistrer les deux jobs**

Dans `backend/app/worker.py`, ajouter les deux fonctions près des autres jobs :

```python
async def job_capture_xg_opening() -> None:
    """Archive la premiere estimation xG publiee pour chaque match."""
    try:
        from app.services.xg_library import capture_opening

        async with async_session() as session:
            await capture_opening(session)
    except Exception as exc:
        logger.exception("job_capture_xg_opening failed: %s", exc)


async def job_capture_xg_closing() -> None:
    """Archive la derniere estimation xG avant le coup d'envoi."""
    try:
        from app.services.xg_library import capture_closing

        async with async_session() as session:
            await capture_closing(session)
    except Exception as exc:
        logger.exception("job_capture_xg_closing failed: %s", exc)
```

Puis les enregistrer dans `create_scheduler` :

```python
    scheduler.add_job(
        job_capture_xg_opening,
        IntervalTrigger(hours=1),
        id="capture_xg_opening",
        name="Archive l'ouverture xG des matchs nouvellement cotes",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        job_capture_xg_closing,
        IntervalTrigger(minutes=30),
        id="capture_xg_closing",
        name="Archive le closing xG des matchs commences",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Étape 7 : lancer les tests**

```bash
cd backend && uv run pytest tests/services/test_xg_library.py -v && uv run python -c "
import app.worker as w
ids = sorted(j.id for j in w.create_scheduler().get_jobs())
assert 'capture_xg_opening' in ids and 'capture_xg_closing' in ids, ids
print('jobs enregistres OK')
" && uv run pytest tests/ -q
```
Attendu : SUCCÈS, `jobs enregistres OK`, aucune régression.

- [ ] **Étape 8 : commit**

```bash
git add backend/alembic/versions/052_team_xg_estimates_phase.py backend/app/models/team_xg.py backend/app/services/xg_library.py backend/app/worker.py backend/tests/services/test_xg_library.py
git commit -m "feat(xg): bibliotheque ouverture + closing archivee definitivement"
```

---

### Task 7 : Alerte sur match non ancré à moins de 7 jours

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_ps3838_anchor_alert.py`

**Interfaces:**
- Consumes: `resolve_anchors` (Task 3), `send_alert(message, channel)`.
- Produces: `_unanchored_alert_lines(rows: list[tuple[str, datetime]], now: datetime) -> list[str]`

**Contexte.** PS3838 ouvre ses lignes 10 jours à l'avance. Un match à moins de 7 jours sans ancrage n'est donc pas un cas limite : c'est un bug, et un match sans ancrage est un match **sans aucune recommandation** puisque `load_match_pricing` renvoie `None` faute de xG. Au-delà de 7 jours, l'absence est normale et silencieuse.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `backend/tests/test_ps3838_anchor_alert.py` :

```python
from datetime import UTC, datetime, timedelta

from app.worker import _unanchored_alert_lines

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def test_fixture_within_seven_days_is_reported():
    rows = [("Atlético Madrid - Málaga CF", NOW + timedelta(days=3))]
    lines = _unanchored_alert_lines(rows, NOW)
    assert len(lines) == 1
    assert "Atlético Madrid" in lines[0]


def test_fixture_beyond_seven_days_is_silent():
    rows = [("Lille - Paris Saint-Germain", NOW + timedelta(days=9))]
    assert _unanchored_alert_lines(rows, NOW) == []


def test_boundary_at_seven_days():
    assert _unanchored_alert_lines([("A - B", NOW + timedelta(days=7))], NOW) == []
    assert len(_unanchored_alert_lines([("A - B", NOW + timedelta(days=6, hours=23))], NOW)) == 1


def test_past_fixture_is_ignored():
    assert _unanchored_alert_lines([("A - B", NOW - timedelta(hours=1))], NOW) == []


def test_empty_input_is_silent():
    assert _unanchored_alert_lines([], NOW) == []
```

- [ ] **Étape 2 : lancer les tests, vérifier qu'ils échouent**

```bash
cd backend && uv run pytest tests/test_ps3838_anchor_alert.py -q
```
Attendu : ÉCHEC (`ImportError: cannot import name '_unanchored_alert_lines'`).

- [ ] **Étape 3 : implémenter le job de résolution et l'alerte**

Dans `backend/app/worker.py` :

```python
# PS3838 ouvre ses lignes ~10 jours a l'avance : un match non ancre a moins de
# 7 jours est un bug, pas un cas limite. Un match non ancre est un match sans
# aucune recommandation (load_match_pricing renvoie None faute de xG).
_ANCHOR_ALERT_HORIZON = timedelta(days=7)


def _unanchored_alert_lines(rows, now: datetime) -> list[str]:
    """Libelles des matchs non ancres a moins de 7 jours. Vide = rien a signaler."""
    out = []
    for label, kickoff in rows:
        if kickoff is None:
            continue
        ko = kickoff if kickoff.tzinfo else kickoff.replace(tzinfo=UTC)
        delta = ko - now
        if timedelta(0) < delta < _ANCHOR_ALERT_HORIZON:
            out.append(f"• {label} ({ko:%d/%m %H:%M} UTC)")
    return out


async def job_resolve_ps3838_anchors() -> None:
    """Resout les ancrages PS3838 et signale les matchs proches restes orphelins."""
    try:
        from app.alerts import send_alert
        from app.ingestion.ps3838.anchor import resolve_anchors
        from app.ingestion.ps3838.client import fetch_events
        from app.models.fixtures import Fixture

        events = await fetch_events()
        if not events:
            await send_alert(
                "🚨 <b>[Ev0] PS3838 injoignable</b>\n\n"
                "Aucun événement récupéré — le xG d'équipe ne sera plus calculé.",
                channel="incidents",
            )
            return

        async with async_session() as session:
            resolved, _ = await resolve_anchors(session, events)

            now = datetime.now(UTC)
            rows = (await session.execute(
                select(Fixture.home_team, Fixture.away_team, Fixture.kickoff_utc).where(
                    Fixture.ps3838_event_id.is_(None),
                    Fixture.kickoff_utc > now,
                    Fixture.kickoff_utc < now + _ANCHOR_ALERT_HORIZON,
                    Fixture.status.notin_(["finished", "cancelled", "postponed"]),
                ).order_by(Fixture.kickoff_utc)
            )).all()

        lines = _unanchored_alert_lines(
            [(f"{h} - {a}", ko) for h, a, ko in rows], now
        )
        logger.info("PS3838 anchors: %d resolus, %d orphelins proches", resolved, len(lines))

        if lines:
            await send_alert(
                f"🚨 <b>[Ev0] {len(lines)} match(s) sans ancrage PS3838 à moins de 7j</b>\n\n"
                + "\n".join(lines[:10])
                + ("\n…" if len(lines) > 10 else "")
                + "\n\nCes matchs n'auront aucune recommandation.",
                channel="incidents",
            )
    except Exception as exc:
        logger.exception("job_resolve_ps3838_anchors failed: %s", exc)
```

Enregistrer le job dans `create_scheduler` :

```python
    scheduler.add_job(
        job_resolve_ps3838_anchors,
        CronTrigger(hour=6, minute=0),
        id="resolve_ps3838_anchors",
        name="Résout les ancrages PS3838 et signale les matchs orphelins",
        replace_existing=True,
        max_instances=1,
    )
```

- [ ] **Étape 4 : lancer les tests**

```bash
cd backend && uv run pytest tests/test_ps3838_anchor_alert.py -v && uv run python -c "
import app.worker as w
ids = sorted(j.id for j in w.create_scheduler().get_jobs())
assert 'resolve_ps3838_anchors' in ids, ids
print('job enregistre OK')
" && uv run pytest tests/ -q
```
Attendu : SUCCÈS.

- [ ] **Étape 5 : commit**

```bash
git add backend/app/worker.py backend/tests/test_ps3838_anchor_alert.py
git commit -m "feat(ps3838): resolution planifiee des ancrages + alerte a moins de 7 jours"
```

---

### Task 8 : Mise en service

**Files:**
- Modify: `docs/DEPLOYMENT.md`
- Vérification manuelle en production

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien de programmatique.

- [ ] **Étape 1 : documenter la source**

Dans `docs/DEPLOYMENT.md`, ajouter avant la section « Alerting » :

```markdown
## xG d'équipe — source PS3838

Le xG d'équipe qui alimente le pricing buteur/passeur provient **exclusivement**
de PS3838 (déclinaison de la plateforme Pinnacle, mêmes identifiants
d'événements, joignable depuis le VPS là où `guest.api.arcadia.pinnacle.com`
répond 403).

Les cotes sont rattachées aux matchs par `fixtures.ps3838_event_id`, résolu une
seule fois en vérifiant les équipes **et** la date. Aucun rapprochement par nom
n'intervient au moment du scraping.

Betclic, Unibet et PMU restent utilisés pour les **cotes buteur et passeur**
uniquement — jamais pour le calcul du xG : ils rattachent parfois les cotes au
mauvais match.

Vérifier qu'un match est ancré et pricé :

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT home_team, away_team, ps3838_event_id FROM fixtures
WHERE kickoff_utc > now() AND kickoff_utc < now() + interval '7 days'
  AND ps3838_event_id IS NULL;"
```

Toute ligne renvoyée est une anomalie : PS3838 ouvre ses lignes ~10 jours à
l'avance. Ces matchs n'auront aucune recommandation, et une alerte part sur le
canal `incidents`.

La bibliothèque `team_xg_estimates` archive l'ouverture et le closing de chaque
match. **Elle ne doit jamais être ajoutée à `job_purge_old_snapshots`** : les
cotes brutes disparaissent à 45 jours, ces valeurs sont la seule trace durable.
```

- [ ] **Étape 2 : commit**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: source PS3838 pour le xG d'equipe + bibliotheque de reference"
```

- [ ] **Étape 3 : déployer**

```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
git fetch origin --quiet && git reset --hard origin/main --quiet
docker compose -p ev0-compose-z5hvqt --env-file .env build backend worker
docker compose -p ev0-compose-z5hvqt --env-file .env run --rm --no-deps backend alembic upgrade head
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --no-deps --remove-orphans backend worker
docker exec ev0-compose-z5hvqt-backend-1 alembic current
```

Attendu : `052 (head)`.

- [ ] **Étape 4 : amorcer les ancrages**

```bash
docker exec -e PYTHONPATH=/app -w /app ev0-compose-z5hvqt-worker-1 python -c "
import asyncio
from app.worker import job_resolve_ps3838_anchors
asyncio.run(job_resolve_ps3838_anchors())
"
```

- [ ] **Étape 5 : vérifier la couverture**

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT
  count(*) FILTER (WHERE ps3838_event_id IS NOT NULL) AS ancres,
  count(*)                                            AS total
FROM fixtures
WHERE kickoff_utc > now() AND kickoff_utc < now() + interval '7 days';"
```

Attendu : au moins 90 % d'ancrés. En dessous, inspecter les orphelins avant de
poursuivre — la normalisation des noms est probablement en cause.

- [ ] **Étape 6 : vérifier le xG sur un match connu**

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT f.home_team, f.away_team, r.player_name,
       round(r.lambda_intensity::numeric,3) AS lambda, r.xg_source
FROM recommendations r JOIN fixtures f ON f.id = r.fixture_id
WHERE r.market_type = 'h2h' AND f.kickoff_utc > now()
ORDER BY f.kickoff_utc LIMIT 10;"
```

Attendu : des λ nettement asymétriques sur les matchs déséquilibrés (favori
au-dessus de 1.6, outsider en dessous de 0.9), et `xg_source = market_implied`.
Deux λ voisins sur un match déséquilibré signalent un ancrage erroné.

- [ ] **Étape 7 : vérifier la bibliothèque après le premier match**

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -d ev0 -c "
SELECT phase, count(*), min(as_of_utc), max(as_of_utc)
FROM team_xg_estimates GROUP BY phase;"
```

Attendu : des lignes `opening` dès le premier passage horaire, des lignes
`closing` après le premier match terminé.

---

## Auto-revue

**Couverture de la spec.**

| Exigence de la spec | Tâche |
|---|---|
| Client PS3838, catégorie football, deux appels fusionnés | 1 |
| Ordre 1X2 `[extérieur, domicile, nul]` verrouillé par un test | 1 |
| Ligne de totals principale, lignes quart ignorées | 1 |
| `fixtures.ps3838_event_id` + index unique | 2 |
| Ancrage vérifié sur équipes **et** date ±2 h, non-résolus retournés | 3 |
| Cotes stockées sous `bookmaker='ps3838'` via le contrat existant | 4 |
| Rattachement par identifiant, jamais par nom au scraping | 4 |
| Cadences existantes conservées, aucun seuil modifié | 4 (Global Constraints) |
| `MarketXgService` ne lit que `ps3838` | 5 |
| Chemin à deux contraintes (pas de BTTS) | 5 |
| Ligne de totals généralisée, push des lignes entières traité | 5 |
| Bibliothèque ouverture + closing, idempotente | 6 |
| `team_xg_estimates` jamais purgée | 6 (test dédié) |
| Alerte `incidents` à moins de 7 jours | 7 |
| Books FR intacts pour buteur/passeur | aucun changement — vérifié par la non-régression en 4, 5 |
| Documentation et mise en service | 8 |

**Cohérence des noms.** `Ps3838Event` porte les mêmes champs de la tâche 1 à la
tâche 4. `XG_BOOKMAKER` (tâche 5) est réutilisé tel quel en tâche 6.
`solve_lambda_t_from_line(p_over, line)` garde cet ordre d'arguments partout.
`capture_opening` / `capture_closing` prennent une session et renvoient un `int`.
`_unanchored_alert_lines(rows, now)` est appelée avec cet ordre en tâche 7.

**Points de vigilance pour l'implémenteur.**

- L'ordre du 1X2 est le piège numéro un : `[0]` est l'**extérieur**. Le test
  `test_h2h_order_is_away_home_draw` existe pour ça, ne pas l'affaiblir.
- Sur une ligne de totals **entière**, le total exact est remboursé : l'équation
  n'est pas la même que sur une ligne demi-entière. La tâche 5 traite les deux.
- `cross_validate_h2h` calcule sa prédiction sur 2.5 **en dur** : l'utiliser avec
  une probabilité issue d'une ligne 3.0 signalerait à tort des calculs corrects.
  Le chemin PS3838 utilise `cross_validate_line`, qui prend la ligne en argument.
- En tâche 4, le nom de la variable listant les fixtures dues dans
  `OddsScheduler.tick` doit être vérifié dans le code avant d'écrire l'appel.
- En tâche 5, supprimer `_preferred_bookmaker` **seulement** après avoir vérifié
  par recherche qu'aucun appelant ne subsiste hors du chemin xG.
- La bibliothèque démarre vide : aucune reconstitution du passé n'est possible ni
  souhaitable.
