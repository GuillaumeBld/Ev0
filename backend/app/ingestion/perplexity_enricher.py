"""Perplexity sonar-pro enricher — real-time pre-match context for player recs.

Queries Perplexity once per fixture (not per player) to check:
  - Is the player confirmed to start?
  - Any injury / suspension concern?
  - Recent scoring form?

The returned context_score is a confidence multiplier (0.1–1.2):
  - confirmed starter, good form  → 1.2 (slight boost)
  - confirmed starter, average    → 1.0 (no change)
  - injury concern                → 0.7 (penalty)
  - confirmed NOT starting        → 0.1 (effectively kills the rec)
  - unknown                       → 0.85 (mild uncertainty discount)
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar-pro"


async def enrich_fixture_context(
    home_team: str,
    away_team: str,
    kickoff_date: str,  # "YYYY-MM-DD"
    players: list[dict],  # [{"name": str, "team": str, "market": str}]
) -> dict[str, dict]:
    """Call Perplexity for pre-match player availability context.

    Returns a dict keyed by exact player name (as supplied):
    {
        "player_name": {
            "confirmed_starter": True | False | None,
            "injury_risk": bool,
            "recent_form": "good" | "average" | "poor",
            "context_score": float,   # confidence multiplier 0.1–1.2
            "notes": str,
        }
    }
    Returns {} if the API key is missing or the call fails.
    """
    api_key = getattr(settings, "perplexity_api_key", None)
    if not api_key:
        logger.debug("PERPLEXITY_API_KEY not set — skipping enrichment")
        return {}

    if not players:
        return {}

    player_lines = "\n".join(
        f'- "{p["name"]}" ({p.get("team", "?")})'
        for p in players
    )

    prompt = (
        f"Football pre-match analysis for {home_team} vs {away_team} on {kickoff_date}.\n\n"
        f"For each player below, answer:\n"
        f"1. Is the player confirmed to START (not injured, not suspended)?\n"
        f"2. Any injury or fitness concern?\n"
        f"3. Recent form: scored or assisted in last 3 matches?\n\n"
        f"Players:\n{player_lines}\n\n"
        f"Reply ONLY with valid JSON using the EXACT player names as keys:\n"
        f'{{\n'
        f'  "players": {{\n'
        f'    "<exact player name>": {{\n'
        f'      "confirmed_starter": true | false | null,\n'
        f'      "injury_risk": true | false,\n'
        f'      "recent_form": "good" | "average" | "poor",\n'
        f'      "notes": "<one sentence>"\n'
        f'    }}\n'
        f'  }},\n'
        f'  "match_context": "<one sentence on scoring conditions>"\n'
        f'}}\n\n'
        f"Use null for confirmed_starter if you cannot find reliable information."
    )

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            r = await client.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 900,
                    "temperature": 0.1,
                },
            )
            r.raise_for_status()
            raw_content = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning(
            "Perplexity API error for %s vs %s: %s", home_team, away_team, exc
        )
        return {}

    data = _parse_json(raw_content)
    if not data:
        return {}

    raw_players: dict = data.get("players", {})
    match_context: str = data.get("match_context", "")

    result: dict[str, dict] = {}
    for player in players:
        name = player["name"]
        # Try exact match first, then case-insensitive, then partial
        info = raw_players.get(name) or _fuzzy_get(raw_players, name)
        if not info:
            result[name] = _unknown_context()
            continue

        confirmed = info.get("confirmed_starter")
        injury = bool(info.get("injury_risk", False))
        form = str(info.get("recent_form", "average")).lower()
        notes = str(info.get("notes", ""))

        score = _context_score(confirmed, injury, form)
        result[name] = {
            "confirmed_starter": confirmed,
            "injury_risk": injury,
            "recent_form": form,
            "context_score": score,
            "notes": notes,
            "match_context": match_context,
        }

    logger.info(
        "Perplexity enriched %d players for %s vs %s",
        len(result), home_team, away_team,
    )
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────


def _context_score(confirmed: bool | None, injury: bool, form: str) -> float:
    """Convert availability + form into a confidence multiplier."""
    if confirmed is False:
        return 0.1  # not starting — effectively kills the rec
    if injury:
        return 0.65  # injury concern
    if confirmed is True:
        if form == "good":
            return 1.20  # confirmed starter in form → boost
        return 1.00  # confirmed starter, average form
    # confirmed is None (unknown)
    if form == "good":
        return 0.95
    return 0.85


def _unknown_context() -> dict:
    return {
        "confirmed_starter": None,
        "injury_risk": False,
        "recent_form": "average",
        "context_score": 0.85,
        "notes": "No information found",
        "match_context": "",
    }


def _parse_json(text: str) -> dict | None:
    """Extract JSON from Perplexity response (handles markdown fences)."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: find first { ... } block
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.debug("Could not parse Perplexity JSON: %s", text[:200])
    return None


def _fuzzy_get(d: dict, name: str) -> dict | None:
    """Case-insensitive + partial name match against dict keys."""
    name_lower = name.lower()
    for key, val in d.items():
        key_lower = key.lower()
        if key_lower == name_lower:
            return val
        # Last-name match (e.g. "Gomes" in "Rodrigo Martins Gomes")
        if key_lower in name_lower or name_lower in key_lower:
            return val
    return None
