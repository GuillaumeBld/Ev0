# Données joueurs — ingestion par match, historique et compos — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire entrer les statistiques joueur par match plutôt que par joueur, remonter cinq saisons d'historique sur six compétitions, réparer le sélecteur d'effectif du calculateur et capter les compos avant le coup d'envoi.

**Architecture :** L'ingestion bascule de `GET /api/player-stats/?player=<id>` (une requête par joueur, jamais terminée) vers `GET /api/player-stats/?event=<id>` (une requête par match, 44 joueurs, sans pagination). La même fonction d'ingestion sert au job périodique et au backfill historique. Le sélecteur d'effectif abandonne la recherche par nom de club — corrompue — pour une résolution par identifiant. La fraîcheur des compos se règle sur `job_sync_bzzoiro_events`, seul goulet réel.

**Tech Stack :** Python 3.13, FastAPI, SQLAlchemy 2 async, PostgreSQL, APScheduler, pytest (`asyncio_mode = "auto"`), httpx.

## Global Constraints

- **Ne jamais écrire `@pytest.mark.asyncio`** — `asyncio_mode = "auto"` est actif dans `pyproject.toml`. Les tests `async def` fonctionnent sans décorateur.
- **Ne jamais résoudre une équipe par son nom stocké.** `bzz_teams.name` et `bzz_players.current_team_name` relèvent d'un espace d'identifiants différent de `bzz_players.current_team_api_id` et sont faux pour de nombreux clubs (les joueurs du Barça portent `current_team_name = "Saint George"`). Seuls les identifiants numériques font foi.
- **`canonical_teams.bzz_team_id` et `bzz_players.current_team_api_id` partagent le même espace.** C'est le seul chemin de résolution autorisé.
- **`player.id` de la réponse Bzzoiro correspond à `bzz_players.api_id`**, jamais à `internal_id` ni à `bzz_players.id`. `bzz_players.api_id` porte la contrainte unique `uq_bzz_players_api_id`.
- **`bzz_player_match_stats.player_api_id` est une clé étrangère vers `bzz_players.api_id`** avec `ON DELETE CASCADE`. Insérer une statistique pour un joueur absent de `bzz_players` échoue : le joueur doit être créé d'abord.
- **Compétitions du périmètre :** identifiants internes `[1, 3, 4, 5, 6, 7]`, déjà exposés par `TARGET_LEAGUE_INTERNAL_ID_LIST` dans `app/ingestion/bzzoiro/constants.py`. C'est aussi la valeur stockée dans `bzz_events.league_api_id`.
- **Le paramètre `season=` de l'API est inopérant** — `?season=2024-2025` renvoie 408 110 événements remontant à 1930. Seul `league` + `date_from` + `date_to` filtre correctement.
- **La pagination de l'API est figée à 50 lignes** ; `limit=` est ignoré. Un appel `?event=<id>` tient toutefois en une seule page (44 lignes au maximum observé).
- Toute nouvelle liste de compétitions ou de saisons est une constante nommée dans `constants.py`, jamais une valeur littérale dispersée dans le code.

---

## File Structure

| Fichier | Rôle |
|---|---|
| `backend/app/api/lineups.py` | Modifié — `get_team_players` résout par identifiant |
| `backend/app/ingestion/bzzoiro/sync_player_stats.py` | Modifié en profondeur — ingestion par match |
| `backend/app/ingestion/bzzoiro/constants.py` | Modifié — fenêtres de saisons pour le backfill |
| `backend/app/scripts/backfill_player_stats.py` | Créé — commande de rattrapage historique |
| `backend/app/worker.py` | Modifié — bascule des jobs, régime d'approche des compos |
| `backend/tests/api/test_team_players_endpoint.py` | Créé |
| `backend/tests/ingestion/bzzoiro/test_sync_player_stats.py` | Modifié — tests de l'ingestion par match |
| `backend/tests/scripts/test_backfill_player_stats.py` | Créé |
| `backend/tests/test_bzzoiro_worker_jobs.py` | Modifié — régime d'approche |

---

### Task 1 : Sélecteur d'effectif du calculateur

**Files:**
- Modify: `backend/app/api/lineups.py:262-271`
- Test: `backend/tests/api/test_team_players_endpoint.py` (créer)

**Interfaces:**
- Consomme : `_fold` depuis `app.ingestion.ps3838.anchor`, modèles `CanonicalTeam` (`app/models/canonical_teams.py`) et `BzzPlayer` (`app/models/bzzoiro.py`).
- Produit : `resolve_team_bzz_id(team: str, session) -> int | None`, réutilisable par d'autres points d'accès.

Le code actuel à remplacer :

