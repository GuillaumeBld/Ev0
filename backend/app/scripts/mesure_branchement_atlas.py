"""Mesure de fin de chantier : le calculateur et l'Atlas lisent-ils la meme source ?

    python -m app.scripts.mesure_branchement_atlas [--days 14]

LECTURE SEULE (SELECT uniquement, aucun commit). Sur les matchs dont le
coup d'envoi tombe dans les N prochains jours, affiche une ligne par mesure :
equipes reliees, ecart d'effectif, minutes ignorees, joueurs sur saison
ancienne / sans stat, ecussons manquants, matchs mal ranges, cotes reliees.
Les fonctions `calc_*` / `ligne_*` sont pures (testables sans base).
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

OBJECTIFS = (
    "Objectifs : mal rangés 0 · minutes ignorées 0 % · "
    "écussons manquants ≤ 10 · cotes reliées ≥ 95 %"
)


def pct(num: int | float, den: int | float) -> float | None:
    """Pourcentage, ou None si rien a mesurer (jamais 0 % invente)."""
    if not den:
        return None
    return 100.0 * num / den


def _fmt_pct(valeur: float | None) -> str:
    return "n/a" if valeur is None else f"{valeur:.1f} %"


# ── Calculs purs ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FixtureRow:
    id: int
    home_bzz_team_id: int | None
    away_bzz_team_id: int | None
    league: str | None
    competition_api_id: int | None
    bzz_league_api_id: int | None  # bzz_events.league_api_id (None si pas de jointure)


def team_slots(fixtures: Iterable[FixtureRow]) -> list[int | None]:
    """Deux places d'equipe par match (domicile, exterieur)."""
    slots: list[int | None] = []
    for f in fixtures:
        slots.extend([f.home_bzz_team_id, f.away_bzz_team_id])
    return slots


def calc_equipes_reliees(slots: list[int | None], canon_ids: set[int]) -> tuple[int, int]:
    reliees = sum(1 for s in slots if s is not None and s in canon_ids)
    return reliees, len(slots)


def calc_ecart_effectif(calc: set[int], atlas: set[int]) -> tuple[int, int, int]:
    """(manquants cote calculateur, en trop cote calculateur, taille Atlas)."""
    return len(atlas - calc), len(calc - atlas), len(atlas)


def calc_minutes(
    rows: Iterable[tuple[int, str, int | None]], current_season: str
) -> tuple[int, int]:
    """(minutes comptees par le calculateur, minutes de la saison courante).

    Le calculateur compte TOUTES les lignes de la derniere saison de chaque
    joueur. Pour la saison courante : minutes des joueurs dont la derniere
    saison est la courante, contre toutes les minutes de la saison courante.
    """
    par_joueur: dict[int, list[tuple[str, int]]] = {}
    for pid, season, minutes in rows:
        par_joueur.setdefault(pid, []).append((season, minutes or 0))
    comptees = totales = 0
    for lignes in par_joueur.values():
        derniere = max(s for s, _ in lignes)
        courantes = sum(m for s, m in lignes if s == current_season)
        totales += courantes
        if derniere == current_season:
            comptees += sum(m for s, m in lignes if s == derniere)
    return comptees, totales


def calc_saisons(
    player_ids: set[int], rows: Iterable[tuple[int, str, int | None]], current_season: str
) -> tuple[int, int, int]:
    """(joueurs sur saison ancienne, joueurs sans aucune stat, total joueurs)."""
    derniere: dict[int, str] = {}
    for pid, season, _ in rows:
        if pid not in derniere or season > derniere[pid]:
            derniere[pid] = season
    sans_stat = sum(1 for p in player_ids if p not in derniere)
    anciens = sum(1 for p in player_ids if p in derniere and derniere[p] < current_season)
    return anciens, sans_stat, len(player_ids)


def calc_ecussons_manquants(
    slots: list[int | None], crests: dict[int, tuple[str | None, int | None]]
) -> tuple[int, int]:
    """Places dont la fiche existe mais n'a ni crest_url ni api_football_id."""
    manquants = sum(
        1 for s in slots
        if s is not None and s in crests and crests[s][0] is None and crests[s][1] is None
    )
    return manquants, len(slots)


def calc_mal_ranges(
    fixtures: Iterable[FixtureRow], league_key_for: Any
) -> tuple[int, int]:
    """(matchs mal ranges, matchs Bzzoiro examines)."""
    mal = total = 0
    for f in fixtures:
        total += 1
        if f.bzz_league_api_id is None:
            continue  # pas de verite Atlas : non mesurable ici
        if (
            f.competition_api_id is None
            or f.competition_api_id != f.bzz_league_api_id
            or f.league != league_key_for(f.bzz_league_api_id)
        ):
            mal += 1
    return mal, total


# ── Lignes affichees ─────────────────────────────────────────────────────

def ligne_equipes(reliees: int, total: int) -> str:
    return (f"1. Équipes reliées à l'Atlas : {reliees}/{total} "
            f"({_fmt_pct(pct(reliees, total))})")


def ligne_effectif(manquants: int, en_trop: int, taille_atlas: int) -> str:
    return (f"2. Écart d'effectif calculateur/Atlas : {manquants} manquants, "
            f"{en_trop} en trop (Atlas : {taille_atlas} joueurs)")


def ligne_minutes(comptees: int, totales: int) -> str:
    ignorees = max(totales - comptees, 0)
    return (f"3. Minutes ignorées : {_fmt_pct(pct(ignorees, totales))} "
            f"({ignorees} min sur {totales})")


