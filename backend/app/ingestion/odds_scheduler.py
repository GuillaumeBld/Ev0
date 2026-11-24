# backend/app/ingestion/odds_scheduler.py
"""OddsScheduler — adaptive scraping frequency based on time-to-KO.

Frequency table:
  > 6h before KO   : every 2h   (7200s)
  2h–6h before KO  : every 30m  (1800s)
  5min–2h before KO: every 2min (120s)
  < 5min before KO : stop
  after KO         : stop
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Thresholds
_STOP_BEFORE_KO = timedelta(minutes=5)
_HIGH_FREQ_THRESHOLD = timedelta(hours=2)
_MID_FREQ_THRESHOLD = timedelta(hours=6)

# Intervals (seconds)
_INTERVAL_HIGH = 120    # 2min
_INTERVAL_MID = 1800    # 30min
_INTERVAL_LOW = 7200    # 2h


def scrape_interval_seconds(kickoff_utc: datetime) -> int:
    """Return the required scrape interval in seconds for a given KO time."""
    now = datetime.now(UTC)
    if kickoff_utc.tzinfo is None:
        kickoff_utc = kickoff_utc.replace(tzinfo=UTC)
    delta = kickoff_utc - now
    if delta <= _HIGH_FREQ_THRESHOLD:
        return _INTERVAL_HIGH
    if delta <= _MID_FREQ_THRESHOLD:
        return _INTERVAL_MID
    return _INTERVAL_LOW


def should_scrape(kickoff_utc: datetime, last_scraped_at: datetime | None) -> bool:
    """Return True if this fixture is due for a scrape."""
    now = datetime.now(UTC)
    if kickoff_utc.tzinfo is None:
        kickoff_utc = kickoff_utc.replace(tzinfo=UTC)
    delta = kickoff_utc - now
    # Stop window: < 5min before KO, or past KO
    if delta <= _STOP_BEFORE_KO:
        return False
    # Never scraped → scrape now
    if last_scraped_at is None:
        return True
    if last_scraped_at.tzinfo is None:
        last_scraped_at = last_scraped_at.replace(tzinfo=UTC)
    interval = scrape_interval_seconds(kickoff_utc)
    return (now - last_scraped_at).total_seconds() >= interval


# Alias table: maps scraper-normalized names → DB-normalized names.
# Both sides go through _normalize_team (accents stripped, ['.,-] → space,
# lowercase, whitespace collapsed). Alias VALUES must therefore match what
# the DB team name produces after that same normalization.
# e.g. DB "Paris Saint-Germain" → normalized "paris saint germain" (hyphen stripped)
#      DB "Bayern München"      → normalized "bayern munchen"
_TEAM_ALIASES: dict[str, str] = {
    # ── Ligue 1 ───────────────────────────────────────────────────────────────
    # DB "Paris Saint-Germain" → "paris saint germain"
    "paris sg":                 "paris saint germain",
    "psg":                      "paris saint germain",

    # ── Bundesliga ────────────────────────────────────────────────────────────
    # DB "Bayern München" → "bayern munchen"
    "bayern munich":            "bayern munchen",
    "fc bayern munich":         "bayern munchen",
    "fc bayern munchen":        "bayern munchen",
    # DB "1. FC Köln" → "1 fc koln"
    "cologne":                  "1 fc koln",
    "fc koln":                  "1 fc koln",
    "1 fc cologne":             "1 fc koln",
    # DB "Hamburger SV" → "hamburger sv"
    "hambourg":                 "hamburger sv",
    # DB "Mainz 05" → "mainz 05"
    "mayence":                  "mainz 05",
    "1 fsv mayence 05":         "mainz 05",
    # DB "Freiburg" → "freiburg"
    "fribourg":                 "freiburg",
    "fribourg sc":              "freiburg",
    "sc freiburg":              "freiburg",
    # DB "Borussia Mönchengladbach" → "borussia monchengladbach"
    "m gladbach":               "borussia monchengladbach",
    "mgladbach":                "borussia monchengladbach",
    "borussia mgladbach":       "borussia monchengladbach",
    # DB "Bayer Leverkusen" → "bayer leverkusen"
    "leverkusen":               "bayer leverkusen",
    "bayer 04 leverkusen":      "bayer leverkusen",
    # DB "Borussia Dortmund" → "borussia dortmund"
    "dortmund":                 "borussia dortmund",
    # DB "VfB Stuttgart" → "vfb stuttgart"
    "stuttgart":                "vfb stuttgart",
    # DB "Eintracht Frankfurt" → "eintracht frankfurt"
    "ein francfort":            "eintracht frankfurt",
    "eintr francfort":          "eintracht frankfurt",
    # DB "Augsburg" → "augsburg"
    "augsbourg":                "augsburg",
    # DB "Wolfsburg" → "wolfsburg"
    "wolfsbourg":               "wolfsburg",
    # DB "Werder Bremen" → "werder bremen"
    # Unibet "Werder Brême" → normalize → "werder breme"
    "werder breme":             "werder bremen",
    "sv werder bremen":         "werder bremen",
    # DB "FC Heidenheim" → "fc heidenheim"
    "heidenheim":               "fc heidenheim",
    "1 fc heidenheim":          "fc heidenheim",
    "1 fc heidenheim 1846":     "fc heidenheim",
    # DB "RB Leipzig" → "rb leipzig"
    "rasenballsport leipzig":   "rb leipzig",

    # ── Serie A ───────────────────────────────────────────────────────────────
    # DB "Napoli" → "napoli"
    "naples":                   "napoli",
    # DB "Roma" → "roma"
    "as rome":                  "roma",
    "as roma":                  "roma",
    # DB "Milan" → "milan"
    "milan ac":                 "milan",
    "ac milan":                 "milan",
    # DB "Inter" → "inter"
    "inter milan":              "inter",
    "fc inter":                 "inter",
    # DB "Juventus" → "juventus"
    "juventus turin":           "juventus",
    # DB "Bologna" → "bologna"
    "bologne":                  "bologna",
    "bologne fc":               "bologna",
    # DB "Parma" → "parma"
    "parme":                    "parma",
    # DB "Como" → "como"
    "como 1907":                "como",
    "come":                     "como",   # Betclic "Côme" → normalize → "come"
    # DB "Hellas Verona" → "hellas verona"
    "hellas verone":            "hellas verona",
    # DB "Lazio" → "lazio"
    "lazio rome":               "lazio",
    "lazio roma":               "lazio",
    # DB "Cremonese" → "cremonese"
    "us cremonese":             "cremonese",
    # DB "Pisa" → "pisa"
    "ac pisa 1909":             "pisa",
    # DB "Fiorentina" → "fiorentina"
    "acf fiorentina":           "fiorentina",
    "florence":                 "fiorentina",

    # ── Premier League ────────────────────────────────────────────────────────
    # DB "Newcastle United" → "newcastle united"
    "newcastle":                "newcastle united",
    # DB "Wolverhampton Wanderers" → "wolverhampton wanderers"
    "wolverhampton":            "wolverhampton wanderers",
    "wolves":                   "wolverhampton wanderers",
    # DB "Manchester City" → "manchester city"
    "man city":                 "manchester city",
    # DB "Manchester United" → "manchester united"
    "man united":               "manchester united",
    "man utd":                  "manchester united",
    # DB "AFC Bournemouth" → "afc bournemouth"
    "bournemouth":              "afc bournemouth",
    # DB "Brighton & Hove Albion" → "brighton & hove albion"
    "brighton hove":            "brighton & hove albion",
    "brighton":                 "brighton & hove albion",
    # DB "Tottenham Hotspur" → "tottenham hotspur"
    "tottenham":                "tottenham hotspur",
    "spurs":                    "tottenham hotspur",
    # DB "Nottingham Forest" → "nottingham forest"
    "nottingham f":             "nottingham forest",
    "nott m forest":            "nottingham forest",  # "Nott'm Forest" → "nott m forest"
    # DB "Leeds United" → "leeds united"
    "leeds utd":                "leeds united",
    # DB "West Ham United" → "west ham united"
    "west ham":                 "west ham united",
    # DB "Sheffield United" → "sheffield united"
    "sheffield utd":            "sheffield united",

    # ── La Liga ───────────────────────────────────────────────────────────────
    # DB "Deportivo de A Coruña" → "deportivo de a coruna"
    "la corogne":               "deportivo de a coruna",
    "deportivo a corogne":      "deportivo de a coruna",
    "deportivo la corogne":     "deportivo de a coruna",
    # DB "Real Racing Club" → "real racing club"
    "santander":                "real racing club",
    "racing santander":         "real racing club",
    # DB "Mallorca" → "mallorca"
    "majorque":                 "mallorca",
    "rcd majorque":             "mallorca",
    # DB "Athletic Club" → "athletic club"
    "ath bilbao":               "athletic club",
    "athletic bilbao":          "athletic club",
    # DB "Atletico Madrid" → "atletico madrid"
    "atl madrid":               "atletico madrid",
    "atletico de madrid":       "atletico madrid",
    # DB "Real Betis" → "real betis"
    "betis":                    "real betis",   # Betclic short form
    "betis seville":            "real betis",
    "r betis":                  "real betis",
    # DB "Sevilla" → "sevilla"
    "seville":                  "sevilla",       # Betclic FR: "Séville" → "seville"
    "fc seville":               "sevilla",
    "sevilla fc":               "sevilla",
    # DB "Valencia" → "valencia"
    "valence":                  "valencia",      # Betclic FR: "Valence" → "valence"
    "cf valence":               "valencia",
    "valence cf":               "valencia",
    # DB "Barcelona" → "barcelona"
    "fc barcelone":             "barcelona",
    "barcelone":                "barcelona",
    "fc barcelona":             "barcelona",
    # DB "Deportivo Alaves" → "deportivo alaves"
    "alaves":                   "deportivo alaves",
    # DB "Girona" → "girona"
    "gerone":                   "girona",
    "girona fc":                "girona",
    # DB "Getafe" → "getafe"
    "getafe cf":                "getafe",
    # DB "Villarreal" → "villarreal"
    "villarreal cf":            "villarreal",
    # DB "Real Madrid" → "real madrid"
    "real madrid cf":           "real madrid",

    # ── Bundesliga (noms FR Betclic) ──────────────────────────────────────────
    # DB "Eintracht Frankfurt" → "eintracht frankfurt"
    # Betclic FR "Eintracht Francfort" → "eintracht francfort"
    "eintracht francfort":      "eintracht frankfurt",
    # DB "Borussia Mönchengladbach" → "borussia monchengladbach"
    # Betclic FR "Borussia M'gladbach" → "borussia m gladbach" (apostrophe → space)
    "borussia m gladbach":      "borussia monchengladbach",

    # ── Serie A (noms FR Betclic) ─────────────────────────────────────────────
    # DB "Pisa" → "pisa"
    # Betclic FR "Pise" → "pise"
    "pise":                     "pisa",

    # ── PMU / Kambi (pmusportsfr) ─────────────────────────────────────────────
    # Kambi uses English names but with some French variants on the FR market
    # DB "Paris Saint-Germain" — "PSG" already covered above
    # DB "Atletico Madrid" → "atletico madrid"
    "atletico":                 "atletico madrid",
    # DB "Athletic Club" → "athletic club"
    "athletic club bilbao":     "athletic club",
    # DB "Real Betis" → "real betis"
    "real betis balompie":      "real betis",
    # DB "Deportivo Alaves" → "deportivo alaves"
    "deportivo alaves":         "deportivo alaves",  # exact match via normalize
    # DB "Borussia Mönchengladbach" → "borussia monchengladbach"
    "m'gladbach":               "borussia monchengladbach",
    # DB "Wolverhampton Wanderers" — Kambi may shorten
    "wolverhampton w":          "wolverhampton wanderers",
    # DB "Nottingham Forest" — Kambi variant
    "nottm forest":             "nottingham forest",
    # DB "Napoli" — Kambi EN
    "ssc napoli":               "napoli",
    # DB "Lazio" — Kambi EN
    "ss lazio":                 "lazio",
    # DB "Inter" — Kambi EN
    "internazionale":           "inter",
    # (acf fiorentina / ac milan already covered above)

    # ── Équipes nationales — PMU/Kambi (anglais) → DB (français, Bzzoiro) ─────
    # PMU uses English team names; DB fixtures use French names from Bzzoiro API.
    "northern ireland":         "irlande du nord",
    "netherlands":              "pays bas",
    "germany":                  "allemagne",
    "england":                  "angleterre",
    "spain":                    "espagne",
    "italy":                    "italie",
    "brazil":                   "bresil",
    "argentina":                "argentine",
    "mexico":                   "mexique",
    "usa":                      "etats unis",
    "united states":            "etats unis",
    "south africa":             "afrique du sud",
    "south korea":              "coree du sud",
    "colombia":                 "colombie",
    "cameroon":                 "cameroun",
    "morocco":                  "maroc",
    "australia":                "australie",
    "japan":                    "japon",
    "switzerland":              "suisse",
    "denmark":                  "danemark",
    "poland":                   "pologne",
    "turkey":                   "turquie",
    "turkiye":                  "turquie",
    "austria":                  "autriche",
    "serbia":                   "serbie",
    "croatia":                  "croatie",
    "belgium":                  "belgique",
    "wales":                    "pays de galles",
    "scotland":                 "ecosse",
    "ireland":                  "irlande",
    "ecuador":                  "equateur",
    "peru":                     "perou",
    "chile":                    "chili",
    "bolivia":                  "bolivie",
    "new zealand":              "nouvelle zelande",
    "ivory coast":              "cote d ivoire",
    "egypt":                    "egypte",
    "algeria":                  "algerie",
    "tunisia":                  "tunisie",
    "saudi arabia":             "arabie saoudite",
    "nigeria":                  "nigeria",
    "czechia":                  "tcheque",
    "czech republic":           "tcheque",
    "greece":                   "grece",
    "hungary":                  "hongrie",
    "romania":                  "roumanie",
    "slovakia":                 "slovaquie",
    "slovenia":                 "slovenie",
    "norway":                   "norvege",
    "sweden":                   "suede",
    "finland":                  "finlande",
    "russia":                   "russie",
    "ukraine":                  "ukraine",
    "iran":                     "iran",
    "qatar":                    "qatar",
    "senegal":                  "senegal",
    "ghana":                    "ghana",
    "mali":                     "mali",
    "burkina faso":             "burkina faso",
    "guinea":                   "guinee",
    "kenya":                    "kenya",
    "tanzania":                 "tanzanie",
    "venezuela":                "venezuela",
    "paraguay":                 "paraguay",
    "uruguay":                  "uruguay",
    "costa rica":               "costa rica",
    "honduras":                 "honduras",
    "panama":                   "panama",
    "jamaica":                  "jamaique",

    # ── Équipes nationales — Betclic/Unibet (noms FR alternatifs) → DB ────────
    # Betclic shows "USA" not "États-Unis"; "etats-unis" (hyphen) → canonical
    "etats-unis":               "etats unis",
    "republique tcheque":       "tcheque",
    "rep tcheque":              "tcheque",

    # ── Unibet LVS — noms compacts (sans espace, abréviations) ───────────────
    # Unibet colle certains noms composés sans espace dans le champ desc
    "irlandedunord":            "irlande du nord",
    "arabiesaoudite":           "arabie saoudite",
    "nlle zelande":             "nouvelle zelande",   # "Nlle Zélande" abbreviation
    "bosnie herzeg":            "bosnie herzegovine", # "Bosnie Herzég." → truncated

    # ── PMU / Kambi — noms anglais supplémentaires ───────────────────────────
    "jordan":                   "jordanie",
    "congo dr":                 "rd congo",
    "dem rep congo":            "rd congo",
    "democratic republic of congo": "rd congo",
    "dr congo":                 "rd congo",
    "cape verde":               "cap vert",

    # ── WC2026 — noms anglais Bzzoiro → noms français bookmakers ─────────────
    # Bzzoiro utilise des noms anglais, les scrapers FR utilisent les noms français.
    "iraq":                     "irak",
    "uzbekistan":               "ouzbekistan",
    "cabo verde":               "cap vert",
    "bosnia & herzegovina":     "bosnie herzegovine",

    # ── Champions League / Europa clubs — FR translations, not spelling
    # variants, so fuzzy matching (SequenceMatcher) cannot bridge them.
    # DB name is API-Football/Bzzoiro's official name; best-effort mapping,
    # confirm against prod "no fixture match" logs if it doesn't fire.
    "etoile rouge":             "fk crvena zvezda",  # Red Star Belgrade (Betclic/PMU FR)
}


def _normalize_team(name: str) -> str:
    """Normalize team name for fuzzy matching: lowercase, strip accents, collapse spaces."""
    n = name.lower().strip()
    n = unicodedata.normalize("NFKD", n)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"['.,-]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return _TEAM_ALIASES.get(n, n)


# ---------------------------------------------------------------------------
# Fallback team-name reconciliation (CORRECTION 1 + mixed-case + anchoring)
#
# When a bookmaker spells a team differently than the DB fixture, we match
# PER TEAM (not per homogeneous pair), SCOPED to fixtures already known to be
# `due` (never the whole fixtures table). For each team side, a bookmaker name
# matches a fixture side at one of these STRENGTHS (strongest first):
#
#   IDENTITY  — strict normalized equality, OR canonical identity (same
#               canonical_teams id, or a shared name_fr / name_en / alias).
#   TOKEN     — token-containment: the normalized token set of the shorter
#               name is fully contained in that of the longer name (both with
#               ≥1 token). Bridges official-vs-abbreviated names that fuzzy
#               scoring misses, e.g. {agf} ⊂ {agf, aarhus} ("AGF" vs
#               "AGF Aarhus"). Treated as a STRONG (near-identity) match.
#   FUZZY     — difflib similarity >= _FUZZY_MATCH_THRESHOLD.
#
# A fixture is a normal candidate when HOME matches AND AWAY matches (each by
# any strength), in one orientation OR the reversed one; its tier is the
# WEAKER of its two sides. Resolution walks tiers strongest-first:
#
#   1. IDENTITY (both sides identity)
#   2. TOKEN    (both sides ≥ token-containment)
#   3. ANCHOR   (calendar anchoring — see _anchor_match)
#   4. FUZZY    (both sides fuzzy)
#
# ANCHOR sits ABOVE fuzzy: when no fixture matches on BOTH sides by a
# certain means (identity/token), but one bookmaker side matches EXACTLY ONE
# due fixture with certainty on a consistent role, we may attach to it if the
# other bookmaker side is at least faintly plausible against that fixture's
# other team and not a better fit for some OTHER due fixture. This rescues
# cases like "Aarhus GF" vs "Sabah FK" → fixture "AGF" vs "Sabah FK" (Sabah
# anchors exactly; "Aarhus GF"/"AGF" ~0.5 clears the low floor).
#
# ANTI-FALSE-POSITIVE GUARDS:
#   - both sides must match — a single matching team is never enough
#     (except anchoring, which still validates the non-anchor side);
#   - at every tier we resolve ONLY when exactly one fixture qualifies; ties
#     are logged and skipped rather than guessed;
#   - STRICT priority identity > token > anchor > fuzzy, so a genuine
#     identity/token match is never stolen by an anchor or a coincidental
#     fuzzy match on another due fixture;
#   - the anchor's certain side must designate a SINGLE due fixture, else the
#     anchor is ambiguous and skipped.
# ---------------------------------------------------------------------------

_FUZZY_MATCH_THRESHOLD = 0.65

# Anchoring floor: the non-anchor bookmaker side must reach at least this
# similarity against the anchored fixture's other team. Calibrated at 0.30 —
# comfortably above absurd pairings ("real madrid"/"agf" ≈ 0.14) yet below
# legitimate abbreviation gaps ("aarhus gf"/"agf" ≈ 0.50). It only gates the
# SECOND side; the anchor side itself is already a certain (identity/token)
# match, so a low but non-zero floor here is safe.
_ANCHOR_FLOOR = 0.30

# Per-side match strengths (higher = more reliable).
_STRENGTH_NONE = 0
_STRENGTH_FUZZY = 1
_STRENGTH_TOKEN = 2
_STRENGTH_IDENTITY = 3
# "Certain" = matched by a non-fuzzy means (used to qualify an anchor side).
_STRENGTH_CERTAIN = _STRENGTH_TOKEN


def _build_canonical_alias_map(canonical_teams: Iterable) -> dict[str, int]:
    """Map every normalized name/alias of a CanonicalTeam to its id.

    Accepts any objects exposing `.id`, `.name_fr`, `.name_en`, `.aliases`
    (CanonicalTeam rows, or lightweight stand-ins in tests).
    """
    alias_map: dict[str, int] = {}
    for ct in canonical_teams:
        for name in (ct.name_fr, ct.name_en, *(ct.aliases or [])):
            if not name:
                continue
            key = _normalize_team(name)
            # First writer wins: never let one team's alias silently steal a
            # key another team already claimed.
            alias_map.setdefault(key, ct.id)
    return alias_map


def _build_canonical_names_by_id(canonical_teams: Iterable) -> dict[int, set[str]]:
    """Map each CanonicalTeam id → the set of its normalized names/aliases."""
    names_by_id: dict[int, set[str]] = {}
    for ct in canonical_teams:
        bucket = names_by_id.setdefault(ct.id, set())
        for name in (ct.name_fr, ct.name_en, *(ct.aliases or [])):
            if name:
                bucket.add(_normalize_team(name))
    return names_by_id


def _team_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _resolve_side(
    norm_name: str,
    explicit_cid: int | None,
    alias_map: dict[str, int],
    canonical_names_by_id: dict[int, set[str]],
) -> tuple[int | None, set[str]]:
    """Resolve a team side to (canonical_id_or_None, set_of_normalized_names).

    The canonical id comes from the fixture's own column when present, else
    from resolving the (normalized) name through the alias map. The name set
    always contains the side's own normalized name plus every known
    name/alias of its canonical team, so two sides can be compared by name
    even when only one of them carries an explicit canonical id.
    """
    cid = explicit_cid if explicit_cid is not None else alias_map.get(norm_name)
    names = {norm_name}
    if cid is not None:
        names |= canonical_names_by_id.get(cid, set())
    return cid, names


# Tokens that mark a DISTINCT team (reserve / youth side), not a fuller
# spelling of the same club. When the only extra token in the longer name is
# one of these, token-containment must NOT fire — "Sparta Prague" and
# "Sparta Prague B" are different teams.
_TEAM_VARIANT_MARKERS = frozenset(
    {"b", "ii", "iii", "iv", "u18", "u19", "u20", "u21", "u23", "reserve", "reserves"}
)


def _tokens(norm_name: str) -> set[str]:
    """Significant (non-empty) normalized tokens of a team name."""
    return {t for t in norm_name.split() if t}


def _token_contained(a_norm: str, b_norm: str) -> bool:
    """True if the shorter name's tokens are fully contained in the longer's.

    Both names must carry ≥1 token. e.g. {agf} ⊆ {agf, aarhus}. Exact string
    equality is handled upstream (identity) — this fires for genuine
    abbreviation/superset relationships, incl. reordered same-token names.

    Guard: if the ONLY extra tokens in the longer name are reserve/youth
    markers (b, ii, u21, …), the longer name is a DIFFERENT team, so
    containment is rejected — e.g. "Sparta Prague" ⊄ "Sparta Prague B". Numeric
    or descriptive extras ("Mainz" → "Mainz 05") are NOT markers and still match.
    """
    ta, tb = _tokens(a_norm), _tokens(b_norm)
    if not ta or not tb:
        return False
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if not shorter.issubset(longer):
        return False
    # Reject when the ONLY extra tokens are reserve/youth markers (distinct team).
    extra = longer - shorter
    return not (extra and extra.issubset(_TEAM_VARIANT_MARKERS))


def _side_strength(
    a: tuple[str, int | None, set[str]],
    b: tuple[str, int | None, set[str]],
    threshold: float,
) -> int:
    """Strength at which bookmaker side `a` matches fixture side `b`.

    Each side is (normalized_name, canonical_id_or_None, names_set).
    Returns one of _STRENGTH_{IDENTITY,TOKEN,FUZZY,NONE}.
    """
    a_norm, a_cid, a_names = a
    b_norm, b_cid, b_names = b
    # IDENTITY — shared canonical id, or strict equality / shared name-alias
    if a_cid is not None and b_cid is not None and a_cid == b_cid:
        return _STRENGTH_IDENTITY
    if a_names & b_names:
        return _STRENGTH_IDENTITY
    # TOKEN — one name's token set contained in the other's
    if _token_contained(a_norm, b_norm):
        return _STRENGTH_TOKEN
    # FUZZY
    if _team_similarity(a_norm, b_norm) >= threshold:
        return _STRENGTH_FUZZY
    return _STRENGTH_NONE


_ResolvedSide = tuple[str, int | None, set[str]]


def _anchor_match(
    bh_side: _ResolvedSide,
    ba_side: _ResolvedSide,
    resolved: Sequence[tuple[int, _ResolvedSide, _ResolvedSide]],
    threshold: float,
    floor: float,
) -> tuple[int | None, str, str | None]:
    """Calendar-anchoring tier — see module comment.

    Only meaningful once identity/token both-sides matching found nothing. One
    bookmaker side must match with CERTAINTY (identity/token, never fuzzy)
    exactly ONE due fixture on a role-consistent orientation; the fixture is
    then accepted iff the OTHER bookmaker side clears `floor` against that
    fixture's other team and is not a strictly better fit for any other due
    fixture's team.

    Returns (fixture_id_or_None, reason, anchor_name) with reason ∈
    {"anchor", "ambiguous_anchor", "no_anchor"}; anchor_name is the certain
    side's normalized name (for logging) when reason == "anchor".
    """
    # Collect certain anchors: (fid, anchor_book_side, other_book_side, other_fix_side)
    anchors: list[tuple[int, _ResolvedSide, _ResolvedSide, _ResolvedSide]] = []
    for fid, fh_side, fa_side in resolved:
        for h_fix, a_fix in ((fh_side, fa_side), (fa_side, fh_side)):  # both orientations
            if _side_strength(bh_side, h_fix, threshold) >= _STRENGTH_CERTAIN:
                anchors.append((fid, bh_side, ba_side, a_fix))  # home certain; away is other
            if _side_strength(ba_side, a_fix, threshold) >= _STRENGTH_CERTAIN:
                anchors.append((fid, ba_side, bh_side, h_fix))  # away certain; home is other

    if not anchors:
        return None, "no_anchor", None

    distinct_fids = {a[0] for a in anchors}
    if len(distinct_fids) > 1:
        # The certain side plausibly belongs to several due fixtures — never guess.
        return None, "ambiguous_anchor", None

    fid = next(iter(distinct_fids))
    other_fixtures = [r for r in resolved if r[0] != fid]

    for _fid, anchor_book, other_book, other_fix in anchors:
        base = _team_similarity(other_book[0], other_fix[0])
        if base < floor:
            continue  # non-anchor side too weak → absurd pairing, reject this anchor
        # The non-anchor side must not resemble ANOTHER due fixture's team more.
        better_elsewhere = any(
            _team_similarity(other_book[0], side[0]) > base
            for _gfid, gh, ga in other_fixtures
            for side in (gh, ga)
        )
        if better_elsewhere:
            return None, "ambiguous_anchor", None
        return fid, "anchor", anchor_book[0]

    # An anchor's certain side pointed at this fixture, but its other side was
    # below the floor everywhere → nothing to anchor on. Fall through to fuzzy.
    return None, "no_anchor", None


# Meme tolerance que l'ancrage PS3838 (ps3838/anchor.py MAX_KICKOFF_DELTA).
_KICKOFF_TOLERANCE = timedelta(hours=2)


def _candidates_near_kickoff(
    candidates: Sequence[tuple[int, str, int | None, str, int | None]],
    kickoff_by_fid: dict[int, datetime | None],
    book_kickoff: datetime | None,
) -> Sequence[tuple[int, str, int | None, str, int | None]]:
    """Restreint les candidats aux fixtures qui commencent a ±2 h du match cote.

    Un club joue souvent deux matchs dans la fenetre de scraping (championnat
    et LDC). Sans la date, l'ancrage sur le seul club reconnu ('Lens' face a
    'Sporting Lisbon') designait plusieurs fixtures et etait abandonne comme
    ambigu : le 12/10/2026, six matchs de LDC n'avaient pas les cotes d'au
    moins un bookmaker. Sans heure cote, ou si aucun candidat n'est proche,
    on garde la liste entiere : la date affine, elle n'exclut jamais seule.
    """
    if book_kickoff is None:
        return candidates
    if book_kickoff.tzinfo is None:
        book_kickoff = book_kickoff.replace(tzinfo=UTC)
    near = []
    for c in candidates:
        ko = kickoff_by_fid.get(c[0])
        if ko is None:
            continue
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=UTC)
        if abs(ko - book_kickoff) <= _KICKOFF_TOLERANCE:
            near.append(c)
    return near or candidates


def _match_result_to_fixture(
    book_home: str,
    book_away: str,
    candidates: Sequence[tuple[int, str, int | None, str, int | None]],
    alias_map: dict[str, int],
    canonical_names_by_id: dict[int, set[str]],
    threshold: float = _FUZZY_MATCH_THRESHOLD,
    anchor_floor: float = _ANCHOR_FLOOR,
) -> tuple[int | None, str, list[int], str | None]:
    """Match a scraped (book_home, book_away) pair to exactly one due fixture.

    `candidates` is a list of
    (fixture_id, home_norm, home_canonical_id, away_norm, away_canonical_id)
    for fixtures already known to be due — never the whole fixtures table.

    Matching is PER TEAM at graded strengths, resolved strongest-tier-first:
    identity > token-containment > calendar-anchoring > fuzzy (see module
    comment). Both sides are required except for the anchoring tier, which
    still validates the non-anchor side.

    Returns (fixture_id_or_None, reason, competing_fids, detail) where reason ∈
    {"identity","token","anchor","fuzzy",
     "ambiguous_identity","ambiguous_token","ambiguous_anchor",
     "ambiguous_fuzzy","none"}, competing_fids lists the fixtures in play at
    the deciding tier, and detail carries the anchor team name for reason
    "anchor" (else None) — both for logging/traceability.
    """
    bh_r = _resolve_side(_normalize_team(book_home), None, alias_map, canonical_names_by_id)
    ba_r = _resolve_side(_normalize_team(book_away), None, alias_map, canonical_names_by_id)
    bh_side: _ResolvedSide = (_normalize_team(book_home), bh_r[0], bh_r[1])
    ba_side: _ResolvedSide = (_normalize_team(book_away), ba_r[0], ba_r[1])

    resolved: list[tuple[int, _ResolvedSide, _ResolvedSide]] = []
    identity_fids: list[int] = []
    token_fids: list[int] = []
    fuzzy_fids: list[int] = []
    for fid, fh_norm, fh_cid, fa_norm, fa_cid in candidates:
        fh_r = _resolve_side(fh_norm, fh_cid, alias_map, canonical_names_by_id)
        fa_r = _resolve_side(fa_norm, fa_cid, alias_map, canonical_names_by_id)
        fh_side: _ResolvedSide = (fh_norm, fh_r[0], fh_r[1])
        fa_side: _ResolvedSide = (fa_norm, fa_r[0], fa_r[1])
        resolved.append((fid, fh_side, fa_side))

        # Best (over both orientations) tier at which BOTH sides match.
        best = _STRENGTH_NONE
        for h_fix, a_fix in ((fh_side, fa_side), (fa_side, fh_side)):
            hs = _side_strength(bh_side, h_fix, threshold)
            as_ = _side_strength(ba_side, a_fix, threshold)
            if hs and as_:
                best = max(best, min(hs, as_))
        if best == _STRENGTH_IDENTITY:
            identity_fids.append(fid)
        elif best == _STRENGTH_TOKEN:
            token_fids.append(fid)
        elif best == _STRENGTH_FUZZY:
            fuzzy_fids.append(fid)

    # Tier 1 — both sides identity.
    if identity_fids:
        if len(identity_fids) == 1:
            return identity_fids[0], "identity", identity_fids, None
        return None, "ambiguous_identity", identity_fids, None

    # Tier 2 — both sides ≥ token-containment.
    if token_fids:
        if len(token_fids) == 1:
            return token_fids[0], "token", token_fids, None
        return None, "ambiguous_token", token_fids, None

    # Tier 3 — calendar anchoring (only when no certain both-sides match).
    anchor_fid, anchor_reason, anchor_name = _anchor_match(
        bh_side, ba_side, resolved, threshold, anchor_floor
    )
    if anchor_reason == "anchor":
        return anchor_fid, "anchor", [anchor_fid] if anchor_fid is not None else [], anchor_name
    if anchor_reason == "ambiguous_anchor":
        return None, "ambiguous_anchor", [], None

    # Tier 4 — both sides fuzzy.
    if fuzzy_fids:
        if len(fuzzy_fids) == 1:
            return fuzzy_fids[0], "fuzzy", fuzzy_fids, None
        return None, "ambiguous_fuzzy", fuzzy_fids, None

    return None, "none", [], None


def _league_key(league_name: str | None) -> str | None:
    """Map fixture league name to scraper league key."""
    if not league_name:
        return None
    mapping = {
        "ligue 1": "ligue_1", "ligue_1": "ligue_1",
        "premier league": "premier_league", "premier_league": "premier_league",
        "bundesliga": "bundesliga",
        "la liga": "la_liga", "la_liga": "la_liga",
        "serie a": "serie_a", "serie_a": "serie_a",
        "champions league": "champions_league", "champions_league": "champions_league",
        "friendly_international": "friendly_international",
        "friendly international": "friendly_international",
        "world_cup_2026": "world_cup_2026",
        "world cup 2026": "world_cup_2026",
        "nations_league_uefa": "nations_league_uefa",
        "nations league uefa": "nations_league_uefa",
        "nations_league_concacaf": "nations_league_concacaf",
        "nations league concacaf": "nations_league_concacaf",
        "uefa_super_cup": "uefa_super_cup",
        "uefa super cup": "uefa_super_cup",
    }
    key = mapping.get(league_name.lower())
    if key:
        return key
    # Compétitions de la table commune : l'étiquette du match (« comp_<id> »)
    # est la clé des scrapers, sauf compétition partagée (Liga MX).
    from app.ingestion.book_competitions import SCRAPER_KEY_BY_LEAGUE

    return SCRAPER_KEY_BY_LEAGUE.get(league_name)


# Matchs lointains (coup d'envoi à plus de 6 h) rafraîchis par passage, au
# plus. Tous les matchs créés ensemble (mise en ligne, synchro du calendrier)
# deviennent « à rafraîchir » en même temps : sans plafond, un seul passage
# lisait 600 matchs chez chaque book pendant 15 à 20 minutes, et les matchs
# proches du coup d'envoi perdaient leur rafraîchissement toutes les 2 minutes.
# Les matchs en trop restent « à rafraîchir » et passent aux tours suivants,
# les plus anciens d'abord : le lot se disperse tout seul.
_MAX_FAR_FIXTURES_PER_TICK = 30


# Unibet a raté un match (liste refusée, lecture en erreur) : on réessaie ce
# délai plus tard, sans attendre le prochain passage normal (jusqu'à 2 h).
_UNIBET_RETRY = timedelta(minutes=5)
# Nombre de passages ratés d'affilée sur un championnat avant d'alerter.
_UNIBET_ALERT_AFTER = 3
# Un match relu par identifiant doit démarrer à peu près quand on l'attend ;
# au-delà, l'identifiant ne désigne plus ce match et l'accrochage est défait.
_UNIBET_ANCHOR_MAX_DRIFT = timedelta(hours=36)
# Écart d'horaire maximal entre le match et l'événement Unibet pour poser l'accrochage.
_UNIBET_ANCHOR_KICKOFF_TOLERANCE = timedelta(hours=2)


class OddsScheduler:
    """Drives adaptive scraping of Betclic + Unibet + PMU for upcoming fixtures."""

    def __init__(self) -> None:
        # fixture_id -> heure à partir de laquelle retenter Unibet seul.
        self._unibet_retry: dict[int, datetime] = {}
        # championnat -> passages ratés d'affilée (liste refusée).
        self._unibet_fail_streak: dict[str, int] = {}

    def _unibet_follow_up(
        self,
        targets: Sequence,
        league_by_fid: dict[int, str],
        known: dict[int, tuple[int, str]],
        report,
        fids_ok: set[int],
        now: datetime,
    ) -> None:
        """Après un passage : programme les reprises Unibet, défait les
        accrochages morts, alerte si un championnat reste refusé.

        Une reprise n'est programmée que pour une vraie panne (liste refusée,
        lecture en erreur, match disparu) — jamais parce qu'Unibet ne propose
        tout simplement pas le match, sinon on le redemanderait sans fin.
        """
        from app.alerts import alert_bg

        event_by_fid = {fid: event_id for event_id, (fid, _) in known.items()}
        for f in targets:
            league = league_by_fid.get(f.id)
            if league is None:
                continue
            event_id = event_by_fid.get(f.id)
            if event_id is not None and event_id in report.gone_events:
                # L'identifiant ne désigne plus ce match : on l'oublie, le
                # prochain passage le recherche dans la liste du championnat.
                f.unibet_event_id = None
                failed = True
            elif event_id is not None:
                failed = event_id in report.failed_events
            else:
                failed = league in report.failed_leagues
            if failed and f.id not in fids_ok:
                self._unibet_retry[f.id] = now + _UNIBET_RETRY
            else:
                self._unibet_retry.pop(f.id, None)

        asked = {
            league_by_fid[f.id] for f in targets
            if f.id in league_by_fid and f.id not in event_by_fid
        }
        a_signaler: list[str] = []
        for league in asked:
            if league not in report.failed_leagues:
                self._unibet_fail_streak.pop(league, None)
                continue
            streak = self._unibet_fail_streak.get(league, 0) + 1
            self._unibet_fail_streak[league] = streak
            if streak == _UNIBET_ALERT_AFTER:
                a_signaler.append(league)
        if a_signaler:
            # UNE alerte par passage, quel que soit le nombre de championnats :
            # si Unibet bloque le serveur, 40 championnats tombent ensemble.
            alert_bg(
                f"⚠️ Unibet refuse la liste des matchs depuis {_UNIBET_ALERT_AFTER} passages "
                f"d'affilée sur {len(a_signaler)} championnat(s) : "
                f"{', '.join(sorted(a_signaler)[:8])}{'…' if len(a_signaler) > 8 else ''}. "
                "Leurs matchs pas encore accrochés n'ont plus de cotes Unibet fraîches.",
                "incidents",
            )

    async def tick(self, session: AsyncSession) -> tuple[int, list[int]]:
        """Process all fixtures due for a scrape. Returns (count_due, stored_fixture_ids)."""
        from app.ingestion.betclic_grpc_scraper import scrape_betclic_leagues
        from app.ingestion.odds_storage import store_match_scrape_result
        from app.ingestion.pmu_scraper import scrape_all_pmu
        from app.ingestion.ps3838.scraper import scrape_ps3838
        from app.ingestion.unibet_lvs_scraper import UnibetReport, scrape_all_unibet
        from app.models.canonical_teams import CanonicalTeam
        from app.models.fixtures import Fixture
        from app.models.odds_scrape_state import OddsScrapeState
        from app.services.player_identity import SquadPlayer

        now = datetime.now(UTC)
        # Effectif Atlas par match, chargé une seule fois par passage et
        # partagé entre bookmakers pour relier chaque cote à son joueur.
        squad_cache: dict[int, list[SquadPlayer]] = {}
        # +11 days so that matches on the 10th day (which may kick off late in
        # the day) are always included regardless of the current time of day.
        cutoff = now + timedelta(days=11)

        # Load upcoming fixtures
        result = await session.execute(
            select(Fixture).where(
                Fixture.kickoff_utc.isnot(None),
                Fixture.kickoff_utc <= cutoff,
                Fixture.kickoff_utc > now - timedelta(minutes=5),
                Fixture.status.notin_(["finished", "cancelled", "postponed"]),
            )
        )
        fixtures = result.scalars().all()
        if not fixtures:
            return 0, []

        # Load scrape states
        states_result = await session.execute(
            select(OddsScrapeState).where(
                OddsScrapeState.fixture_id.in_([f.id for f in fixtures])
            )
        )
        states: dict[int, OddsScrapeState] = {
            s.fixture_id: s for s in states_result.scalars().all()
        }

        # Determine which fixtures are due
        due = [
            f for f in fixtures
            if should_scrape(
                f.kickoff_utc,
                states[f.id].last_scraped_at if f.id in states else None,
            )
        ]

        proches = [f for f in due if f.kickoff_utc - now <= _MID_FREQ_THRESHOLD]
        lointains = [f for f in due if f.kickoff_utc - now > _MID_FREQ_THRESHOLD]
        if len(lointains) > _MAX_FAR_FIXTURES_PER_TICK:
            jamais = datetime.min.replace(tzinfo=UTC)

            def _anciennete(f) -> datetime:
                last = states[f.id].last_scraped_at if f.id in states else None
                return last or jamais

            lointains.sort(key=lambda f: (_anciennete(f), f.kickoff_utc))
            logger.info(
                "OddsScheduler.tick: %d matchs lointains à rafraîchir, %d traités ce passage",
                len(lointains), _MAX_FAR_FIXTURES_PER_TICK,
            )
            lointains = lointains[:_MAX_FAR_FIXTURES_PER_TICK]
        due = proches + lointains

        # Reprises Unibet : matchs dont la dernière lecture Unibet a raté et
        # dont le délai de reprise est écoulé. Seul Unibet est relu pour eux.
        due_ids = {f.id for f in due}
        live_ids = {f.id for f in fixtures}
        self._unibet_retry = {
            fid: t for fid, t in self._unibet_retry.items() if fid in live_ids
        }
        unibet_retry = [
            f for f in fixtures
            if f.id not in due_ids
            and self._unibet_retry.get(f.id, now + _UNIBET_RETRY) <= now
            and f.kickoff_utc - now > _STOP_BEFORE_KO
        ]

        if not due and not unibet_retry:
            logger.debug("OddsScheduler.tick: 0 fixtures due")
            return 0, []

        logger.info(
            "OddsScheduler.tick: %d fixtures due for scraping, %d reprise(s) Unibet",
            len(due), len(unibet_retry),
        )

        # Collect leagues needed + per-team match candidates for due fixtures.
        # A single candidate list drives the unified per-team matcher below —
        # each tuple carries both the normalized names AND the canonical ids so
        # a side can match by strict / canonical / fuzzy independently.
        leagues_needed: set[str] = set()
        # Horaires des matchs à rafraîchir, par championnat : Betclic et PMU ne
        # lisent que ces matchs-là, pas tout le championnat.
        due_kickoffs: dict[str, list[datetime]] = {}
        match_candidates: list[tuple[int, str, int | None, str, int | None]] = []
        unibet_targets = [*due, *unibet_retry]
        fixture_by_id = {f.id: f for f in unibet_targets}
        kickoff_by_fid = {f.id: f.kickoff_utc for f in unibet_targets}
        # Unibet : matchs déjà accrochés (relus par identifiant) / championnats
        # où chercher ceux qui ne le sont pas encore.
        unibet_known: dict[int, tuple[int, str]] = {}
        unibet_kickoffs: dict[str, list[datetime]] = {}
        league_by_fid: dict[int, str] = {}
        for f in unibet_targets:
            league = _league_key(f.league)
            if league:
                league_by_fid[f.id] = league
                event_id = getattr(f, "unibet_event_id", None)
                if event_id is not None:
                    unibet_known[event_id] = (f.id, league)
                else:
                    unibet_kickoffs.setdefault(league, []).append(f.kickoff_utc)
                if f.id in due_ids:
                    leagues_needed.add(league)
                    due_kickoffs.setdefault(league, []).append(f.kickoff_utc)
            match_candidates.append(
                (
                    f.id,
                    _normalize_team(f.home_team),
                    f.home_canonical_team_id,
                    _normalize_team(f.away_team),
                    f.away_canonical_team_id,
                )
            )

        # NOTE : contrairement au brief initial, l'absence de ligue reconnue
        # ne fait plus sortir tick() en anticipe (`return 0, []`). PS3838 n'a
        # besoin d'aucune ligue reconnue — il lit toute la categorie football
        # et travaille par identifiant (ps3838_event_id) — donc il doit
        # pouvoir tourner meme quand betclic/unibet/pmu n'ont rien a scraper.
        all_results = []
        unibet_report = UnibetReport()
        # Identifiants Unibet déjà posés sur un match, quel qu'il soit : un
        # identifiant ne sert jamais deux fois (contrainte unique en base), et
        # un match déjà accroché n'est pas relu une seconde fois par la liste.
        taken_event_ids = set(unibet_known)
        if unibet_kickoffs:
            taken_event_ids |= {
                e for e in (await session.execute(
                    select(Fixture.unibet_event_id).where(Fixture.unibet_event_id.isnot(None))
                )).scalars().all()
                if isinstance(e, int)
            }
        if not leagues_needed and not unibet_known and not unibet_kickoffs:
            logger.warning("OddsScheduler.tick: no recognized leagues in due fixtures")
        else:
            # Canonical-team indexes for the per-team matcher. Small table —
            # loading it in full is cheap and avoids missing entries.
            canonical_teams = (await session.execute(select(CanonicalTeam))).scalars().all()
            canonical_alias_map = _build_canonical_alias_map(canonical_teams)
            canonical_names_by_id = _build_canonical_names_by_id(canonical_teams)

            # Scrape les 3 books en parallèle
            async def _none() -> list:
                return []

            # Betclic et PMU : seulement pour les matchs normalement à
            # rafraîchir. Unibet : ceux-là plus ses reprises.
            regular = bool(leagues_needed)
            betclic_results, unibet_results, pmu_results = await asyncio.gather(
                scrape_betclic_leagues(list(leagues_needed), due_kickoffs) if regular else _none(),
                scrape_all_unibet(
                    list(unibet_kickoffs),
                    known_events=unibet_known,
                    wanted_kickoffs=unibet_kickoffs,
                    report=unibet_report,
                    skip_event_ids=taken_event_ids,
                ),
                scrape_all_pmu(list(leagues_needed), due_kickoffs) if regular else _none(),
                return_exceptions=True,
            )

            if isinstance(betclic_results, BaseException):
                logger.error(
                    "OddsScheduler: betclic scrape failed: %s", betclic_results, exc_info=betclic_results
                )
            else:
                all_results.extend(betclic_results)
            if isinstance(unibet_results, BaseException):
                logger.error(
                    "OddsScheduler: unibet scrape failed: %s", unibet_results, exc_info=unibet_results
                )
                unibet_report.failed_leagues.update(unibet_kickoffs)
                unibet_report.failed_events.update(unibet_known)
            else:
                all_results.extend(unibet_results)
            if isinstance(pmu_results, BaseException):
                logger.error(
                    "OddsScheduler: pmu scrape failed: %s", pmu_results, exc_info=pmu_results
                )
            else:
                all_results.extend(pmu_results)

        # Match scraped results to fixture_ids and store
        scraped = 0
        stored_fixture_ids: set[int] = set()
        unibet_fids_ok: set[int] = set()
        anchored_fids = {fid for fid, _ in unibet_known.values()}
        for r in all_results:
            # Unibet, match déjà accroché : le résultat porte son fixture_id,
            # aucun rapprochement par noms.
            if r.bookmaker == "unibet" and r.book_event_id in unibet_known:
                fid = unibet_known[r.book_event_id][0]
                expected = kickoff_by_fid[fid]
                if r.kickoff_utc and abs(r.kickoff_utc - expected) > _UNIBET_ANCHOR_MAX_DRIFT:
                    logger.warning(
                        "OddsScheduler: accrochage Unibet e%s défait pour le fixture %s "
                        "(coup d'envoi %s chez Unibet, %s attendu)",
                        r.book_event_id, fid, r.kickoff_utc, expected,
                    )
                    unibet_report.gone_events.add(r.book_event_id)
                    continue
                r.fixture_id = fid
                await store_match_scrape_result(r, session, squad_cache=squad_cache)
                stored_fixture_ids.add(fid)
                unibet_fids_ok.add(fid)
                scraped += 1
                continue
            # Unified per-team matcher: identity / token-containment / anchor /
            # fuzzy, each side graded, both sides required (except anchoring),
            # strict tier priority + unique-candidate guard.
            # Seuls les matchs de la MÊME compétition sont candidats. Avec
            # tout l'Atlas au calendrier (70 matchs par jour), comparer les
            # noms d'un match de book à ceux de tous les championnats collait
            # des cotes au mauvais match.
            same_league = [
                c for c in match_candidates if league_by_fid.get(c[0]) == r.league
            ]
            fixture_id, reason, competing_fids, detail = _match_result_to_fixture(
                r.home_team, r.away_team,
                _candidates_near_kickoff(same_league, kickoff_by_fid, r.kickoff_utc),
                canonical_alias_map, canonical_names_by_id,
            )
            if fixture_id and reason == "anchor":
                logger.info(
                    "OddsScheduler: anchor match for '%s' vs '%s' -> fixture %s (ancre=%s)",
                    r.home_team, r.away_team, fixture_id, detail,
                )
            elif fixture_id:
                logger.info(
                    "OddsScheduler: %s match for '%s' vs '%s' -> fixture %s",
                    reason, r.home_team, r.away_team, fixture_id,
                )
            elif reason.startswith("ambiguous"):
                logger.warning(
                    "OddsScheduler: %s for '%s' vs '%s' skipped "
                    "(ne devine pas) - candidates=%s",
                    reason, r.home_team, r.away_team, competing_fids,
                )
                continue
            else:
                logger.warning(
                    "OddsScheduler: no fixture match for '%s' vs '%s' (normalized: '%s' vs '%s')",
                    r.home_team, r.away_team,
                    _normalize_team(r.home_team), _normalize_team(r.away_team),
                )
                continue
            r.fixture_id = fixture_id
            if r.bookmaker == "unibet":
                unibet_fids_ok.add(fixture_id)
                # Premier rapprochement réussi : on retient l'identifiant
                # Unibet du match pour le relire directement ensuite.
                fx = fixture_by_id.get(fixture_id)
                # Seulement si l'horaire colle : aller et retour d'une même
                # affiche ne doivent jamais échanger leurs identifiants.
                if (
                    fx is not None and r.book_event_id is not None
                    and fixture_id not in anchored_fids
                    and r.book_event_id not in taken_event_ids
                    and r.kickoff_utc is not None
                    and abs(r.kickoff_utc - fx.kickoff_utc) <= _UNIBET_ANCHOR_KICKOFF_TOLERANCE
                ):
                    fx.unibet_event_id = r.book_event_id
                    anchored_fids.add(fixture_id)
                    taken_event_ids.add(r.book_event_id)
            await store_match_scrape_result(r, session, squad_cache=squad_cache)
            stored_fixture_ids.add(fixture_id)
            scraped += 1

        self._unibet_follow_up(
            unibet_targets, league_by_fid, unibet_known, unibet_report, unibet_fids_ok, now,
        )

        # PS3838 — source unique du xG d'equipe. Les resultats portent deja le
        # bon fixture_id (ancrage par identifiant) : ils NE PASSENT PAS par le
        # rapprochement de noms, qui ecraserait fixture_id.
        try:
            # Passage de reprise Unibet seul (aucun match « à rafraîchir ») :
            # PS3838 n'est pas relu — une liste vide lui ferait lire TOUS les
            # matchs accrochés.
            ps_results = await scrape_ps3838(session, [f.id for f in due]) if due else []
        except Exception as exc:
            logger.error("OddsScheduler: ps3838 scrape failed: %s", exc, exc_info=exc)
            ps_results = []

        for r in ps_results:
            await store_match_scrape_result(r, session, squad_cache=squad_cache)
            stored_fixture_ids.add(r.fixture_id)
            scraped += 1

        # Update odds_scrape_state for all due fixtures
        for f in due:
            betclic_ok = any(
                r.fixture_id == f.id and r.bookmaker == "betclic"
                for r in all_results
            )
            unibet_ok = any(
                r.fixture_id == f.id and r.bookmaker == "unibet"
                for r in all_results
            )
            pmu_ok = any(
                r.fixture_id == f.id and r.bookmaker == "pmu"
                for r in all_results
            )
            interval = scrape_interval_seconds(f.kickoff_utc)
            stmt = (
                pg_insert(OddsScrapeState)
                .values(
                    fixture_id=f.id,
                    last_scraped_at=now,
                    next_scrape_at=now + timedelta(seconds=interval),
                    betclic_ok=betclic_ok,
                    unibet_ok=unibet_ok,
                    pmu_ok=pmu_ok,
                )
                .on_conflict_do_update(
                    index_elements=["fixture_id"],
                    set_={
                        "last_scraped_at": now,
                        "next_scrape_at": now + timedelta(seconds=interval),
                        "betclic_ok": betclic_ok,
                        "unibet_ok": unibet_ok,
                        "pmu_ok": pmu_ok,
                    },
                )
            )
            await session.execute(stmt)

        await session.commit()
        logger.info(
            "OddsScheduler.tick: stored %d results for %d fixtures",
            scraped, len(due),
        )
        return len(due), list(stored_fixture_ids)