```python
result = await session.execute(
    select(BzzPlayer.name)
    .where(BzzPlayer.current_team_name.ilike(f"%{team}%"))
    .order_by(BzzPlayer.name)
    .limit(100)
)
return [row[0] for row in result]
```

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/api/test_team_players_endpoint.py` :

```python
"""Le selecteur d'effectif resout par identifiant, jamais par nom de club."""
from unittest.mock import AsyncMock, MagicMock

from app.api.lineups import resolve_team_bzz_id


def _session_returning(*values):
    """Session factice dont chaque execute() rend la valeur suivante."""
    session = MagicMock()
    results = []
    for v in values:
        r = MagicMock()
        r.scalar_one_or_none.return_value = v
        results.append(r)
    session.execute = AsyncMock(side_effect=results)
    return session


async def test_resolve_trouve_le_club_par_nom_francais():
    session = _session_returning(2697)
    assert await resolve_team_bzz_id("Inter Milan", session) == 2697


async def test_resolve_ignore_les_accents():
    session = _session_returning(77903)
    assert await resolve_team_bzz_id("Seville", session) == 77903


async def test_resolve_rend_none_si_club_inconnu():
    session = _session_returning(None)
    assert await resolve_team_bzz_id("Club Inexistant", session) is None
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/api/test_team_players_endpoint.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_team_bzz_id'`

- [ ] **Step 3 : Écrire la résolution et réécrire le point d'accès**

Dans `backend/app/api/lineups.py`, ajouter l'import en tête de fichier :

```python
from app.ingestion.ps3838.anchor import _fold
from app.models.canonical_teams import CanonicalTeam
```

Puis remplacer intégralement le corps de `get_team_players` et ajouter la fonction de résolution juste au-dessus :

```python
async def resolve_team_bzz_id(team: str, session: AsyncSession) -> int | None:
    """Resout un nom d'equipe vers son identifiant Bzzoiro.

    Passe exclusivement par canonical_teams : bzz_teams.name et
    bzz_players.current_team_name relevent d'un autre espace d'identifiants
    et sont faux pour de nombreux clubs.
    """
    cible = _fold(team)
    result = await session.execute(
        select(CanonicalTeam.bzz_team_id).where(
            func.lower(func.unaccent(CanonicalTeam.name_fr)) == cible,
            CanonicalTeam.bzz_team_id.is_not(None),
        )
    )
    trouve = result.scalar_one_or_none()
    if trouve is not None:
        return trouve

    result = await session.execute(
        select(CanonicalTeam.bzz_team_id).where(
            func.lower(func.unaccent(CanonicalTeam.name_en)) == cible,
            CanonicalTeam.bzz_team_id.is_not(None),
        )
    )
    return result.scalar_one_or_none()


@router.get("/lineups/team-players/{team}", response_model=list[str])
async def get_team_players(team: str, session: AsyncSession = Depends(get_db)):
    """Retourne l'effectif du club, resolu par identifiant.

    Une equipe absente de canonical_teams rend une liste vide : un effectif
    vide se corrige en completant la table, alors qu'un effectif emprunte a
    un autre club contaminerait silencieusement les compos.
    """
    bzz_id = await resolve_team_bzz_id(team, session)
    if bzz_id is None:
        logger.warning("team-players : equipe non resolue dans canonical_teams : %s", team)
        return []

    result = await session.execute(
        select(BzzPlayer.name)
        .where(BzzPlayer.current_team_api_id == bzz_id)
        .order_by(BzzPlayer.name)
    )
    return [row[0] for row in result]
```

Vérifier que `func` est importé depuis `sqlalchemy` en tête de fichier ; l'ajouter à l'import existant si absent. Vérifier de même qu'un `logger = logging.getLogger(__name__)` existe dans le module ; l'ajouter sinon.

`_fold` replie déjà les accents et met en minuscules ; `func.unaccent` fait le travail symétrique côté PostgreSQL. L'extension `unaccent` est déjà utilisée en production (elle sert aux requêtes d'audit de `canonical_teams`).

- [ ] **Step 4 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/api/test_team_players_endpoint.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5 : Vérifier qu'aucun appelant ne casse**

Run: `cd backend && uv run pytest tests/api/ tests/test_lineup_resolver.py -q`
Expected: PASS

- [ ] **Step 6 : Commit**

```bash
git add backend/app/api/lineups.py backend/tests/api/test_team_players_endpoint.py
git commit -m "fix(calculateur): resoudre l'effectif par identifiant et non par nom de club"
```

---

### Task 2 : Ingestion des statistiques par match

