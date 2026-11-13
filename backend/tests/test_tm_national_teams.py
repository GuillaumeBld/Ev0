"""Tests de la récolte tm_id via les sélections (`national_teams.py`).

Garde-fou central : une sélection ne reçoit JAMAIS de
`transfermarkt_club_id` et le club d'un joueur n'est jamais modifié.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.transfermarkt import national_teams as nt
from app.ingestion.transfermarkt.player_match import MatchReport
from app.ingestion.transfermarkt.squad_scraper import SquadResult, TMPlayer
from app.models.canonical_teams import CanonicalTeam


def _ranking_page(entries: list[tuple[str, int]]) -> str:
    return "".join(
        f'<a title="{name}" href="/{name.lower().replace(" ", "-")}/startseite/verein/{tm_id}">x</a>'
        for name, tm_id in entries
    )


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _RankingClient:
    def __init__(self, pages: list[str]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, *, what: str = "", accept_json: bool = False) -> _Resp:
        self.urls.append(url)
        page = int(url.rsplit("=", 1)[1])
        return _Resp(self.pages[page - 1] if page <= len(self.pages) else "")


def _nation(id_: int, name: str) -> CanonicalTeam:
    return CanonicalTeam(id=id_, name_fr=name, name_en=None, aliases=[], league_api_id=27)


def test_fetch_national_team_ids_paginates_until_empty_page():
    client = _RankingClient([_ranking_page([("France", 3377)]), _ranking_page([("Ivory Coast", 3591)])])

    nations = nt.fetch_national_team_ids(client)  # type: ignore[arg-type]

    assert nations == {"france": ("France", 3377), "ivory coast": ("Ivory Coast", 3591)}
    assert len(client.urls) == 3  # 2 pages pleines + la page vide qui arrête


def test_match_nations_uses_aliases_and_reports_non_fifa_territories():
    nations = {
        "france": ("France", 3377),
        "ivory coast": ("Ivory Coast", 3591),
        "united states": ("United States", 3505),
        "republic of ireland": ("Republic of Ireland", 3509),
    }
    teams = [
        _nation(1, "France"),
        _nation(2, "Côte d'Ivoire"),
        _nation(3, "USA"),
        _nation(4, "Ireland"),
        _nation(5, "Guadeloupe"),
    ]

    matched, missing = nt.match_nations(teams, nations)

    assert matched == {3377: "France", 3591: "Côte d'Ivoire", 3505: "USA", 3509: "Ireland"}
    assert missing == ["Guadeloupe"]


@pytest.mark.asyncio
async def test_harvest_writes_tm_ids_only_never_a_club_id_on_the_nation():
    france = _nation(1, "France")
    session = AsyncMock()
    nations_result = AsyncMock()
    nations_result.scalars = lambda: type("S", (), {"all": lambda self: [france]})()
    found_result = AsyncMock()
    found_result.scalars = lambda: type("S", (), {"all": lambda self: [500]})()
    session.execute = AsyncMock(side_effect=[nations_result, found_result])

    squad = SquadResult(
        club_id=3377,
        players=[TMPlayer(name=f"P{i}", dob=None, age=25, position=None, tm_player_id=10 + i) for i in range(20)],
        status="ok",
        raw_html="",
    )
    with patch.object(nt, "fetch_national_team_ids", return_value={"france": ("France", 3377)}), \
         patch.object(nt, "fetch_club_squad", AsyncMock(return_value=squad)), \
         patch.object(nt, "match_players", AsyncMock(return_value=MatchReport(matched={10: 500}, unmatched=[]))) as mp, \
         patch.object(nt, "_persist_matched_tm_ids", AsyncMock()) as persist:
        report = await nt.harvest_national_tm_ids(session, client=object(), today=date(2026, 11, 13))  # type: ignore[arg-type]

    persist.assert_awaited_once_with(session, {10: 500}, {500})
    # Pas de contexte club : la seconde passe naissance+club ne s'applique pas.
    assert "bzz_team_id" not in mp.await_args.kwargs
    assert france.transfermarkt_club_id is None
    assert report.nations_matched == 1
    assert report.players_matched == 1
    assert report.players_listed == 20
