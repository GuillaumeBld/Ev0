"""LEAGUE_REGISTRY — single source of truth for the ~57 competitions tracked
by the teams/players database expansion (Task 1 of the expansion plan).

ID SPACE — IMPORTANT: the key of LEAGUE_REGISTRY is the Bzzoiro **internal id**
(espace A), the same id space already used by TARGET_LEAGUE_INTERNAL_IDS,
EFFECTIFS_REGLEMENTAIRES (see constants.py) and CanonicalTeam.league_api_id
elsewhere in this codebase — despite the "api_id" naming inherited from
those older modules, those columns/dicts actually carry internal ids, not
Bzzoiro's external api_id. See constants.py's module docstring for the full
explanation of the two distinct Bzzoiro id spaces; this module stays in the
same (internal/espace A) space to avoid yet another silent mismatch.

Every value below is transcribed verbatim from the validated registry data
(.superpowers/sdd/task-1-registry-data.md) — nothing here is invented or
estimated. If a real-world count differs from `expected_count`, Task 2/7
flags the discrepancy (never silently) and the fix is to adjust this single
number, not to guess around it.

Field semantics
----------------
- `tier`: competition tier. `1..4` for every competition that is NOT a
  national-team "selection" (1 = top domestic division, 2 = UEFA club
  competition, 3 = second domestic division, 4 = other major world
  competition). The literal string `"selection"` for every national-team
  competition (World Cup, qualifiers, Nations League, continental cups) —
  they don't rank a club by tier, hence the dedicated sentinel.
- `kind`: `domestic_1 | domestic_2 | uefa_club | world | selection`.
- `format`: `round_robin | groups | knockout`.
- `expected_count`: **non-None if and only if `format == "round_robin"`**.
  This is the structural invariant `tests/test_league_registry.py` enforces
  — it's also the ground Task 2's strict segmentation guard stands on, so it
  must never be violated here. `None` for every `groups`/`knockout` entry.
- `country`: the competition's country. `None` for the four UEFA club
  competitions (7 Champions League, 8 Europa League, 83 Conference League,
  90 UEFA Super Cup) and for every "selection" entry — none of them rank a
  club by country, and the two CONMEBOL club cups (32, 33) are continental,
  not national, despite being club competitions.
- `rank`: global importance ordering, ascending, already assigned by the
  registry data (1..57, no gaps, no repeats).
"""
from __future__ import annotations

from dataclasses import dataclass

# Kinds a competition can have.
KIND_DOMESTIC_1 = "domestic_1"
KIND_DOMESTIC_2 = "domestic_2"
KIND_UEFA_CLUB = "uefa_club"
KIND_WORLD = "world"
KIND_SELECTION = "selection"

VALID_KINDS = frozenset(
    {KIND_DOMESTIC_1, KIND_DOMESTIC_2, KIND_UEFA_CLUB, KIND_WORLD, KIND_SELECTION}
)

# Formats a competition's season can take.
FORMAT_ROUND_ROBIN = "round_robin"
FORMAT_GROUPS = "groups"
FORMAT_KNOCKOUT = "knockout"

VALID_FORMATS = frozenset({FORMAT_ROUND_ROBIN, FORMAT_GROUPS, FORMAT_KNOCKOUT})

# Sentinel tier for national-team competitions (see module docstring).
TIER_SELECTION = "selection"


@dataclass(frozen=True)
class LeagueMeta:
    """Static metadata for one competition, keyed by Bzzoiro internal id."""

    name: str
    country: str | None
    tier: int | str  # 1..4, or TIER_SELECTION ("selection")
    kind: str  # one of VALID_KINDS
    format: str  # one of VALID_FORMATS
    expected_count: int | None  # non-None iff format == FORMAT_ROUND_ROBIN
    rank: int


# ---------------------------------------------------------------------------
# Tier 1 — 1res divisions europeennes (domestic_1, round_robin, strict)
# ---------------------------------------------------------------------------
_TIER_1: dict[int, LeagueMeta] = {
    1: LeagueMeta("Premier League", "England", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 20, 1),
    3: LeagueMeta("La Liga", "Spain", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 20, 2),
    4: LeagueMeta("Serie A", "Italy", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 20, 3),
    5: LeagueMeta("Bundesliga", "Germany", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 18, 4),
    6: LeagueMeta("Ligue 1", "France", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 18, 5),
    2: LeagueMeta("Liga Portugal", "Portugal", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 18, 6),
    10: LeagueMeta("Eredivisie", "Netherlands", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 18, 7),
    11: LeagueMeta("Süper Lig", "Turkey", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 18, 8),
    14: LeagueMeta("Pro League", "Belgium", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 18, 9),
    15: LeagueMeta("Super League", "Switzerland", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 12, 10),
    13: LeagueMeta("Scottish Premiership", "Scotland", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 12, 11),
    24: LeagueMeta("Super League Greece", "Greece", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 14, 12),
    25: LeagueMeta("Ekstraklasa", "Poland", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 18, 13),
    23: LeagueMeta("Superliga", "Romania", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 16, 14),
    54: LeagueMeta("Eliteserien", "Norway", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 16, 15),
    26: LeagueMeta("Allsvenskan", "Sweden", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 16, 16),
    84: LeagueMeta("Superliga", "Denmark", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 12, 17),
    99: LeagueMeta("Czech First League", "Czechia", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 16, 18),
    22: LeagueMeta("Parva Liga", "Bulgaria", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 14, 19),
    55: LeagueMeta("Veikkausliiga", "Finland", 1, KIND_DOMESTIC_1, FORMAT_ROUND_ROBIN, 13, 20),
}