**Files:**
- Modify: `backend/app/ingestion/bzzoiro/sync_player_stats.py`
- Test: `backend/tests/ingestion/bzzoiro/test_sync_player_stats.py`

**Interfaces:**
- Consomme : `compute_derived_metrics(row)` (déjà présent, inchangé), `BzzoiroClient.get_all(path, params)`.
- Produit :
  - `build_stat_values(row, event_api_id, team_api_id, is_home) -> dict[str, Any]`
  - `async sync_player_stats_for_event(session, client, event_api_id, home_team_api_id, away_team_api_id) -> int`
  - `async ensure_player_exists(session, player_obj) -> int` (rend `api_id`)

**Contexte :** le module affirme aujourd'hui en en-tête que l'API ne renvoie jamais l'identité du joueur, ce qui justifiait l'appel par joueur. C'est faux depuis au moins le 21/08/2026 : `?event=<id>` rend 44 lignes portant chacune `player: {id, name, short_name, position, positions_detailed, team}`. Cet en-tête doit être réécrit.

Forme exacte d'une ligne de réponse, relevée sur `?event=223384` :

```json
{
  "event": {"id": 223384, "home_team": "FC Schalke 04", "away_team": "Real Madrid",
            "event_date": "2026-08-16T15:00:00Z", "home_score": 0, "away_score": 3},
  "player": {"id": 27598, "name": "Loris Karius", "short_name": "L. Karius",
             "position": "G", "specific_position": "GK",
             "positions_detailed": ["GK"], "team": "FC Schalke 04"},
  "minutes_played": 90, "goals": 0, "goal_assist": 0, "expected_goals": null,
  "total_shots": 0, "rating": 6.5
}
```

Les clés `team` et `is_home` **n'existent pas** à la racine — c'est pourquoi le code actuel écrit `NULL` dans ces deux colonnes sur 1 133 341 lignes sur 1 135 494.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à la fin de `backend/tests/ingestion/bzzoiro/test_sync_player_stats.py` :

```python
from app.ingestion.bzzoiro.sync_player_stats import (
    build_stat_values,
    sync_player_stats_for_event,
)


def _ligne(player_id: int, club: str, **extra):
    base = {
        "event": {"id": 223384, "home_team": "FC Schalke 04", "away_team": "Real Madrid"},
        "player": {"id": player_id, "name": f"Joueur {player_id}", "team": club},
        "minutes_played": 90, "goals": 1, "goal_assist": 0,
        "expected_goals": 0.4, "expected_assists": 0.1,
        "total_shots": 2, "shots_on_target": 1,
        "total_pass": 30, "accurate_pass": 24,
    }
    base.update(extra)
    return base


def test_build_stat_values_domicile():
    v = build_stat_values(_ligne(27598, "FC Schalke 04"), 223384, 500, True)
    assert v["player_api_id"] == 27598
    assert v["event_api_id"] == 223384
    assert v["team_api_id"] == 500
    assert v["is_home"] is True
    assert v["minutes_played"] == 90
    # les metriques derivees sont bien fusionnees
    assert v["shot_accuracy"] == 0.5


def test_build_stat_values_exterieur():
    v = build_stat_values(_ligne(594, "Real Madrid"), 223384, 600, False)
    assert v["team_api_id"] == 600
    assert v["is_home"] is False


async def test_sync_par_match_traite_les_deux_camps():
    """Chaque joueur est rattache au bon camp d'apres le nom de son club."""
    rows = [_ligne(27598, "FC Schalke 04"), _ligne(594, "Real Madrid")]
    client = MagicMock()
    client.get_all = AsyncMock(return_value=rows)

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    async def _capture(session_, player):
        return player["id"]

    import app.ingestion.bzzoiro.sync_player_stats as mod
    original = mod.ensure_player_exists
    mod.ensure_player_exists = _capture
    try:
        count = await sync_player_stats_for_event(
            session, client, event_api_id=223384,
            home_team_api_id=500, away_team_api_id=600,
        )
    finally:
        mod.ensure_player_exists = original

    assert count == 2
    client.get_all.assert_called_once_with("/api/player-stats/", {"event": 223384})


async def test_sync_par_match_ignore_une_ligne_sans_identite():
    rows = [_ligne(27598, "FC Schalke 04"), {"event": {"id": 223384}, "minutes_played": 12}]
    client = MagicMock()
    client.get_all = AsyncMock(return_value=rows)
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    import app.ingestion.bzzoiro.sync_player_stats as mod
    original = mod.ensure_player_exists

    async def _capture(session_, player):
        return player["id"]

    mod.ensure_player_exists = _capture
    try:
        count = await sync_player_stats_for_event(
            session, client, event_api_id=223384,
            home_team_api_id=500, away_team_api_id=600,
        )
    finally:
        mod.ensure_player_exists = original

    assert count == 1


async def test_sync_par_match_sans_stats_n_ecrit_rien():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    count = await sync_player_stats_for_event(
        session, client, event_api_id=999,
        home_team_api_id=500, away_team_api_id=600,
    )
    assert count == 0
    session.commit.assert_not_called()
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/ingestion/bzzoiro/test_sync_player_stats.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_stat_values'`

