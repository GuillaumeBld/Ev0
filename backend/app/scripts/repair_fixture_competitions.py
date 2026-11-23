"""Repare les matchs Bzzoiro ranges dans la mauvaise competition.

    python -m app.scripts.repair_fixture_competitions           # simulation
    python -m app.scripts.repair_fixture_competitions --apply   # ecriture

La verite est `bzz_events.league_api_id` (jointure
`fixtures.external_id = 'bzz_' || bzz_events.api_id`). Pour TOUS les matchs
Bzzoiro on renseigne `competition_api_id` et on recalcule `league`.
Recos : seules celles des matchs FUTURS reclasses sont supprimees, et
jamais celles liees a un pari / bankroll / decision autopilot (historique
et backtest conserves). Les matchs non Bzzoiro ne sont pas touches.
Tout est ecrit dans une seule transaction.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update

from app.services.competitions import league_key_for


@dataclass(frozen=True)
class FixtureRow:
    id: int
    league: str
    competition_api_id: int | None
    bzz_league_api_id: int | None
    kickoff_utc: datetime


@dataclass
class RepairPlan:
    relabel: dict[int, tuple[int, str]] = field(default_factory=dict)
    recos_a_supprimer_fixture_ids: set[int] = field(default_factory=set)
    non_relies: list[int] = field(default_factory=list)


def plan_repair(rows: list[FixtureRow], now: datetime | None = None) -> RepairPlan:
    """Calcule les corrections (pure). Un match deja juste n'apparait pas."""
    now = now or datetime.now(timezone.utc)
    plan = RepairPlan()
    for r in rows:
        if r.bzz_league_api_id is None:
            plan.non_relies.append(r.id)
            continue
        cible = league_key_for(r.bzz_league_api_id)
        if r.competition_api_id == r.bzz_league_api_id and r.league == cible:
            continue
        plan.relabel[r.id] = (r.bzz_league_api_id, cible)
        if r.league != cible and r.kickoff_utc > now:
            plan.recos_a_supprimer_fixture_ids.add(r.id)
    return plan


async def _charger(session) -> list[FixtureRow]:
    from sqlalchemy import String, cast, func

    from app.models.bzzoiro import BzzEvent
    from app.models.fixtures import Fixture

    q = (
        select(
            Fixture.id, Fixture.league, Fixture.competition_api_id,
            BzzEvent.league_api_id, Fixture.kickoff_utc,
        )
        .outerjoin(
            BzzEvent,
            Fixture.external_id == func.concat("bzz_", cast(BzzEvent.api_id, String)),
        )
        .where(Fixture.external_id.like("bzz\\_%", escape="\\"))
    )
    return [FixtureRow(*t) for t in (await session.execute(q)).all()]


async def main(apply: bool) -> None:
    from app.alerts import send_alert
    from app.db import async_session
    from app.models.autopilot import AutopilotDecision
    from app.models.bankroll import BankrollEntry
    from app.models.fixtures import Fixture
    from app.models.recommendations import Recommendation

    async with async_session() as session:
        rows = await _charger(session)
        plan = plan_repair(rows)
        anciennes = {r.id: r.league for r in rows}

        ids = list(plan.recos_a_supprimer_fixture_ids)
        liees = (
            select(BankrollEntry.recommendation_id)
            .where(BankrollEntry.recommendation_id.is_not(None))
            .union(
                select(AutopilotDecision.recommendation_id)
                .where(AutopilotDecision.recommendation_id.is_not(None))
            )
        )
        sur_fixtures = Recommendation.fixture_id.in_(ids)
        nb_a_supprimer = nb_conservees = 0
        if ids:
            nb_a_supprimer = (await session.execute(
                select(func.count()).select_from(Recommendation)
                .where(sur_fixtures, Recommendation.id.not_in(liees))
            )).scalar_one()
            nb_conservees = (await session.execute(
                select(func.count()).select_from(Recommendation)
                .where(sur_fixtures, Recommendation.id.in_(liees))
            )).scalar_one()

        comptes = Counter((anciennes[i], lg) for i, (_, lg) in plan.relabel.items())
        print(f"Matchs Bzzoiro lus : {len(rows)} ; a corriger : {len(plan.relabel)}")
        for (ancien, nouveau), n in sorted(comptes.items()):
            marque = "" if ancien != nouveau else " (colonne competition seulement)"
            print(f"  {ancien} -> {nouveau} : {n}{marque}")
        print(f"Recos a supprimer (matchs futurs reclasses) : {nb_a_supprimer}")
        print(f"Recos conservees car liees a un pari : {nb_conservees}")

        if plan.non_relies:
            msg = (
                f"Reparation calendrier : {len(plan.non_relies)} match(s) Bzzoiro sans "
                f"competition connue, non touches (ids {plan.non_relies[:20]})"
            )
            print(msg)
            await send_alert(msg, channel="incidents")

        if not apply:
            print("Simulation : rien n'a ete ecrit (relancer avec --apply).")
            return

        try:
            nb_supprimees = 0
            if ids:
                # Exclusion evaluee AU MOMENT de la suppression (une reco liee
                # entre-temps par le worker/autopilot n'est jamais supprimee).
                res = await session.execute(
                    delete(Recommendation).where(
                        sur_fixtures, Recommendation.id.not_in(liees)
                    )
                )
                nb_supprimees = res.rowcount
            for fid, (comp, lg) in plan.relabel.items():
                await session.execute(
                    update(Fixture).where(Fixture.id == fid)
                    .values(competition_api_id=comp, league=lg)
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        print(f"Recos supprimees : {nb_supprimees} (simulation : {nb_a_supprimer})")
        if nb_supprimees != nb_a_supprimer:
            print("ATTENTION : ecart entre simulation et suppression reelle "
                  "(des recos ont ete liees ou creees entre-temps).")
        print("Corrections appliquees.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    asyncio.run(main(ap.parse_args().apply))
