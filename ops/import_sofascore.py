"""import_sofascore.py — importe les stats Sofascore dans la DB Ev0.

Exécuter dans le container backend :
    docker exec -e PYTHONPATH=/app <container> python /tmp/import_sofascore.py

Lit /tmp/sofascore_data.json, matche les joueurs par nom normalisé,
patche la dernière snapshot source=average avec les champs Sofascore,
recompute les per-90, commit.
"""

import asyncio
import json
import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select
from app.db import async_session
from app.models.players import Player, PlayerStats

CURRENT_SEASON = "2025-2026"
DATA_PATH = "/tmp/sofascore_data.json"


def norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower().strip()
    n = re.sub(r"\s+", "-", n)
    return n


def parse_entry(raw: dict) -> dict:
    player = raw.get("player", {})
    minutes = raw.get("minutesPlayed", 0) or 0

    def p90(v: int | float) -> float:
        return round((float(v) / minutes) * 90, 4) if minutes > 0 else 0.0

    bcc = raw.get("bigChancesCreated", 0) or 0
    sot = raw.get("shotsOnTarget", 0) or 0
    acc = raw.get("accurateCrosses", 0) or 0
    tc  = raw.get("totalCrosses", 0) or 0
    kp  = raw.get("keyPasses", 0) or 0
    tap = raw.get("touchesInAttackPenaltyArea", 0) or 0
    tb  = raw.get("throughBalls", 0) or 0

    return {
        "sofascore_id": str(player.get("id", "")),
        "name": player.get("name", ""),
        "normalized_name": norm(player.get("name", "")),
        "big_chances_created": bcc,
        "shots_on_target": sot,
        "accurate_crosses": acc,
        "total_crosses": tc,
        "key_passes": kp,
        "touches_attack_pen_area": tap,
        "through_balls": tb,
        "sofascore_rating": raw.get("rating") or None,
        "bcc_per_90": p90(bcc),
        "shots_on_target_per_90": p90(sot),
        "accurate_crosses_per_90": p90(acc),
        "through_balls_per_90": p90(tb),
        "touches_attack_pen_area_per_90": p90(tap),
    }


async def import_league(session, league_key: str, entries: list[dict]) -> int:
    as_of = datetime.now(UTC)
    ss_by_norm = {e["normalized_name"]: e for e in entries}

    result = await session.execute(
        select(Player).where(Player.league == league_key)
    )
    db_players = result.scalars().all()

    updated = 0
    for player in db_players:
        ss = ss_by_norm.get(norm(player.name))
        if not ss:
            continue

        stats_res = await session.execute(
            select(PlayerStats)
            .where(
                PlayerStats.player_id == player.id,
                PlayerStats.source == "average",
                PlayerStats.season == CURRENT_SEASON,
            )
            .order_by(PlayerStats.as_of_utc.desc())
            .limit(1)
        )
        stat = stats_res.scalar_one_or_none()
        if stat is None:
            continue

        stat.shots_on_target         = ss["shots_on_target"]
        stat.touches_attack_pen_area = ss["touches_attack_pen_area"]
        stat.big_chances_created     = ss["big_chances_created"]
        stat.accurate_crosses        = ss["accurate_crosses"]
        stat.total_crosses           = ss["total_crosses"]
        stat.through_balls           = ss["through_balls"]
        stat.key_passes              = ss["key_passes"]
        stat.sofascore_rating        = ss["sofascore_rating"]

        stat.shots_on_target_per_90         = ss["shots_on_target_per_90"]
        stat.touches_attack_pen_area_per_90 = ss["touches_attack_pen_area_per_90"]
        stat.bcc_per_90                     = ss["bcc_per_90"]
        stat.accurate_crosses_per_90        = ss["accurate_crosses_per_90"]
        stat.through_balls_per_90           = ss["through_balls_per_90"]

        stat.as_of_utc = as_of
        updated += 1

    return updated


async def main():
    with open(DATA_PATH) as f:
        raw_data = json.load(f)

    print(f"Chargé : {DATA_PATH}")
    for key, entries in raw_data.items():
        print(f"  {key}: {len(entries)} records Sofascore")

    total = 0
    async with async_session() as session:
        for key, entries in raw_data.items():
            if not entries:
                continue
            rows = [parse_entry(e) for e in entries]
            n = await import_league(session, key, rows)
            print(f"{key}: {n} joueurs mis à jour")
            total += n
        await session.commit()

    print(f"\nTerminé — {total} joueurs mis à jour")


asyncio.run(main())