# ---------------------------------------------------------------------------
# Tier 2 — competitions UEFA clubs (uefa_club, country=None)
# ---------------------------------------------------------------------------
_TIER_2: dict[int, LeagueMeta] = {
    7: LeagueMeta("Champions League", None, 2, KIND_UEFA_CLUB, FORMAT_GROUPS, None, 21),
    8: LeagueMeta("Europa League", None, 2, KIND_UEFA_CLUB, FORMAT_GROUPS, None, 22),
    83: LeagueMeta("Conference League", None, 2, KIND_UEFA_CLUB, FORMAT_GROUPS, None, 23),
    90: LeagueMeta("UEFA Super Cup", None, 2, KIND_UEFA_CLUB, FORMAT_KNOCKOUT, None, 24),
}

# ---------------------------------------------------------------------------
# Tier 3 — 2es divisions europeennes (domestic_2, round_robin, strict)
# ---------------------------------------------------------------------------
_TIER_3: dict[int, LeagueMeta] = {
    12: LeagueMeta("Championship", "England", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 24, 25),
    38: LeagueMeta("Segunda División", "Spain", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 22, 26),
    100: LeagueMeta("Serie B", "Italy", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 20, 27),
    94: LeagueMeta("2. Bundesliga", "Germany", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 18, 28),
    89: LeagueMeta("Ligue 2", "France", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 18, 29),
    88: LeagueMeta("Liga Portugal 2", "Portugal", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 18, 30),
    101: LeagueMeta("Serie C Girone A", "Italy", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 20, 31),
    102: LeagueMeta("Serie C Girone B", "Italy", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 20, 32),
    103: LeagueMeta("Serie C Girone C", "Italy", 3, KIND_DOMESTIC_2, FORMAT_ROUND_ROBIN, 20, 33),
}

# ---------------------------------------------------------------------------
# Tier 4 — grands championnats mondiaux (world)
# ---------------------------------------------------------------------------
_TIER_4: dict[int, LeagueMeta] = {
    9: LeagueMeta("Brasileirão Série A", "Brazil", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 20, 34),
    17: LeagueMeta("Saudi Pro League", "Saudi Arabia", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 18, 35),
    18: LeagueMeta("Major League Soccer", "USA", 4, KIND_WORLD, FORMAT_GROUPS, None, 36),
    19: LeagueMeta("Liga MX Apertura", "Mexico", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 18, 37),
    20: LeagueMeta("Liga MX Clausura", "Mexico", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 18, 38),
    49: LeagueMeta("J1 League", "Japan", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 20, 39),
    50: LeagueMeta("K League 1", "South Korea", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 12, 40),
    52: LeagueMeta("Chinese Super League", "China", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 16, 41),
    53: LeagueMeta("Botola Pro", "Morocco", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 16, 42),
    47: LeagueMeta("Ligue 1 Tunisie", "Tunisia", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 16, 43),
    80: LeagueMeta("Primera A", "Colombia", 4, KIND_WORLD, FORMAT_GROUPS, None, 44),
    85: LeagueMeta("Liga Profesional", "Argentina", 4, KIND_WORLD, FORMAT_GROUPS, None, 45),
    32: LeagueMeta("Copa Libertadores", None, 4, KIND_WORLD, FORMAT_GROUPS, None, 46),
    33: LeagueMeta("Copa Sudamericana", None, 4, KIND_WORLD, FORMAT_GROUPS, None, 47),
    104: LeagueMeta("A-League Men", "Australia", 4, KIND_WORLD, FORMAT_ROUND_ROBIN, 13, 48),
}

# ---------------------------------------------------------------------------
# Selections (selection, country=None, tier="selection")
# ---------------------------------------------------------------------------
_SELECTIONS: dict[int, LeagueMeta] = {
    27: LeagueMeta("Coupe du monde 2026", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 49),
    58: LeagueMeta("Qualifs CM — UEFA", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 50),
    59: LeagueMeta("Qualifs CM — CONMEBOL", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 51),
    62: LeagueMeta("Qualifs CM — CONCACAF", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 52),
    63: LeagueMeta("Qualifs CM — OFC", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 53),
    64: LeagueMeta("Nations League — UEFA", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 54),
    65: LeagueMeta("Nations League — CONCACAF", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 55),
    66: LeagueMeta("Euro 2024", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 56),
    67: LeagueMeta("Copa América 2024", None, TIER_SELECTION, KIND_SELECTION, FORMAT_GROUPS, None, 57),
}

