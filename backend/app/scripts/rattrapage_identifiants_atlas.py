"""Rattrapage des identifiants Atlas sur les donnees ecrites avant le branchement.

    python -m app.scripts.rattrapage_identifiants_atlas            # simulation : compte
    python -m app.scripts.rattrapage_identifiants_atlas --apply    # ecrit

Deux rattrapages idempotents (ne touchent que les lignes sans identifiant) :
- cotes bookmakers `player_odds_snapshots.player_api_id` (meme resolveur que
  l'ecriture des cotes, effectif Atlas du match) ;
- passes decisives `match_events.player_id` relues dans les incidents Bzzoiro.
A lancer apres le deploiement, apres la reparation des competitions.
"""
from __future__ import annotations

import argparse
import asyncio


async def main(apply: bool) -> None:
    from sqlalchemy import text

    from app.db import async_session
    from app.ingestion.bzzoiro.sync_incidents import backfill_assist_player_ids
    from app.ingestion.odds_storage import backfill_player_api_ids

    async with async_session() as session:
        cotes, passes = (await session.execute(text("""
            SELECT
              (SELECT count(*) FROM player_odds_snapshots WHERE player_api_id IS NULL),
              (SELECT count(*) FROM match_events
                 WHERE event_type = 'assist' AND player_id IS NULL)
        """))).one()
        print(f"Cotes bookmakers sans joueur : {cotes}")
        print(f"Passes décisives sans joueur : {passes}")
        if not apply:
            print("Simulation : rien n'est écrit. Relancer avec --apply.")
            return

        reliees = await backfill_player_api_ids(session)
        print(f"Cotes reliées : {reliees}/{cotes}")
        passes_reliees = await backfill_assist_player_ids(session)
        await session.commit()
        print(f"Passes reliées : {passes_reliees}/{passes}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run(main(ap.parse_args().apply))