def ligne_saisons(anciens: int, sans_stat: int, total: int) -> str:
    return (f"4. Joueurs cotés sur une saison ancienne : {anciens}/{total} "
            f"({_fmt_pct(pct(anciens, total))}) ; sans aucune stat : {sans_stat}/{total} "
            f"({_fmt_pct(pct(sans_stat, total))})")


def ligne_ecussons(manquants: int, total: int) -> str:
    return f"5. Écussons manquants : {manquants} places sur {total}"


def ligne_mal_ranges(mal: int, total: int) -> str:
    return f"6. Matchs mal rangés : {mal} sur {total}"


def ligne_cotes(reliees: int, total: int) -> str:
    return (f"7. Cotes bookmakers reliées à un joueur (24 h) : {reliees}/{total} "
            f"({_fmt_pct(pct(reliees, total))})")


# ── Lecture base (SELECT uniquement) ─────────────────────────────────────

async def _mesurer(session, days: int) -> list[str]:
    from sqlalchemy import String, and_, cast, func, or_, select

    from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerSeasonStat
    from app.models.canonical_teams import CanonicalTeam
    from app.models.fixtures import Fixture
    from app.models.player_odds_snapshot import PlayerOddsSnapshot
    from app.services.atlas_source import club_squad_filter
    from app.services.competitions import league_key_for
    from app.services.season_service import current_season

    now = datetime.now(timezone.utc)
    saison = await current_season(session)

    rows = (await session.execute(
        select(
            Fixture.id, Fixture.home_bzz_team_id, Fixture.away_bzz_team_id,
            Fixture.league, Fixture.competition_api_id, BzzEvent.league_api_id,
            Fixture.external_id,
        )
        .outerjoin(
            BzzEvent,
            Fixture.external_id == func.concat("bzz_", cast(BzzEvent.api_id, String)),
        )
        .where(Fixture.kickoff_utc >= now, Fixture.kickoff_utc < now + timedelta(days=days))
    )).all()
    fixtures = [FixtureRow(*r[:6]) for r in rows]
    bzz_fixtures = [
        FixtureRow(*r[:6]) for r in rows if (r[6] or "").startswith("bzz_")
    ]
    slots = team_slots(fixtures)
    team_ids = {s for s in slots if s is not None}

    canon = {}
    if team_ids:
        canon = {
            r[0]: (r[1], r[2])
            for r in (await session.execute(
                select(CanonicalTeam.bzz_team_id, CanonicalTeam.crest_url,
                       CanonicalTeam.api_football_id)
                .where(CanonicalTeam.bzz_team_id.in_(team_ids))
            )).all()
        }

    # Effectifs : calculateur (club_squad_filter) vs Atlas (current_team_api_id).
    calc_ids: set[int] = set()
    atlas_ids: set[int] = set()
    if team_ids:
        non_gardien = or_(BzzPlayer.position.is_(None), BzzPlayer.position != "G")
        calc_ids = {r[0] for r in (await session.execute(
            select(BzzPlayer.api_id).where(
                or_(*[club_squad_filter(t) for t in team_ids]), non_gardien)
        )).all()}
        atlas_ids = {r[0] for r in (await session.execute(
            select(BzzPlayer.api_id).where(
                BzzPlayer.current_team_api_id.in_(team_ids), non_gardien)
        )).all()}

    stats = []
    if calc_ids:
        stats = [
            (r[0], r[1], r[2]) for r in (await session.execute(
                select(BzzPlayerSeasonStat.player_api_id, BzzPlayerSeasonStat.season,
                       BzzPlayerSeasonStat.minutes_played)
                .where(BzzPlayerSeasonStat.player_api_id.in_(calc_ids))
            )).all()
        ]

    odds_total = odds_relies = 0
    fixture_ids = [f.id for f in fixtures]
    if fixture_ids:
        depuis = now - timedelta(hours=24)
        odds_total, odds_relies = (await session.execute(
            select(
                func.count(),
                func.count(PlayerOddsSnapshot.player_api_id),
            ).where(
                and_(PlayerOddsSnapshot.fixture_id.in_(fixture_ids),
                     PlayerOddsSnapshot.scraped_at >= depuis)
            )
        )).one()

    manq, en_trop, taille = calc_ecart_effectif(calc_ids, atlas_ids)
    comptees, totales = calc_minutes(stats, saison)
    anciens, sans_stat, nb_joueurs = calc_saisons(calc_ids, stats, saison)
    mal, nb_bzz = calc_mal_ranges(bzz_fixtures, league_key_for)
    reliees, nb_slots = calc_equipes_reliees(slots, set(canon))
    ecu, nb_ecu = calc_ecussons_manquants(slots, canon)
    return [
        f"Mesure du branchement Atlas — {len(fixtures)} matchs dans les {days} prochains jours "
        f"(saison {saison})",
        ligne_equipes(reliees, nb_slots),
        ligne_effectif(manq, en_trop, taille),
        ligne_minutes(comptees, totales),
        ligne_saisons(anciens, sans_stat, nb_joueurs),
        ligne_ecussons(ecu, nb_ecu),
        ligne_mal_ranges(mal, nb_bzz),
        ligne_cotes(odds_relies, odds_total),
        OBJECTIFS,
    ]


async def main(days: int) -> None:
    from app.db import async_session

    async with async_session() as session:
        for ligne in await _mesurer(session, days):
            print(ligne)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    asyncio.run(main(ap.parse_args().days))
