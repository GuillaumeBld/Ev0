from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.pricing.sub_constants import P_SUB_DEFAULT, T_SUB_DEFAULT


@dataclass
class SubStats:
    p_sub: float
    avg_sub_time: float


async def get_player_sub_stats(
    player_name: str,
    position: str,
    db: AsyncSession,
    n_matches: int = 20,
) -> SubStats:
    """
    Calcule p_sub et avg_sub_time depuis les n derniers matchs du joueur.
    Un joueur est considéré remplacé si minutes_played < 85.
    Fallback sur les defaults positionnels si données insuffisantes.

    Source: table player_match_minutes (minutes jouées par fixture, depuis Understat).
    """
    rows = (await db.execute(
        text("""
            SELECT minutes_played
            FROM player_match_minutes
            WHERE player_name = :name
              AND minutes_played > 0
            ORDER BY fixture_id DESC
            LIMIT :n
        """),
        {"name": player_name, "n": n_matches},
    )).fetchall()

    if not rows:
        return SubStats(
            p_sub=P_SUB_DEFAULT.get(position, 0.35),
            avg_sub_time=T_SUB_DEFAULT,
        )

    subbed = [r.minutes_played for r in rows if r.minutes_played < 85]
    p_sub = len(subbed) / len(rows)
    avg_sub_time = (sum(subbed) / len(subbed)) if subbed else T_SUB_DEFAULT

    return SubStats(p_sub=p_sub, avg_sub_time=avg_sub_time)
