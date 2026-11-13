"""Sélections nationales : récolte des tm_id joueurs depuis les listes TM.

Pourquoi : les joueurs connus de l'univers Atlas par leur seule sélection
(club hors référentiel) n'avaient jamais de tm_id -> carrière bloquée sur la
recherche Transfermarkt rate-limitée. La page effectif d'une sélection
(`/kader/verein/<id>`, même format qu'un club) liste la dernière convocation.

Règle d'or : on n'écrit QUE `player_tm_ids` (source 'squad', exacte : le
joueur figure sur la liste). JAMAIS de `transfermarkt_club_id` sur une
sélection dans `canonical_teams` : la synchro quotidienne des effectifs
(`sync_squads`) déplacerait alors le club actuel des joueurs vers leur
sélection et détacherait leurs vrais clubs.

Identifiants des sélections : classement FIFA de TM
(`/statistik/weltrangliste?page=N`, 25 nations par page, ~211 nations), où
chaque sélection est un lien `/<slug>/startseite/verein/<id>` au titre lisible
-> même analyse que les pages compétition (`parse_competition_clubs`).
Rapprochement par nom replié + table d'alias pour les noms qui diffèrent.
Les territoires non membres de la FIFA (Guadeloupe, Martinique...) n'y
figurent pas : non rapprochés, c'est normal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.transfermarkt.player_match import match_players
from app.ingestion.transfermarkt.resolve_clubs import parse_competition_clubs
from app.ingestion.transfermarkt.squad_scraper import fetch_club_squad
from app.ingestion.transfermarkt.sync_squads import _persist_matched_tm_ids
from app.ingestion.transfermarkt.text_utils import fold_accents
from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam
from app.scripts.transfermarkt_career import BASE_URL, TransfermarktClient

logger = logging.getLogger(__name__)

# Ligues Bzzoiro des sélections dans `canonical_teams` : Coupe du monde 2026,
# Ligue des nations UEFA, Ligue des nations CONCACAF.
NATIONAL_LEAGUE_IDS: tuple[int, ...] = (27, 64, 65)

RANKING_MAX_PAGES = 12

# nom canonique replié -> nom TM replié, quand ils diffèrent (vérifié le
# 13/11/2026 sur le classement FIFA de TM).
NATION_ALIASES: dict[str, str] = {
    "usa": "united states",
    "turkiye": "turkey",
    "czechia": "czech republic",
    "cabo verde": "cape verde",
    "bosnia & herzegovina": "bosnia-herzegovina",
    "dr congo": "democratic republic of the congo",
    "cote d'ivoire": "ivory coast",
    "south korea": "korea, south",
    "ireland": "republic of ireland",
    "saint kitts and nevis": "st. kitts & nevis",
    "saint lucia": "st. lucia",
    "saint vincent and the grenadines": "st. vincent & grenadinen",
    "turks and caicos islands": "turks- and caicosinseln",
    "us virgin islands": "united states virgin islands",
}


@dataclass
class NationalHarvestReport:
    nations_tm: int = 0
    nations_matched: int = 0
    nations_failed: int = 0
    players_listed: int = 0
    players_matched: int = 0
    unmatched_nations: list[str] = field(default_factory=list)


def fetch_national_team_ids(client: TransfermarktClient) -> dict[str, tuple[str, int]]:
    """{nom TM replié: (nom TM, id TM)} pour toutes les sélections du classement FIFA."""
    nations: dict[str, tuple[str, int]] = {}
    for page in range(1, RANKING_MAX_PAGES + 1):
        resp = client.get(
            f"{BASE_URL}/statistik/weltrangliste?page={page}",
            what=f"le classement FIFA Transfermarkt (page {page})",
        )
        found = parse_competition_clubs(resp.text)
        if not found:
            break
        for name, tm_id in found:
            nations.setdefault(fold_accents(name), (name, tm_id))
    return nations


def match_nations(
    teams: list[CanonicalTeam], nations: dict[str, tuple[str, int]]
) -> tuple[dict[int, str], list[str]]:
    """({id TM sélection: nom canonique}, [noms canoniques non rapprochés])."""
    matched: dict[int, str] = {}
    missing: list[str] = []
    for team in teams:
        keys = [fold_accents(team.name_fr)]
        if team.name_en:
            keys.append(fold_accents(team.name_en))
        keys += [NATION_ALIASES[k] for k in list(keys) if k in NATION_ALIASES]
        hit = next((nations[k] for k in keys if k in nations), None)
        if hit is None:
            missing.append(team.name_fr)
            continue
        matched.setdefault(hit[1], team.name_fr)
    return matched, sorted(set(missing))


async def harvest_national_tm_ids(
    session: AsyncSession, client: TransfermarktClient, today: date
) -> NationalHarvestReport:
    """Lit la liste de chaque sélection rapprochée et enregistre les tm_id
    des joueurs reconnus. Ne modifie jamais le club d'un joueur."""
    report = NationalHarvestReport()
    nations = fetch_national_team_ids(client)
    report.nations_tm = len(nations)

    teams = list(
        (
            await session.execute(
                select(CanonicalTeam).where(CanonicalTeam.league_api_id.in_(NATIONAL_LEAGUE_IDS))
            )
        ).scalars().all()
    )
    to_read, report.unmatched_nations = match_nations(teams, nations)
    report.nations_matched = len(to_read)

    for tm_id, name in to_read.items():
        squad = await fetch_club_squad(client, tm_id)
        if squad.status != "ok":
            report.nations_failed += 1
            logger.warning("Sélection %s (TM %d) : liste illisible (%s).", name, tm_id, squad.status)
            continue
        # Pas de bzz_team_id : la seconde passe "naissance + club" ne s'applique
        # pas à une sélection (les joueurs n'y sont pas rattachés).
        matches = await match_players(session, squad.players, today)
        ids = set(matches.matched.values())
        found: set[int] = set()
        if ids:
            found = set(
                (await session.execute(select(BzzPlayer.api_id).where(BzzPlayer.api_id.in_(ids)))).scalars().all()
            )
        await _persist_matched_tm_ids(session, matches.matched, found)
        await session.commit()
        report.players_listed += len(squad.players)
        report.players_matched += len(found)

    return report
