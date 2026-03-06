"""FPL (Fantasy Premier League) client — free Opta-powered stats for PL players.

Endpoints:
  GET https://fantasy.premierleague.com/api/bootstrap-static/
    → all players, season totals, teams

  GET https://fantasy.premierleague.com/api/element-summary/{id}/
    → per-gameweek history for one player
"""

from __future__ import annotations

import re
import unicodedata

import httpx

_BASE = "https://fantasy.premierleague.com/api"
_TIMEOUT = 20.0

_POSITION_MAP = {1: "GK", 2: "DEF", 3: "MID", 4: "FW"}


def _normalize(name: str) -> str:
    """Lowercase, strip accents, keep only alphanum + spaces."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", "", ascii_name.lower()).strip()


class FPLClient:
    """Thin async wrapper around the FPL public API."""

    async def get_all_players(self) -> list[dict]:
        """Return season-total stats for all ~820 PL players."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/bootstrap-static/")
            r.raise_for_status()
            data = r.json()

        teams = {t["id"]: t["name"] for t in data["teams"]}
        out = []
        for el in data["elements"]:
            minutes = el.get("minutes", 0)
            xg = float(el.get("expected_goals", 0) or 0)
            xa = float(el.get("expected_assists", 0) or 0)
            out.append(
                {
                    "fpl_id": el["id"],
                    "full_name": f"{el['first_name']} {el['second_name']}",
                    "normalized_name": _normalize(
                        f"{el['first_name']} {el['second_name']}"
                    ),
                    "team": teams.get(el["team"], ""),
                    "position": _POSITION_MAP.get(el["element_type"], "MID"),
                    "minutes": minutes,
                    "goals": el.get("goals_scored", 0),
                    "assists": el.get("assists", 0),
                    "xg": xg,
                    "xa": xa,
                    "xg_per_90": round(xg / minutes * 90, 4) if minutes >= 90 else None,
                    "xa_per_90": round(xa / minutes * 90, 4) if minutes >= 90 else None,
                    "form": float(el.get("form", 0) or 0),
                    "ict_index": float(el.get("ict_index", 0) or 0),
                    "selected_by_pct": float(el.get("selected_by_percent", 0) or 0),
                }
            )
        return out

    async def get_player_history(self, fpl_id: int) -> list[dict]:
        """Return per-gameweek history for a single player."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/element-summary/{fpl_id}/")
            r.raise_for_status()
            data = r.json()

        out = []
        for gw in data.get("history", []):
            out.append(
                {
                    "gameweek": gw.get("round"),
                    "kickoff_time": gw.get("kickoff_time"),
                    "minutes": gw.get("minutes", 0),
                    "goals": gw.get("goals_scored", 0),
                    "assists": gw.get("assists", 0),
                    "xg": float(gw.get("expected_goals", 0) or 0),
                    "xa": float(gw.get("expected_assists", 0) or 0),
                    "bonus": gw.get("bonus", 0),
                    "ict_index": float(gw.get("ict_index", 0) or 0),
                    "was_home": gw.get("was_home", False),
                    "opponent_team": gw.get("opponent_team"),
                }
            )
        return out

    async def get_recent_form(self, fpl_id: int, last_n: int = 5) -> dict:
        """Aggregated stats over last N gameweeks with >0 minutes."""
        history = await self.get_player_history(fpl_id)
        active = [gw for gw in history if gw["minutes"] > 0]
        recent = active[-last_n:] if len(active) >= last_n else active
        if not recent:
            return {}
        total_mins = sum(gw["minutes"] for gw in recent)
        return {
            "games": len(recent),
            "minutes": total_mins,
            "goals": sum(gw["goals"] for gw in recent),
            "assists": sum(gw["assists"] for gw in recent),
            "xg": sum(gw["xg"] for gw in recent),
            "xa": sum(gw["xa"] for gw in recent),
            "xg_per_90": sum(gw["xg"] for gw in recent) / total_mins * 90
            if total_mins > 0
            else 0.0,
            "xa_per_90": sum(gw["xa"] for gw in recent) / total_mins * 90
            if total_mins > 0
            else 0.0,
        }