- [ ] **Step 3 : Réécrire l'en-tête du module**

Remplacer le docstring de tête de `backend/app/ingestion/bzzoiro/sync_player_stats.py` par :

```python
"""Ingestion des statistiques joueur par match depuis Bzzoiro.

L'endpoint /api/player-stats/ accepte un filtre ``event=<api_id>`` qui rend
en une seule page l'integralite des joueurs des deux equipes (44 lignes
observees), chacune portant l'identite du joueur sous la cle ``player``.

C'est la voie retenue : une requete par match au lieu d'une par joueur.
L'ancienne approche par joueur demandait environ 30 000 requetes pour couvrir
une saison et n'aboutissait jamais, d'ou une couverture bloquee a 16 % des
joueurs.

Correspondance des identifiants : ``player.id`` de la reponse vaut
``bzz_players.api_id``, et non ``internal_id`` ni ``bzz_players.id``.

Les cles ``team`` et ``is_home`` sont absentes de la reponse. Le camp se
deduit en comparant ``player.team`` a ``event.home_team`` — les deux chaines
proviennent de la meme reponse et se comparent donc sans ambiguite.
"""
```

- [ ] **Step 4 : Extraire la construction des valeurs**

Ajouter dans le même module, après `compute_derived_metrics` :

```python
def build_stat_values(
    row: dict[str, Any],
    event_api_id: int,
    team_api_id: int | None,
    is_home: bool | None,
) -> dict[str, Any]:
    """Construit la ligne a inserer dans bzz_player_match_stats."""
    player = row.get("player") or {}
    return {
        "player_api_id": player.get("id"),
        "event_api_id": event_api_id,
        "team_api_id": team_api_id,
        "is_home": is_home,
        "minutes_played": row.get("minutes_played"),
        "rating": row.get("rating"),
        "touches": row.get("touches"),
        "goals": row.get("goals"),
        "goal_assist": row.get("goal_assist"),
        "expected_goals": row.get("expected_goals"),
        "expected_assists": row.get("expected_assists"),
        "total_shots": row.get("total_shots"),
        "shots_on_target": row.get("shots_on_target"),
        "total_pass": row.get("total_pass"),
        "accurate_pass": row.get("accurate_pass"),
        "key_pass": row.get("key_pass"),
        "total_long_balls": row.get("total_long_balls"),
        "accurate_long_balls": row.get("accurate_long_balls"),
        "total_cross": row.get("total_cross"),
        "accurate_cross": row.get("accurate_cross"),
        "duel_won": row.get("duel_won"),
        "duel_lost": row.get("duel_lost"),
        "aerial_won": row.get("aerial_won"),
        "aerial_lost": row.get("aerial_lost"),
        "total_tackle": row.get("total_tackle"),
        "won_tackle": row.get("won_tackle"),
        "total_clearance": row.get("total_clearance"),
        "interception": row.get("interception"),
        "ball_recovery": row.get("ball_recovery"),
        "yellow_card": row.get("yellow_card"),
        "red_card": row.get("red_card"),
        "fouls": row.get("fouls"),
        "was_fouled": row.get("was_fouled"),
        "dispossessed": row.get("dispossessed"),
        "possession_lost": row.get("possession_lost"),
        "saves": row.get("saves"),
        "goals_conceded": row.get("goals_conceded"),
        **compute_derived_metrics(row),
    }
```

- [ ] **Step 5 : Écrire la création de joueur manquant**

Ajouter dans le même module :

```python
async def ensure_player_exists(session: AsyncSession, player: dict[str, Any]) -> int:
    """Cree le joueur s'il est absent de bzz_players. Rend son api_id.

    bzz_player_match_stats.player_api_id est une cle etrangere vers
    bzz_players.api_id : sans cette creation, l'insertion des statistiques
    echoue. C'est ce mecanisme qui comble les joueurs manquants.
    """
    from app.models.bzzoiro import BzzPlayer

    api_id = player["id"]
    stmt = (
        pg_insert(BzzPlayer)
        .values(
            api_id=api_id,
            name=player.get("name") or f"Joueur {api_id}",
            short_name=player.get("short_name"),
            position=player.get("position"),
        )
        .on_conflict_do_nothing(index_elements=["api_id"])
    )
    await session.execute(stmt)
    return api_id
```

