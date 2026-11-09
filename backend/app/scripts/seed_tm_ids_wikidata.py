"""Jalon 3/5 (contournement rate-limit Transfermarkt) : alimente
`player_tm_ids` (source='wikidata') depuis Wikidata.

    python -m app.scripts.seed_tm_ids_wikidata

Recupere la totalite du jeu Wikidata (joueur Transfermarkt-id + dob) via
`fetch_wikidata_players`, puis apparie/persiste via `match_and_store`
(nom normalise + date de naissance exacte uniquement ; aucune devinette,
'squad' jamais ecrase, voir docstrings de `app.ingestion.wikidata_tm_id`).
"""
from __future__ import annotations

import asyncio
import logging

from app.db import async_session
from app.ingestion.wikidata_tm_id import fetch_wikidata_players, match_and_store

logger = logging.getLogger(__name__)


async def main() -> None:
    rows = await fetch_wikidata_players()
    print(f"[wikidata] {len(rows)} lignes recuperees")

    async with async_session() as session:
        stats = await match_and_store(session, rows)

    print(
        f"[wikidata] matched={stats['matched']} "
        f"skipped_no_dob={stats['skipped_no_dob']} "
        f"skipped_ambiguous={stats['skipped_ambiguous']} "
        f"skipped_squad_exists={stats['skipped_squad_exists']}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
