"""One-shot : résout les ID club Transfermarkt puis lance UNE réconciliation
complète des effectifs (mode 'daily'). Opérationnel, à exécuter à la main lors
de la mise en service ou d'un besoin de resynchronisation immédiate.

    python -m app.scripts.run_squad_sync_once

Affiche : résolution des clubs (résolus / non appariés), puis le résultat du
run (clubs OK/KO, joueurs mis à jour / détachés, statut). En cas de run
failed/partial, déclenche le garde-fou (issue + PR auto si github_token).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.ingestion.transfermarkt.failure_surface import surface_failure
from app.ingestion.transfermarkt.resolve_clubs import resolve_and_store_club_ids
from app.ingestion.transfermarkt.sync_squads import sync_squads
from app.models.canonical_teams import CanonicalTeam
from app.scripts.transfermarkt_career import TransfermarktClient

logger = logging.getLogger(__name__)


async def _main() -> None:
    client = TransfermarktClient()
    try:
        async with async_session() as session:
            # 1. Ancrage : résout/enregistre transfermarkt_club_id pour les clubs couverts.
            report = await resolve_and_store_club_ids(session, client=client)
            print(
                f"[résolution clubs] {report.resolved} résolus | "
                f"{len(report.unresolved_tm)} TM non appariés | "
                f"{len(report.unmatched_canonical)} canoniques sans id TM"
            )
            if report.unmatched_canonical:
                print("  canoniques sans id TM :", sorted(report.unmatched_canonical)[:30])

            # 2. Clubs à réconcilier (déjà résolus + rattachés à un bzz_team).
            clubs = (
                await session.execute(
                    select(CanonicalTeam).where(
                        CanonicalTeam.transfermarkt_club_id.isnot(None),
                        CanonicalTeam.bzz_team_id.isnot(None),
                    )
                )
            ).scalars().all()
            print(f"[réconciliation] {len(clubs)} clubs")

            # 3. Un run complet (mode 'daily' = passage complet).
            run, samples = await sync_squads(
                session, client, list(clubs), mode="daily", today=date.today()
            )
            print(
                f"[run] status={run.status} clubs_ok={run.clubs_ok} "
                f"clubs_failed={run.clubs_failed} players_updated={run.players_updated} "
                f"players_detached={run.players_detached}"
            )

            # 4. Garde-fou anti-mort-silencieuse.
            if run.status in ("failed", "partial"):
                await surface_failure(run, samples, settings=settings)
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