Le `on_conflict_do_nothing` garantit qu'un joueur déjà connu n'est jamais écrasé : les données de `bzz_players` viennent du sync dédié, plus riches que ce que porte la réponse de statistiques.

- [ ] **Step 6 : Écrire l'ingestion par match**

Ajouter dans le même module :

```python
async def sync_player_stats_for_event(
    session: AsyncSession,
    client: Any,
    event_api_id: int,
    home_team_api_id: int | None,
    away_team_api_id: int | None,
) -> int:
    """Ingere les statistiques des deux equipes d'un match. Rend le nombre de lignes."""
    from app.models.bzzoiro import BzzPlayerMatchStat

    rows = await client.get_all("/api/player-stats/", {"event": event_api_id})
    if not rows:
        return 0

    count = 0
    for row in rows:
        player = row.get("player") or {}
        if not player.get("id"):
            continue

        event = row.get("event") or {}
        club = player.get("team")
        domicile = event.get("home_team")
        exterieur = event.get("away_team")

        if club is not None and club == domicile:
            is_home, team_api_id = True, home_team_api_id
        elif club is not None and club == exterieur:
            is_home, team_api_id = False, away_team_api_id
        else:
            is_home, team_api_id = None, None

        await ensure_player_exists(session, player)

        values = build_stat_values(row, event_api_id, team_api_id, is_home)
        stmt = pg_insert(BzzPlayerMatchStat).values(**values).on_conflict_do_update(
            index_elements=["player_api_id", "event_api_id"],
            set_={
                k: v for k, v in values.items()
                if k not in ("player_api_id", "event_api_id")
            },
        )
        await session.execute(stmt)
        count += 1

    if count:
        await session.commit()

    return count
```

Un joueur dont le club ne correspond à aucun des deux camps garde `is_home` et `team_api_id` à `None` plutôt que de se voir attribuer un camp au hasard — le reste de ses statistiques est conservé.

- [ ] **Step 7 : Réécrire l'orchestration**

Remplacer `_get_players_for_recent_events`, `_get_players_for_full_season`, `sync_player_stats_for_player` et `sync_player_stats` par une sélection de **matchs** :

```python
async def _get_events_to_sync(
    session: AsyncSession,
    days_back: int | None,
) -> list[tuple[int, int | None, int | None]]:
    """Rend (event_api_id, home_team_api_id, away_team_api_id) des matchs termines.

    days_back=None couvre toute la base, sans restriction de date.
    """
    conditions = [
        BzzEvent.status == "finished",
        BzzEvent.league_api_id.in_(_ALL_LEAGUE_IDS),
    ]
    if days_back is not None:
        conditions.append(BzzEvent.event_date >= datetime.now(UTC) - timedelta(days=days_back))

    result = await session.execute(
        select(BzzEvent.api_id, BzzEvent.home_team_api_id, BzzEvent.away_team_api_id)
        .where(*conditions)
        .order_by(BzzEvent.event_date.desc())
    )
    return [r for r in result.fetchall() if r[0] is not None]


async def sync_player_stats(
    session: AsyncSession,
    client: Any,
    days_back: int = 14,
    full_season: bool = False,
) -> int:
    """Ingere les statistiques joueur, un appel par match.

    Args:
        days_back: profondeur en jours (ignore si full_season=True).
        full_season: si vrai, couvre tous les matchs termines de la base.
    """
    events = await _get_events_to_sync(session, None if full_season else days_back)
    logger.info(
        "Statistiques joueur : %d matchs a traiter (full_season=%s)",
        len(events), full_season,
    )
    if not events:
        return 0

    total = 0
    erreurs = 0
    for i, (event_api_id, home_id, away_id) in enumerate(events):
        try:
            total += await sync_player_stats_for_event(
                session, client, event_api_id, home_id, away_id
            )
        except Exception as exc:
            erreurs += 1
            logger.warning("Echec statistiques match %s : %s", event_api_id, exc)
        if i % 50 == 49:
            logger.info("  Progression : %d/%d matchs, %d lignes", i + 1, len(events), total)

    logger.info(
        "Statistiques joueur : %d lignes sur %d matchs (%d erreurs)",
        total, len(events), erreurs,
    )
    return total
```

Supprimer les imports devenus inutiles : `asyncio`, `or_`, `BzzPlayer`, `BzzTeam`. Conserver `datetime`, `UTC`, `timedelta`, `select`, `pg_insert`, `BzzEvent`.

La temporisation `asyncio.sleep(0.5)` toutes les dix requêtes disparaît : elle servait à ménager un quota qui n'existe plus, et le volume passe de 30 000 à 2 600 requêtes.