LEAGUE_REGISTRY: dict[int, LeagueMeta] = {
    **_TIER_1,
    **_TIER_2,
    **_TIER_3,
    **_TIER_4,
    **_SELECTIONS,
}


def _validate_registry(registry: dict[int, LeagueMeta]) -> None:
    """Fail loudly at import time if the registry's own invariants break.

    No silent corruption: a future edit that violates the round_robin /
    expected_count invariant, or re-introduces a duplicate rank, must crash
    on import — not surface three steps later as a wrong club count.
    """
    if len(registry) != 57:
        raise ValueError(f"LEAGUE_REGISTRY must have 57 entries, got {len(registry)}")

    ranks = sorted(meta.rank for meta in registry.values())
    if ranks != list(range(1, len(registry) + 1)):
        raise ValueError("LEAGUE_REGISTRY ranks must be unique and cover 1..57 with no gaps")

    for league_id, meta in registry.items():
        if meta.kind not in VALID_KINDS:
            raise ValueError(f"league {league_id}: invalid kind {meta.kind!r}")
        if meta.format not in VALID_FORMATS:
            raise ValueError(f"league {league_id}: invalid format {meta.format!r}")
        has_count = meta.expected_count is not None
        is_round_robin = meta.format == FORMAT_ROUND_ROBIN
        if has_count != is_round_robin:
            raise ValueError(
                f"league {league_id} ({meta.name}): expected_count must be set "
                f"if and only if format == round_robin (format={meta.format!r}, "
                f"expected_count={meta.expected_count!r})"
            )
        if meta.kind == KIND_SELECTION and meta.tier != TIER_SELECTION:
            raise ValueError(f"league {league_id}: selection kind must have tier == 'selection'")
        if meta.kind != KIND_SELECTION and meta.tier == TIER_SELECTION:
            raise ValueError(f"league {league_id}: non-selection kind must not have tier == 'selection'")


_validate_registry(LEAGUE_REGISTRY)


# ---------------------------------------------------------------------------
# Derived id lists — Task 3 (ingestion stats joueurs sur tout le perimetre).
#
# Computed once here so every ingestion module (sync_player_stats, aggregate,
# backfill_player_stats, ...) shares the exact same list instead of each
# rebuilding its own subset of LEAGUE_REGISTRY and silently drifting apart.
# ---------------------------------------------------------------------------

# Every competition tracked by the registry, in insertion order (Tier 1 ->
# Tier 2 -> Tier 3 -> Tier 4 -> Selections). 57 ids.
ALL_LEAGUE_IDS: list[int] = list(LEAGUE_REGISTRY)

# Euro 2024 (66) and Copa America 2024 (67) are *past* national-team
# tournaments: there is nothing left to sync recurringly, only a one-shot
# historical backfill — which Task 6 owns explicitly. Keeping them out of the
# default player-stats scope means no module in this task ever tries to
# "keep up to date" a tournament that ended over a year ago.
HISTORICAL_SELECTION_LEAGUE_IDS: frozenset[int] = frozenset({66, 67})

# Default scope for the player-stats ingestion chain (events -> per-match
# stats -> season aggregation): every registry competition except the
# historical one-off tournaments reserved for Task 6's dedicated backfill.
# sync_player_stats() has no explicit league-list parameter at all — it
# filters locally-synced bzz_events against this module-level constant
# directly (see sync_player_stats.py's _ALL_LEAGUE_IDS). aggregate_all_leagues()
# and backfill_player_stats.backfill() DO take an explicit `league_ids` /
# `leagues` override, for callers that need a different scope than their own
# default (e.g. the Task 6 backfill for 66/67, or an opt-in full-registry run).
PLAYER_STATS_LEAGUE_IDS: list[int] = [
    league_id for league_id in ALL_LEAGUE_IDS
    if league_id not in HISTORICAL_SELECTION_LEAGUE_IDS
]


def seed_rows() -> list[dict]:
    """Return LEAGUE_REGISTRY as plain row dicts, ready for a bulk insert into
    the `leagues` table (see app/models/leagues.py and the Task 1 migration).

    `tier` is serialized to its string form (`"1"`..`"4"` or `"selection"`)
    because the `leagues.tier` column is a single VARCHAR holding both the
    int tiers and the "selection" sentinel — see app/models/leagues.py for
    why a single string column was chosen over a nullable int + flag.
    """
    return [
        {
            "id": league_id,
            "name": meta.name,
            "country": meta.country,
            "tier": str(meta.tier),
            "kind": meta.kind,
            "format": meta.format,
            "expected_count": meta.expected_count,
            "rank": meta.rank,
        }
        for league_id, meta in LEAGUE_REGISTRY.items()
    ]
