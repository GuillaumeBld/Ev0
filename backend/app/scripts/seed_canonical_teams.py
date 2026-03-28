"""Seed canonical_teams and fix player duplicates.

Usage (inside the backend container):
    python -m app.scripts.seed_canonical_teams

What it does:
  1. Insert all canonical teams with French names + aliases
  2. Set canonical_team_id on players and fixtures by alias matching
  3. Merge duplicate players (same name, same canonical_team) — keep the one
     with the most matches played, reassign FK references, delete the rest
  4. Add UNIQUE(name, canonical_team_id) constraint on players

Safe to run multiple times (idempotent).
"""
from __future__ import annotations

import asyncio
import unicodedata

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models.canonical_teams import CanonicalTeam
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent
from app.models.odds import OddsSnapshot
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.players import Player, PlayerStats
from app.models.recommendations import Recommendation

# ── Canonical team definitions ────────────────────────────────────
# Format: (name_fr, [alias1, alias2, …])
# aliases = every raw spelling ever seen from Understat / FotMob / API-Football

CANONICAL_TEAMS: list[tuple[str, list[str]]] = [
    # ── Bundesliga ────────────────────────────────────────────────
    ("Bayern Munich",       ["Bayern München", "Bayern Munich", "FC Bayern München",
                             "FC Bayern Munich", "FC Bayern"]),
    ("Dortmund",            ["Borussia Dortmund", "BVB", "BVB Dortmund"]),
    ("Leverkusen",          ["Bayer Leverkusen", "Bayer 04 Leverkusen"]),
    ("Leipzig",             ["RB Leipzig", "Red Bull Leipzig"]),
    ("Mönchengladbach",     ["Borussia Mönchengladbach", "B. Mönchengladbach", "Monchengladbach"]),
    ("Francfort",           ["Eintracht Frankfurt", "Frankfurt"]),
    ("Stuttgart",           ["VfB Stuttgart", "Stuttgart"]),
    ("Fribourg",            ["Freiburg", "SC Freiburg"]),
    ("Wolfsbourg",          ["Wolfsburg", "VfL Wolfsburg"]),
    ("Hoffenheim",          ["Hoffenheim", "TSG Hoffenheim", "TSG 1899 Hoffenheim"]),
    ("Werder Brême",        ["Werder Bremen", "Bremen", "SV Werder Bremen"]),
    ("Augsbourg",           ["Augsburg", "FC Augsburg"]),
    ("Union Berlin",        ["Union Berlin", "1. FC Union Berlin"]),
    ("Mayence",             ["Mainz", "FSV Mainz", "1. FSV Mainz 05", "Mainz 05"]),
    ("Cologne",             ["Köln", "FC Köln", "Cologne", "FC Cologne", "1. FC Köln"]),
    ("Bochum",              ["Bochum", "VfL Bochum"]),
    ("Heidenheim",          ["Heidenheim", "1. FC Heidenheim", "FC Heidenheim"]),
    ("Darmstadt",           ["Darmstadt", "SV Darmstadt 98", "Darmstadt 98"]),
    ("Kiel",                ["Holstein Kiel"]),
    ("Saint-Pauli",         ["FC St. Pauli", "St. Pauli"]),
    # ── Ligue 1 ──────────────────────────────────────────────────
    ("Paris Saint-Germain", ["Paris Saint-Germain", "PSG", "Paris Saint Germain",
                             "Paris SG", "Paris S-G"]),
    ("Marseille",           ["Olympique de Marseille", "Olympique Marseille", "OM", "Marseille"]),
    ("Lyon",                ["Olympique Lyonnais", "Lyon", "OL"]),
    ("Monaco",              ["AS Monaco", "Monaco"]),
    ("Lille",               ["LOSC Lille", "LOSC", "Lille"]),
    ("Nice",                ["OGC Nice", "Nice"]),
    ("Rennes",              ["Stade Rennais", "Rennes", "Stade Rennais FC"]),
    ("Lens",                ["RC Lens", "Lens"]),
    ("Toulouse",            ["Toulouse FC", "Toulouse"]),
    ("Nantes",              ["FC Nantes", "Nantes"]),
    ("Brest",               ["Stade Brestois 29", "Brest", "SB 29"]),
    ("Montpellier",         ["Montpellier HSC", "Montpellier", "MHSC"]),
    ("Auxerre",             ["AJ Auxerre", "Auxerre"]),
    ("Saint-Étienne",       ["AS Saint-Étienne", "Saint-Etienne", "ASSE",
                             "Saint-Étienne", "St Etienne"]),
    ("Strasbourg",          ["RC Strasbourg", "Strasbourg", "RCSA",
                             "Racing Club de Strasbourg"]),
    ("Metz",                ["FC Metz", "Metz"]),
    ("Reims",               ["Stade de Reims", "Reims"]),
    ("Lorient",             ["FC Lorient", "Lorient"]),
    ("Clermont",            ["Clermont Foot", "Clermont", "Clermont Foot 63"]),
    ("Angers",              ["Angers SCO", "Angers", "SCO Angers"]),
    ("Le Havre",            ["Le Havre AC", "Le Havre", "HAC"]),
    ("Rodez",               ["Rodez AF", "Rodez"]),
    # ── Premier League ────────────────────────────────────────────
    ("Arsenal",             ["Arsenal", "Arsenal FC"]),
    ("Chelsea",             ["Chelsea", "Chelsea FC"]),
    ("Liverpool",           ["Liverpool", "Liverpool FC"]),
    ("Manchester City",     ["Manchester City", "Man City", "Man. City"]),
    ("Manchester United",   ["Manchester United", "Man United", "Man Utd", "Man. United"]),
    ("Tottenham",           ["Tottenham Hotspur", "Tottenham", "Spurs"]),
    ("Newcastle",           ["Newcastle United", "Newcastle", "NUFC"]),
    ("Brighton",            ["Brighton & Hove Albion", "Brighton", "BHAFC",
                             "Brighton and Hove Albion"]),
    ("Aston Villa",         ["Aston Villa", "Villa"]),
    ("Wolverhampton",       ["Wolverhampton Wanderers", "Wolves", "Wolverhampton"]),
    ("West Ham",            ["West Ham United", "West Ham", "WHUFC"]),
    ("Crystal Palace",      ["Crystal Palace"]),
    ("Brentford",           ["Brentford", "Brentford FC"]),
    ("Fulham",              ["Fulham", "Fulham FC"]),
    ("Everton",             ["Everton", "Everton FC"]),
    ("Nottingham Forest",   ["Nottingham Forest", "Nott'm Forest", "Nottm Forest",
                             "Nottingham F"]),
    ("Bournemouth",         ["AFC Bournemouth", "Bournemouth"]),
    ("Leicester",           ["Leicester City", "Leicester"]),
    ("Ipswich",             ["Ipswich Town", "Ipswich"]),
    ("Southampton",         ["Southampton", "Southampton FC"]),
    ("Leeds",               ["Leeds United", "Leeds"]),
    ("Sunderland",          ["Sunderland", "Sunderland AFC"]),
    # ── Champions League / Europe ─────────────────────────────────
    ("Real Madrid",         ["Real Madrid", "Real Madrid CF"]),
    ("Barcelone",           ["Barcelona", "FC Barcelona", "Barça"]),
    ("Atlético Madrid",     ["Atlético Madrid", "Atletico Madrid",
                             "Atletico de Madrid", "Club Atlético de Madrid"]),
    ("Séville",             ["Sevilla", "Sevilla FC"]),
    ("Villarreal",          ["Villarreal", "Villarreal CF"]),
    ("Real Sociedad",       ["Real Sociedad"]),
    ("Athletic Bilbao",     ["Athletic Club", "Athletic Bilbao"]),
    ("Valence",             ["Valencia", "Valencia CF"]),
    ("Betis",               ["Real Betis", "Betis", "Real Betis Balompié"]),
    ("Milan",               ["AC Milan", "Milan"]),
    ("Inter Milan",         ["Inter", "Inter Milan", "FC Internazionale",
                             "Internazionale"]),
    ("Juventus",            ["Juventus", "Juventus FC"]),
    ("Naples",              ["Napoli", "SSC Napoli"]),
    ("Roma",                ["AS Roma", "Roma"]),
    ("Lazio",               ["Lazio", "SS Lazio"]),
    ("Atalanta",            ["Atalanta", "Atalanta BC"]),
    ("Fiorentina",          ["Fiorentina", "ACF Fiorentina"]),
    ("Benfica",             ["Benfica", "SL Benfica"]),
    ("Porto",               ["Porto", "FC Porto"]),
    ("Sporting CP",         ["Sporting CP", "Sporting Clube de Portugal"]),
    ("Bruges",              ["Club Brugge", "Brugge", "Club Bruges"]),
    ("Ajax",                ["Ajax", "AFC Ajax"]),
    ("PSV Eindhoven",       ["PSV", "PSV Eindhoven"]),
    ("Feyenoord",           ["Feyenoord"]),
    ("Celtic",              ["Celtic", "Celtic FC"]),
    ("Rangers",             ["Rangers", "Rangers FC"]),
    ("Shakhtar",            ["Shakhtar Donetsk", "Shakhtar"]),
    ("Dinamo Zagreb",       ["Dinamo Zagreb", "GNK Dinamo Zagreb"]),
    ("Red Bull Salzbourg",  ["Red Bull Salzburg", "RB Salzburg", "Salzburg"]),
]