- [ ] **Step 8 : Retirer les tests de l'ancienne voie**

Supprimer de `backend/tests/ingestion/bzzoiro/test_sync_player_stats.py` les deux tests portant sur la fonction disparue : `test_sync_player_stats_for_player` et `test_sync_player_stats_for_player_skips_missing_event_api_id`, ainsi que l'import de `sync_player_stats_for_player`. Conserver tous les tests de `compute_derived_metrics`, qui restent valables.

- [ ] **Step 9 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/ingestion/bzzoiro/ -v`
Expected: PASS

- [ ] **Step 10 : Vérifier que rien d'autre n'appelle l'ancienne fonction**

Run: `cd backend && grep -rn "sync_player_stats_for_player" app tests || echo "aucun appelant"`
Expected: `aucun appelant`

- [ ] **Step 11 : Suite complète**

Run: `cd backend && uv run pytest -q`
Expected: PASS

- [ ] **Step 12 : Commit**

```bash
git add backend/app/ingestion/bzzoiro/sync_player_stats.py backend/tests/ingestion/bzzoiro/test_sync_player_stats.py
git commit -m "feat(stats): ingerer les statistiques joueur par match plutot que par joueur"
```

---

### Task 3 : Backfill historique

**Files:**
- Modify: `backend/app/ingestion/bzzoiro/constants.py`
- Create: `backend/app/scripts/backfill_player_stats.py`
- Test: `backend/tests/scripts/test_backfill_player_stats.py` (créer)

**Interfaces:**
- Consomme : `sync_player_stats_for_event` de la tâche 2, `TARGET_LEAGUE_INTERNAL_ID_LIST`.
- Produit : `BACKFILL_SEASONS`, `season_window(season) -> tuple[str, str]`, `async backfill(session, client, seasons=None, leagues=None) -> tuple[int, int]`.

**Forme de la réponse `/api/events/`**, relevée le 21/08/2026 — il n'existe
**pas** de clés `home_team_api_id` / `away_team_api_id` :

```json
{
  "id": 306044,
  "status": "finished",
  "home_team": "Le Havre",
  "away_team": "Paris Saint-Germain",
  "home_team_obj": {"id": 102, "name": "Le Havre", "short_name": "Le Havre"},
  "away_team_obj": {"id": 114, "name": "Paris Saint-Germain", "short_name": "PSG"},
  "league": {"id": 6, "name": "Ligue 1"}
}
```

`home_team_obj.id` relève bien de l'espace de `canonical_teams.bzz_team_id` —
vérifié : le PSG y vaut 114 des deux côtés.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `backend/tests/scripts/test_backfill_player_stats.py` :

```python
"""Le backfill enumere par fenetre de dates, jamais par season=."""
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.bzzoiro.constants import BACKFILL_SEASONS
from app.scripts.backfill_player_stats import backfill, season_window


def test_cinq_saisons_au_perimetre():
    assert BACKFILL_SEASONS == [
        "2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026",
    ]


def test_fenetre_de_saison_juillet_a_juin():
    assert season_window("2024-2025") == ("2024-07-01", "2025-06-30")


async def test_backfill_n_utilise_jamais_le_parametre_season():
    """season= renvoie 408 110 evenements remontant a 1930 : il est inutilisable."""
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    await backfill(session, client, seasons=["2024-2025"], leagues=[6])

    for appel in client.get_all.call_args_list:
        params = appel.args[1] if len(appel.args) > 1 else appel.kwargs.get("params", {})
        assert "season" not in params
        if appel.args[0] == "/api/events/":
            assert params["date_from"] == "2024-07-01"
            assert params["date_to"] == "2025-06-30"
            assert params["league"] == 6


