"""Task 8 (plan complement carriere Transfermarkt) : reconciliation a la
lecture de GET /players/{id}/career entre Bzzoiro (bzz_player_season_stats,
autorite) et Transfermarkt (player_career_seasons, complement).

Regle : une seule entree par (saison, competition). Bzzoiro gagne sur
recouvrement ; Transfermarkt ne comble que les champs NULL de Bzzoiro
(source devient "merged" dans ce cas). Si seulement Transfermarkt -> entree
source="transfermarkt", les champs xG/notes (propres a Bzzoiro) restent
None, jamais devines. Si seulement Bzzoiro -> source="bzzoiro".

3 cas (brief Task 8, step 1) sur un meme joueur :
  1. Saison 2025-2026 Ligue 1 des DEUX cotes (Bzzoiro riche, TM complement)
     -> une entree, valeurs Bzzoiro, source="merged" (le champ `minutes`
     Bzzoiro est NULL ici et comble par TM -> teste le comblement, pas
     seulement le cas pur "bzzoiro").
  2. Saison 14/15 (National 2, France) seulement TM -> source="transfermarkt",
     expected_goals is None (jamais invente).
  3. Compartiment selection : une saison EURO TM `is_national=True`, code
     "EM" non mappe (voir app.ingestion.competition_identity) -> reste une
     entree distincte, jamais fusionnee avec le club.

Total attendu : 3 entrees (pas 4), aucun doublon (saison, competition).
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.players import _build_career_seasons, get_player_career
from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat
from app.models.player_career import PlayerCareerSeason

# ---------------------------------------------------------------------------
# Fixtures communes aux 2 styles de test (unitaire sur _build_career_seasons,
# et integration sur l'endpoint reel avec une vraie base SQLite en memoire).
# ---------------------------------------------------------------------------


def _bzz_row(
    *,
    player_api_id: int = 42,
    league_api_id: int | None = 6,
    season: str = "2025-2026",
    matches_played: int | None = 10,
    minutes_played: int | None = None,
    goals: int | None = 5,
    goal_assist: int | None = 3,
    expected_goals: float | None = 4.2,
    avg_rating: float | None = 7.1,
    starts: int | None = None,
    total_shots: int | None = None,
    shots_on_target: int | None = None,
    key_pass: int | None = None,
    total_pass: int | None = None,
    accurate_pass: int | None = None,
    total_cross: int | None = None,
    accurate_cross: int | None = None,
    total_long_balls: int | None = None,
    accurate_long_balls: int | None = None,
    total_tackle: int | None = None,
    won_tackle: int | None = None,
    duel_won: int | None = None,
    duel_lost: int | None = None,
    interception: int | None = None,
    ball_recovery: int | None = None,
    aerial_won: int | None = None,
    aerial_lost: int | None = None,
    yellow_card: int | None = None,
    red_card: int | None = None,
    expected_assists: float | None = None,
) -> BzzPlayerSeasonStat:
    return BzzPlayerSeasonStat(
        player_api_id=player_api_id,
        league_api_id=league_api_id,
        season=season,
        matches_played=matches_played,
        minutes_played=minutes_played,
        goals=goals,
        goal_assist=goal_assist,
        expected_goals=expected_goals,
        avg_rating=avg_rating,
        starts=starts,
        total_shots=total_shots,
        shots_on_target=shots_on_target,
        key_pass=key_pass,
        total_pass=total_pass,
        accurate_pass=accurate_pass,
        total_cross=total_cross,
        accurate_cross=accurate_cross,
        total_long_balls=total_long_balls,
        accurate_long_balls=accurate_long_balls,
        total_tackle=total_tackle,
        won_tackle=won_tackle,
        duel_won=duel_won,
        duel_lost=duel_lost,
        interception=interception,
        ball_recovery=ball_recovery,
        aerial_won=aerial_won,
        aerial_lost=aerial_lost,
        yellow_card=yellow_card,
        red_card=red_card,
        expected_assists=expected_assists,
    )


def _tm_row(
    *,
    player_api_id: int = 42,
    season: str,
    season_start_year: int | None,
    competition_code: str | None,
    competition: str | None,
    appearances: int,
    goals: int,
    assists: int,
    minutes: int,
    is_national: bool = False,
    starts: int | None = None,
    total_shots: int | None = None,
    shots_on_target: int | None = None,
    total_pass: int | None = None,
    accurate_pass: int | None = None,
    total_tackle: int | None = None,
    won_tackle: int | None = None,
    yellow_card: int | None = None,
    red_card: int | None = None,
    fouls_committed: int | None = None,
    fouls_suffered: int | None = None,
    offsides: int | None = None,
) -> PlayerCareerSeason:
    return PlayerCareerSeason(
        player_api_id=player_api_id,
        season=season,
        season_start_year=season_start_year,
        competition_code=competition_code,
        competition=competition,
        appearances=appearances,
        goals=goals,
        assists=assists,
        minutes=minutes,
        is_national=is_national,
        starts=starts,
        total_shots=total_shots,
        shots_on_target=shots_on_target,
        total_pass=total_pass,
        accurate_pass=accurate_pass,
        total_tackle=total_tackle,
        won_tackle=won_tackle,
        yellow_card=yellow_card,
        red_card=red_card,
        fouls_committed=fouls_committed,
        fouls_suffered=fouls_suffered,
        offsides=offsides,
    )


def _three_case_rows() -> tuple[list[PlayerCareerSeason], list[BzzPlayerSeasonStat]]:
    bzz_rows = [
        # Case 1 : Ligue 1 2025-2026, des deux cotes. `minutes_played` est
        # NULL cote Bzzoiro -> sera comble par TM (source="merged").
        # total_shots/aerial_won fournis cote Bzzoiro -> doivent gagner sur
        # la valeur TM (99) ; fouls_committed/offsides restent None cote
        # Bzzoiro (hors gabarit) -> seront combles par TM.
        _bzz_row(total_shots=25, shots_on_target=10, aerial_won=8, expected_assists=1.1),
    ]
    tm_rows = [
        # Case 1 (complement de la ligne Bzzoiro ci-dessus). total_shots=99
        # ne doit JAMAIS gagner (Bzzoiro autorite) ; fouls_committed/offsides
        # n'existent pas cote Bzzoiro -> doivent combler.
        _tm_row(
            season="25/26", season_start_year=2025, competition_code="FR1",
            competition="Ligue 1", appearances=10, goals=5, assists=3, minutes=900,
            total_shots=99, fouls_committed=2, fouls_suffered=5, offsides=1,
        ),
        # Case 2 : seulement TM, National 2, saison ancienne.
        _tm_row(
            season="14/15", season_start_year=2014, competition_code="NU2",
            competition="National 2", appearances=20, goals=2, assists=1, minutes=1500,
            total_shots=10, fouls_committed=3,
        ),
        # Case 3 : seulement TM, selection (EURO, code "EM" non mappe -> cf.
        # app.ingestion.competition_identity.TM_CODE_TO_LEAGUE, reste distinct).
        _tm_row(
            season="23/24", season_start_year=2023, competition_code="EM",
            competition="European Championship", appearances=4, goals=1, assists=0,
            minutes=360, is_national=True,
        ),
    ]
    return tm_rows, bzz_rows


# ---------------------------------------------------------------------------
# Tests unitaires sur _build_career_seasons (pas de base necessaire : les
# lignes ORM sont construites en memoire, meme procede que
# tests/test_career_priority.py).
# ---------------------------------------------------------------------------


def test_total_entries_is_three_not_four():
    """4 lignes brutes (1 Bzzoiro + 3 TM) -> 3 entrees apres reconciliation :
    la ligne Ligue 1 des 2 cotes fusionne en une seule."""
    tm_rows, bzz_rows = _three_case_rows()

    seasons = _build_career_seasons(tm_rows, bzz_rows)

    all_entries = [c for s in seasons for c in s.competitions]
    assert len(all_entries) == 3


def test_no_duplicate_season_competition_key():
    tm_rows, bzz_rows = _three_case_rows()
    seasons = _build_career_seasons(tm_rows, bzz_rows)

    keys = [
        (s.season_start_year, c.competition_code, c.competition)
        for s in seasons
        for c in s.competitions
    ]
    assert len(keys) == len(set(keys)), f"doublon detecte parmi {keys}"


def test_case_1_overlap_bzzoiro_authority_with_merge_fill():
    """Ligue 1 2025-2026 : valeurs Bzzoiro (autorite), `minutes` NULL cote
    Bzzoiro comble par TM -> source="merged", jamais 2 entrees."""
    tm_rows, bzz_rows = _three_case_rows()
    seasons = _build_career_seasons(tm_rows, bzz_rows)

    ligue1_season = next(s for s in seasons if s.season_start_year == 2025)
    assert len(ligue1_season.competitions) == 1
    entry = ligue1_season.competitions[0]

    assert entry.source == "merged"
    # Valeurs Bzzoiro (autorite) sur les champs qu'elle avait.
    assert entry.appearances == 10
    assert entry.goals == 5
    assert entry.assists == 3
    # Comble par TM car Bzzoiro avait NULL ici.
    assert entry.minutes == 900
    # Champs xG/notes propres a Bzzoiro, jamais fournis par TM.
    assert entry.expected_goals == pytest.approx(4.2)
    assert entry.avg_rating == pytest.approx(7.1)
    assert entry.is_national is False

    # Task 10 : Bzzoiro gagne quand les deux cotes fournissent le champ.
    assert entry.total_shots == 25  # pas 99 (valeur TM)
    assert entry.shots_on_target == 10
    assert entry.aerial_won == 8
    assert entry.expected_assists == pytest.approx(1.1)
    # Bzzoiro n'a pas ces champs (hors gabarit) -> combles par TM.
    assert entry.fouls_committed == 2
    assert entry.fouls_suffered == 5
    assert entry.offsides == 1


def test_case_2_tm_only_season_no_invented_xg():
    """National 2 14/15 : seulement TM -> source="transfermarkt",
    expected_goals (propre a Bzzoiro) reste None, jamais invente."""
    tm_rows, bzz_rows = _three_case_rows()
    seasons = _build_career_seasons(tm_rows, bzz_rows)

    old_season = next(s for s in seasons if s.season_start_year == 2014)
    assert len(old_season.competitions) == 1
    entry = old_season.competitions[0]

    assert entry.source == "transfermarkt"
    assert entry.competition_code == "NU2"
    assert entry.appearances == 20
    assert entry.goals == 2
    assert entry.assists == 1
    assert entry.minutes == 1500
    assert entry.expected_goals is None
    assert entry.avg_rating is None
    assert entry.is_national is False

    # Task 10 : TM fournit les champs de son gabarit...
    assert entry.total_shots == 10
    assert entry.fouls_committed == 3
    # ...mais jamais les champs propres a Bzzoiro (hors gabarit TM).
    assert entry.expected_goals is None
    assert entry.aerial_won is None
    assert entry.expected_assists is None


def test_case_3_national_selection_bucket_stays_separate():
    """EURO (TM is_national=True, code "EM" non mappe) reste une entree
    distincte, jamais fusionnee avec une competition club."""
    tm_rows, bzz_rows = _three_case_rows()
    seasons = _build_career_seasons(tm_rows, bzz_rows)

    euro_season = next(s for s in seasons if s.season_start_year == 2023)
    assert len(euro_season.competitions) == 1
    entry = euro_season.competitions[0]

    assert entry.source == "transfermarkt"
    assert entry.is_national is True
    assert entry.competition_code == "EM"
    assert entry.expected_goals is None

    # Task 10 : aucune des 2 sources n'a fourni fouls_committed pour cette
    # competition -> le total agrege de la saison reste None, jamais 0 invente.
    assert euro_season.fouls_committed is None


def test_bzzoiro_only_season_gets_bzzoiro_source_untouched():
    """Un bucket Bzzoiro sans aucune ligne TM en vis-a-vis -> source="bzzoiro",
    jamais "merged" (rien a combler)."""
    bzz_rows = [
        _bzz_row(
            league_api_id=6, season="2025-2026", matches_played=10,
            minutes_played=900, goals=5, goal_assist=3,
            aerial_won=8, expected_assists=1.1,
        ),
    ]
    seasons = _build_career_seasons([], bzz_rows)

    assert len(seasons) == 1
    entry = seasons[0].competitions[0]
    assert entry.source == "bzzoiro"
    assert entry.minutes == 900
    # Task 10 : champs propres a Bzzoiro non-None...
    assert entry.aerial_won == 8
    assert entry.expected_assists == pytest.approx(1.1)
    # ...champs hors gabarit Bzzoiro (fouls_committed etc.) restent None,
    # jamais inventes, meme sans ligne TM pour combler.
    assert entry.fouls_committed is None
    assert entry.fouls_suffered is None
    assert entry.offsides is None


# ---------------------------------------------------------------------------
# Task 10 : CareerSeasonOut expose aussi des totaux agreges (sommes sur les
# competitions de la saison) pour le sous-ensemble "Parcours" des nouveaux
# champs. Zero invention : si aucune competition de la saison ne fournit un
# champ, le total agrege reste None (jamais 0 devine).
# ---------------------------------------------------------------------------


def test_season_aggregate_sums_new_fields_across_competitions():
    """2 competitions dans la meme saison, total_shots renseigne sur les
    deux (5 + 7 = 12), fouls_committed renseigne sur une seule (reste 1,
    pas invente a partir de l'autre competition silencieuse)."""
    tm_rows = [
        _tm_row(
            season="22/23", season_start_year=2022, competition_code="FR1",
            competition="Ligue 1", appearances=10, goals=1, assists=1, minutes=900,
            total_shots=5, fouls_committed=1,
        ),
        _tm_row(
            season="22/23", season_start_year=2022, competition_code="CL",
            competition="Champions League", appearances=5, goals=0, assists=0,
            minutes=450, total_shots=7,
        ),
    ]
    seasons = _build_career_seasons(tm_rows, [])

    season = next(s for s in seasons if s.season_start_year == 2022)
    assert len(season.competitions) == 2
    assert season.total_shots == 12
    assert season.fouls_committed == 1


def test_season_aggregate_none_when_no_competition_has_the_field():
    """Aucune competition de la saison ne fournit `offsides` -> le total
    agrege de la saison reste None, jamais 0 invente."""
    tm_rows = [
        _tm_row(
            season="22/23", season_start_year=2022, competition_code="FR1",
            competition="Ligue 1", appearances=10, goals=1, assists=1, minutes=900,
        ),
    ]
    seasons = _build_career_seasons(tm_rows, [])

    season = next(s for s in seasons if s.season_start_year == 2022)
    assert season.offsides is None


# ---------------------------------------------------------------------------
# Test d'integration : le vrai endpoint get_player_career, sur une vraie base
# SQLite en memoire (pas un mock), pour verifier que la requete Bzzoiro est
# bien branchee bout en bout et que blended_rhythm n'est pas affecte.
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for table in (
            BzzPlayer.__table__,
            PlayerCareerSeason.__table__,
            BzzPlayerSeasonStat.__table__,
        ):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_endpoint_reconciles_end_to_end(session_factory, monkeypatch):
    from app.api import players as players_mod

    async def fake_blended_rhythm(session, player_api_id):
        return None

    monkeypatch.setattr(players_mod, "blended_rhythm", fake_blended_rhythm)

    async with session_factory() as session:
        session.add(BzzPlayer(api_id=42, name="Joueur Test"))
        session.add_all(
            [
                BzzPlayerSeasonStat(
                    player_api_id=42, league_api_id=6, season="2025-2026",
                    matches_played=10, minutes_played=None, goals=5, goal_assist=3,
                    expected_goals=4.2, avg_rating=7.1,
                ),
                PlayerCareerSeason(
                    player_api_id=42, season="25/26", season_start_year=2025,
                    competition_code="FR1", competition="Ligue 1",
                    appearances=10, goals=5, assists=3, minutes=900,
                ),
                PlayerCareerSeason(
                    player_api_id=42, season="14/15", season_start_year=2014,
                    competition_code="NU2", competition="National 2",
                    appearances=20, goals=2, assists=1, minutes=1500,
                ),
                PlayerCareerSeason(
                    player_api_id=42, season="23/24", season_start_year=2023,
                    competition_code="EM", competition="European Championship",
                    appearances=4, goals=1, assists=0, minutes=360, is_national=True,
                ),
            ]
        )
        await session.commit()

        response = await get_player_career(player_api_id=42, session=session)

    assert response["player"] == {"player_api_id": 42, "name": "Joueur Test"}
    assert response["blended_rhythm"] is None  # inchange, non touche par la reconciliation

    all_entries = [c for s in response["career"] for c in s.competitions]
    assert len(all_entries) == 3
    sources = sorted(c.source for c in all_entries)
    assert sources == ["merged", "transfermarkt", "transfermarkt"]


@pytest.mark.asyncio
async def test_endpoint_404_when_player_unknown(session_factory):
    from fastapi import HTTPException

    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await get_player_career(player_api_id=999, session=session)

    assert exc_info.value.status_code == 404
