"""Competition identity map between the two id spaces used across Ev0's
career-data ingestion: Bzzoiro's internal league id (the key of
`LEAGUE_REGISTRY`, i.e. the same space as `canonical_teams.league_api_id`)
and Transfermarkt's competition code (`competition_code`, e.g. "FR1").

Task 8 (reconciliation) needs to know that a Bzzoiro row with
`league_api_id=6` and a Transfermarkt row with `competition_code="FR1"`
designate the SAME real-world competition (Ligue 1) — without silently
guessing when a TM code doesn't clearly correspond to a tracked Bzzoiro
competition.

Authority for this file's two sides:
- Bzzoiro side: `app.ingestion.bzzoiro.league_registry.LEAGUE_REGISTRY`
  (keyed by Bzzoiro **internal** id — see that module's docstring for the
  warning about the two distinct Bzzoiro id spaces).
- Transfermarkt side: `app.scripts.transfermarkt_career.COMPETITION_NAMES`
  (TM competition code -> human-readable name).

`TM_CODE_TO_LEAGUE` was built by hand-matching competition *identity*
(same real-world competition), never by guessing from naming similarity
alone. Two categories of TM codes are deliberately left OUT of this dict
(resolving to `None`, never a wrong guess):

1. Codes with no corresponding entry in `LEAGUE_REGISTRY` at all (e.g.
   domestic cups like "DFB", "FAC", "CDR", supercups, the FIFA Club World
   Cup "FIWC") — the registry simply doesn't track those competitions.
2. Codes that are ambiguous or edition-mismatched even though a
   same-named registry entry exists:
   - "WM" (World Cup, every edition a player appeared in) vs. registry id
     27 "Coupe du monde 2026" (one specific edition only) — not the same
     identity for any pre-2026 World Cup match.
   - "EM" (European Championship, every edition) vs. registry id 66
     "Euro 2024" (one specific, historical edition only) — same mismatch.
   - "EMQ" ("Euro Qualifying") — the registry has no generic Euro
     Qualifying entry to match against (only the edition-specific "Euro
     2024").
   - "CLQ" ("Champions League - Qualifying") — the registry has no
     separate id for UEFA Champions League qualifying rounds, only the
     main competition (id 7). Whether Bzzoiro tags qualifying matches
     under id 7 too is unverified, so this code is left unmapped rather
     than guessed.
   - "NLQ" ("UEFA Nations League") — left unmapped despite a plausible
     match to registry id 64 ("Nations League — UEFA"): the trailing "Q"
     in the TM code suggests a qualifying-specific phase that may not
     share identity with the main competition, and this was not
     independently confirmed.
"""
from __future__ import annotations

# TM competition code -> Bzzoiro internal league id (LEAGUE_REGISTRY key
# space, NOT any Bzzoiro external api_id). Every value here must be a key
# of `app.ingestion.bzzoiro.league_registry.LEAGUE_REGISTRY`.
TM_CODE_TO_LEAGUE: dict[str, int] = {
    # --- Big-5 top divisions ---
    "GB1": 1,  # Premier League (England)
    "ES1": 3,  # La Liga / LaLiga (Spain)
    "IT1": 4,  # Serie A (Italy)
    "L1": 6,  # Ligue 1 (France) — TM code used for Ligue 1 in this source
    "FR1": 6,  # Ligue 1 (France) — alternate code, same competition
    # --- Other European top divisions with an unambiguous name match ---
    "TR1": 11,  # Süper Lig / "Super Lig" (Turkey)
    "PO1": 2,  # "Primeira Liga" == Liga Portugal (Portugal), same division
    # --- Second divisions (domestic_2) with an unambiguous name match ---
    "GB2": 12,  # Championship (England)
    "L2": 89,  # Ligue 2 (France)
    "FR2": 89,  # Ligue 2 (France) — alternate code, same competition
    # --- UEFA club competitions ---
    "CL": 7,  # Champions League
    "EL": 8,  # Europa League
    "ECL": 83,  # Conference League
    "USC": 90,  # UEFA Super Cup
    # --- National-team qualifiers (not edition-specific, unlike the
    # tournaments themselves) ---
    "WMQ": 58,  # "World Cup Qualifying (UEFA)" == "Qualifs CM — UEFA"
}


def tm_code_to_league_api_id(code: str) -> int | None:
    """Map a Transfermarkt competition code to the Bzzoiro internal league
    id (`LEAGUE_REGISTRY` key space) designating the same competition.

    Returns `None` for any code that is not confidently known to designate
    the same competition as a tracked Bzzoiro league — never a guess.
    """
    return TM_CODE_TO_LEAGUE.get(code)


def same_competition(league_api_id: int, tm_code: str) -> bool:
    """True iff `tm_code` (Transfermarkt) and `league_api_id` (Bzzoiro
    internal id, i.e. a `LEAGUE_REGISTRY` key / `canonical_teams.league_api_id`
    value) designate the same real-world competition.
    """
    return tm_code_to_league_api_id(tm_code) == league_api_id
