"""Enregistre les cotes Bet365 d'un match à partir du texte de sa page.

Usage (le texte de la page « Pari personnalisé » arrive sur l'entrée standard) :

    python -m app.scripts.import_bet365 --fixture 1180285 < page.txt
    python -m app.scripts.import_bet365 --fixture 1180285 --dry-run < page.txt

Affiche ce qui a été lu et relié aux joueurs de l'Atlas, puis recalcule les
recos du match.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db import async_session
from app.ingestion.bet365_reader import Bet365ParseError, parse_bet_builder_text, to_scrape_result
from app.ingestion.odds_storage import store_match_scrape_result
from app.models.fixtures import Fixture
from app.services.player_identity import load_fixture_squad, resolve_odds_player


async def _run(fixture_id: int, text: str, dry_run: bool) -> int:
    try:
        parsed = parse_bet_builder_text(text)
    except Bet365ParseError as exc:
        print(f"ÉCHEC lecture : {exc}")
        return 1

    async with async_session() as session:
        fx = (await session.execute(select(Fixture).where(Fixture.id == fixture_id))).scalar_one_or_none()
        if fx is None:
            print(f"ÉCHEC : match {fixture_id} inconnu")
            return 1
        squad = await load_fixture_squad(session, fixture_id)
        relies = [n for n in parsed.players if resolve_odds_player(n, squad) is not None]
        non_relies = [n for n in parsed.players if n not in relies]
        print(
            f"{fx.home_team} - {fx.away_team} : {len(parsed.players)} joueurs lus, "
            f"{len(relies)} reliés à l'Atlas, {'avec' if parsed.avec_sub else 'SANS'} remplaçant"
        )
        if non_relies:
            print("  non reliés :", ", ".join(non_relies))
        if dry_run:
            return 0
        result = to_scrape_result(parsed, fx.id, fx.home_team, fx.away_team, fx.kickoff_utc, fx.league)
        _, n = await store_match_scrape_result(result, session)
        print(f"  {n} cotes enregistrées")

    from app.services.recommendation_service import process_scraped_fixtures

    async with async_session() as session:
        stats = await process_scraped_fixtures([fixture_id], session)
        print("  recos :", {k: v for k, v in stats.items() if v})
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.fixture, sys.stdin.read(), args.dry_run)))


if __name__ == "__main__":
    main()