def _norm(s: str) -> str:
    """Lowercase + strip accents for fuzzy matching."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


async def run(db: AsyncSession) -> None:
    print("=== seed_canonical_teams ===")

    # ── 1. Upsert canonical teams ─────────────────────────────────
    print("1. Upsert canonical teams…")
    for name_fr, aliases in CANONICAL_TEAMS:
        stmt = (
            pg_insert(CanonicalTeam)
            .values(name_fr=name_fr, aliases=aliases)
            .on_conflict_do_update(
                index_elements=["name_fr"],
                set_={"aliases": aliases},
            )
        )
        await db.execute(stmt)
    await db.commit()

    # Build alias → canonical_team_id map
    all_teams = (await db.execute(select(CanonicalTeam))).scalars().all()
    alias_map: dict[str, int] = {}
    for ct in all_teams:
        for alias in (ct.aliases or []):
            alias_map[_norm(alias)] = ct.id
        alias_map[_norm(ct.name_fr)] = ct.id

    print(f"   {len(all_teams)} canonical teams, {len(alias_map)} aliases indexed")

    # ── 2. Set canonical_team_id on players ───────────────────────
    print("2. Linking players to canonical teams…")
    players_all = (await db.execute(select(Player))).scalars().all()
    linked = 0
    for p in players_all:
        if not p.team:
            continue
        ct_id = alias_map.get(_norm(p.team))
        if ct_id and p.canonical_team_id != ct_id:
            p.canonical_team_id = ct_id
            linked += 1
    await db.commit()
    print(f"   {linked} players linked")

    # ── 3. Set canonical_team_id on fixtures ──────────────────────
    print("3. Linking fixtures to canonical teams…")
    fixtures_all = (await db.execute(select(Fixture))).scalars().all()
    linked_fx = 0
    for f in fixtures_all:
        h_id = alias_map.get(_norm(f.home_team or ""))
        a_id = alias_map.get(_norm(f.away_team or ""))
        if h_id and f.home_canonical_team_id != h_id:
            f.home_canonical_team_id = h_id
            linked_fx += 1
        if a_id and f.away_canonical_team_id != a_id:
            f.away_canonical_team_id = a_id
            linked_fx += 1
    await db.commit()
    print(f"   {linked_fx} fixture-team links updated")

    # ── 4. Merge duplicate players ────────────────────────────────
    print("4. Merging duplicate players…")
    total_merged = 0

    for ct in all_teams:
        # Find all players for this canonical team
        res = await db.execute(
            select(Player).where(Player.canonical_team_id == ct.id)
        )
        team_players = res.scalars().all()

        # Group by normalised name
        groups: dict[str, list[Player]] = {}
        for p in team_players:
            key = _norm(p.name or "")
            groups.setdefault(key, []).append(p)

        for name_key, dupes in groups.items():
            if len(dupes) < 2:
                continue

            # Pick the player with the most matches_played as the canonical one
            best_stats: dict[int, int] = {}
            for p in dupes:
                res_s = await db.execute(
                    select(PlayerStats.matches_played)
                    .where(
                        PlayerStats.player_id == p.id,
                        PlayerStats.source == "average",
                    )
                    .order_by(PlayerStats.as_of_utc.desc())
                    .limit(1)
                )
                row = res_s.scalar_one_or_none()
                best_stats[p.id] = row or 0

            keep = max(dupes, key=lambda p: best_stats[p.id])
            to_delete = [p for p in dupes if p.id != keep.id]
            delete_ids = [p.id for p in to_delete]

            print(f"   {ct.name_fr} — merge {[p.name for p in to_delete]} → keep id={keep.id}")

            # Reassign FK references — for tables with unique constraints on
            # (player_id, …), delete conflicting rows first then update the rest
            for del_id in delete_ids:
                # player_stats: delete rows where keep already has the same snapshot
                conflict_sub = (
                    select(PlayerStats.as_of_utc, PlayerStats.league,
                           PlayerStats.season, PlayerStats.source)
                    .where(PlayerStats.player_id == keep.id)
                    .subquery()
                )
                await db.execute(
                    delete(PlayerStats).where(
                        PlayerStats.player_id == del_id,
                        PlayerStats.as_of_utc.in_(
                            select(conflict_sub.c.as_of_utc)
                        ),
                    )
                )
                # Move remaining stats to kept player
                await db.execute(
                    update(PlayerStats)
                    .where(PlayerStats.player_id == del_id)
                    .values(player_id=keep.id)
                )
                # Other FK tables — no unique constraint on player_id alone
                for Model in (Recommendation, MatchEvent, OddsSnapshot, PlayerMatchMinutes):
                    await db.execute(
                        update(Model)
                        .where(Model.player_id == del_id)  # type: ignore[attr-defined]
                        .values(player_id=keep.id)
                    )
                # Delete the duplicate player
                await db.execute(delete(Player).where(Player.id == del_id))

            total_merged += len(delete_ids)

    await db.commit()
    print(f"   {total_merged} duplicate players removed")

    # ── 5. Add UNIQUE constraint (players name + canonical_team_id) ─
    print("5. Adding UNIQUE(name, canonical_team_id) on players…")
    try:
        await db.execute(text(
            "ALTER TABLE players "
            "ADD CONSTRAINT uq_player_name_canonical_team "
            "UNIQUE (name, canonical_team_id)"
        ))
        await db.commit()
        print("   Constraint added")
    except Exception as e:
        await db.rollback()
        print(f"   Constraint already exists or error: {e}")

    print("=== Done ===")


async def main() -> None:
    import os
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        await run(db)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