async def test_backfill_saute_les_matchs_deja_ingeres():
    """Une execution interrompue puis relancee ne retraite pas l'existant."""
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[
        {"id": 111, "status": "finished",
         "home_team_obj": {"id": 102}, "away_team_obj": {"id": 114}},
        {"id": 222, "status": "finished",
         "home_team_obj": {"id": 102}, "away_team_obj": {"id": 114}},
    ])
    session = MagicMock()
    deja = MagicMock()
    deja.scalars.return_value.all.return_value = [111]
    session.execute = AsyncMock(return_value=deja)
    session.commit = AsyncMock()

    traites, ignores = await backfill(
        session, client, seasons=["2024-2025"], leagues=[6],
    )
    assert ignores == 1
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/scripts/test_backfill_player_stats.py -v`
Expected: FAIL — `ImportError: cannot import name 'BACKFILL_SEASONS'`

- [ ] **Step 3 : Déclarer les saisons du périmètre**

Ajouter à la fin de `backend/app/ingestion/bzzoiro/constants.py` :

```python
# Profondeur du rattrapage historique — validee le 21/08/2026.
# Une saison se definit par une fenetre de dates : le parametre season= de
# l'API est inoperant (il rend 408 110 evenements remontant a 1930).
BACKFILL_SEASONS: list[str] = [
    "2021-2022",
    "2022-2023",
    "2023-2024",
    "2024-2025",
    "2025-2026",
]
```

- [ ] **Step 4 : Écrire la commande de rattrapage**

Créer `backend/app/scripts/backfill_player_stats.py` :

```python
"""Rattrapage historique des statistiques joueur, par match.

Reutilise sync_player_stats_for_event : aucune logique d'ingestion n'est
dupliquee ici. Ce script ne fait qu'enumerer les matchs a traiter.

Volume mesure le 21/08/2026 sur les six competitions du perimetre :
2 042 matchs pour la saison 2024-2025, soit environ 10 200 appels pour les
cinq saisons.

Reprenable : les matchs deja presents dans bzz_player_match_stats sont
ignores, une execution interrompue redemarre donc sans retraiter l'existant.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import (
    BACKFILL_SEASONS,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats_for_event

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def season_window(season: str) -> tuple[str, str]:
    """Rend (date_from, date_to) pour une saison 'AAAA-AAAA'."""
    debut, fin = season.split("-")
    return f"{debut}-07-01", f"{fin}-06-30"


async def _events_deja_ingeres(session: AsyncSession) -> set[int]:
    from app.models.bzzoiro import BzzPlayerMatchStat

    result = await session.execute(
        select(BzzPlayerMatchStat.event_api_id).distinct()
    )
    return set(result.scalars().all())


async def backfill(
    session: AsyncSession,
    client: Any,
    seasons: list[str] | None = None,
    leagues: list[int] | None = None,
) -> tuple[int, int]:
    """Rattrape les statistiques manquantes. Rend (matchs traites, matchs ignores)."""
    seasons = seasons or BACKFILL_SEASONS
    leagues = leagues or TARGET_LEAGUE_INTERNAL_ID_LIST

    deja = await _events_deja_ingeres(session)
    logger.info("%d matchs deja ingeres, ils seront ignores", len(deja))

    traites = ignores = 0

    for season in seasons:
        date_from, date_to = season_window(season)
        for league in leagues:
            events = await client.get_all(
                "/api/events/",
                {"league": league, "date_from": date_from, "date_to": date_to},
            )
            logger.info(
                "Saison %s / competition %s : %d matchs", season, league, len(events)
            )

            for event in events:
                event_api_id = event.get("id")
                if event_api_id is None or event.get("status") != "finished":
                    continue
                if event_api_id in deja:
                    ignores += 1
                    continue

                domicile = event.get("home_team_obj") or {}
                exterieur = event.get("away_team_obj") or {}

                try:
                    lignes = await sync_player_stats_for_event(
                        session, client, event_api_id,
                        domicile.get("id"),
                        exterieur.get("id"),
                    )
                    if lignes:
                        deja.add(event_api_id)
                    traites += 1
                except Exception as exc:
                    logger.warning("Echec match %s : %s", event_api_id, exc)

            logger.info(
                "  Cumul : %d matchs traites, %d ignores", traites, ignores
            )

    logger.info("Rattrapage termine : %d traites, %d ignores", traites, ignores)
    return traites, ignores


async def _main() -> None:
    from app.config import settings
    from app.db import async_session
    from app.ingestion.bzzoiro.client import BzzoiroClient

    async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
        await backfill(session, client)


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 5 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/scripts/test_backfill_player_stats.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6 : Commit**

```bash
git add backend/app/ingestion/bzzoiro/constants.py backend/app/scripts/backfill_player_stats.py backend/tests/scripts/test_backfill_player_stats.py
git commit -m "feat(stats): rattrapage historique sur cinq saisons, enumere par fenetre de dates"
```

---

### Task 4 : Fraîcheur des compos

**Files:**
- Modify: `backend/app/worker.py:2082-2090` (planification des événements)
- Test: `backend/tests/test_bzzoiro_worker_jobs.py`

**Interfaces:**
- Consomme : `job_sync_bzzoiro_events` (existant, inchangé), modèle `Fixture`.
- Produit : `async has_imminent_kickoff(session, heures=3) -> bool`, `async job_sync_bzzoiro_events_approche() -> None`.

**Contexte :** `sync_bzzoiro_lineups` tourne toutes les 30 minutes mais ne récupère rien — il relit `bzz_events.lineups`, un bloc JSONB alimenté par `job_sync_bzzoiro_events`, planifié **toutes les 6 heures** (`app/worker.py:2083`). Bzzoiro publie les compos environ une heure avant le coup d'envoi : à ce rythme, elles arrivent souvent après le match. `sync_bzzoiro_lineups` n'est pas en cause et garde sa cadence.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à `backend/tests/test_bzzoiro_worker_jobs.py` :

```python
from unittest.mock import AsyncMock, MagicMock

from app.worker import has_imminent_kickoff


def _session_comptant(n: int):
    session = MagicMock()
    r = MagicMock()
    r.scalar.return_value = n
    session.execute = AsyncMock(return_value=r)
    return session


async def test_regime_approche_si_match_dans_moins_de_trois_heures():
    assert await has_imminent_kickoff(_session_comptant(1)) is True


async def test_regime_normal_si_aucun_match_proche():
    assert await has_imminent_kickoff(_session_comptant(0)) is False
```

- [ ] **Step 2 : Lancer les tests et vérifier qu'ils échouent**

Run: `cd backend && uv run pytest tests/test_bzzoiro_worker_jobs.py -k approche -v`
Expected: FAIL — `ImportError: cannot import name 'has_imminent_kickoff'`

- [ ] **Step 3 : Écrire le détecteur et le job d'approche**

Ajouter dans `backend/app/worker.py`, juste avant `job_sync_bzzoiro_lineups` :

```python
async def has_imminent_kickoff(session, heures: int = 3) -> bool:
    """Vrai si au moins un match commence dans les prochaines heures."""
    from sqlalchemy import func as sa_func

    maintenant = datetime.now(UTC)
    result = await session.execute(
        select(sa_func.count())
        .select_from(Fixture)
        .where(
            Fixture.kickoff_utc > maintenant,
            Fixture.kickoff_utc <= maintenant + timedelta(hours=heures),
        )
    )
    return (result.scalar() or 0) > 0


async def job_sync_bzzoiro_events_approche() -> None:
    """Toutes les 10 min : rafraichit les evenements quand un coup d'envoi approche.

    Les compos vivent dans bzz_events.lineups, alimente par le sync des
    evenements. Bzzoiro les publie environ 1 h avant le coup d'envoi : a la
    cadence normale de 6 h, elles arrivent souvent apres le match.
    """
    if not settings.bzzoiro_api_key:
        return
    try:
        async with async_session() as session:
            if not await has_imminent_kickoff(session):
                return
        logger.info("Coup d'envoi imminent — rafraichissement des evenements")
        await job_sync_bzzoiro_events()
    except Exception as exc:
        logger.error("Echec du rafraichissement d'approche : %s", exc, exc_info=True)
```

Vérifier que `Fixture`, `select`, `datetime`, `UTC` et `timedelta` sont importés dans `app/worker.py` ; les ajouter aux imports existants si nécessaire.

- [ ] **Step 4 : Planifier le régime d'approche**

Ajouter dans la fonction de planification, juste après le bloc `sync_bzzoiro_events` existant (`app/worker.py:2083`) — qui reste inchangé à 6 heures :

```python
    # Régime d'approche : toutes les 10 min, actif seulement si un coup d'envoi
    # est dans les 3 heures. C'est ce qui fait arriver les compos avant le match.
    scheduler.add_job(
        job_sync_bzzoiro_events_approche,
        IntervalTrigger(minutes=10),
        id="sync_bzzoiro_events_approche",
        name="Sync Bzzoiro events — regime d'approche",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 5 : Lancer les tests et vérifier qu'ils passent**

Run: `cd backend && uv run pytest tests/test_bzzoiro_worker_jobs.py -v`
Expected: PASS

- [ ] **Step 6 : Suite complète**

Run: `cd backend && uv run pytest -q`
Expected: PASS

- [ ] **Step 7 : Commit**

```bash
git add backend/app/worker.py backend/tests/test_bzzoiro_worker_jobs.py
git commit -m "feat(compos): regime d'approche pour capter les compos avant le coup d'envoi"
```

---

## Après la fusion

Le rattrapage historique ne s'exécute pas tout seul — il se lance à la main une fois le code déployé :

```bash
docker exec ev0-compose-z5hvqt-worker-1 python -m app.scripts.backfill_player_stats
```

Compter environ 10 200 appels. Le script est reprenable : une interruption ne coûte que les matchs en cours.

Le déploiement se fait par Dokploy à la fusion sur `main`. **Ne pas déployer à la main en parallèle** — c'est la cause des conteneurs orphelins qui ont coupé le site le 21/08.
