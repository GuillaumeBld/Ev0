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
# Fallback team-name reconciliation (CORRECTION 1 + mixed-case follow-up)
#
# When a bookmaker spells a team differently than the DB fixture, we match
# PER TEAM (not per homogeneous pair), SCOPED to fixtures already known to be
# `due` (never the whole fixtures table). For each team side, a bookmaker name
# is considered to match a fixture side if ANY of these succeed:
#
#   (a) strict normalized equality;
#   (b) canonical identity — the bookmaker name and the fixture side resolve
#       (via canonical_teams id, or via a shared name_fr / name_en / alias)
#       to the SAME canonical team;
#   (c) fuzzy similarity (difflib) >= threshold.
#
# A fixture is a candidate when HOME matches AND AWAY matches (each by any of
# the three means), in one orientation OR the reversed one. This is what
# unlocks the mixed case — e.g. bookmaker "Sparta Prague" vs "Lyon" against a
# DB fixture "AC Sparta Praha" (home canonical NULL) vs "Olympique Lyonnais"
# (away canonical = Lyon): the away side matches by canonical identity while
# the home side matches only by fuzzy — different means per side.
#
# ANTI-FALSE-POSITIVE GUARDS (unchanged intent):
#   - both sides must match — a single matching team is never enough;
#   - among due fixtures we resolve ONLY when exactly one fixture matches;
#     ties are logged and skipped rather than guessed (a wrong guess would
#     attach odds to the wrong match);
#   - exact matches (all sides via strict/canonical identity) are preferred
#     over fuzzy-involved ones, so a genuine identity match is never lost to a
#     coincidental fuzzy match on another due fixture.
# ---------------------------------------------------------------------------

_FUZZY_MATCH_THRESHOLD = 0.65


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


def _sides_match(
    a: tuple[str, int | None, set[str]],
    b: tuple[str, int | None, set[str]],
    threshold: float,
) -> tuple[bool, bool]:
    """Does bookmaker side `a` match fixture side `b`?

    Each side is (normalized_name, canonical_id_or_None, names_set).
    Returns (matched, is_identity) where is_identity is True for strict /
    canonical matches and False for fuzzy-only matches.
    """
    a_norm, a_cid, a_names = a
    b_norm, b_cid, b_names = b
    # (b) canonical identity via shared id
    if a_cid is not None and b_cid is not None and a_cid == b_cid:
        return True, True
    # (a)+(b) strict equality OR shared canonical name/alias
    if a_names & b_names:
        return True, True
    # (c) fuzzy
    if _team_similarity(a_norm, b_norm) >= threshold:
        return True, False
    return False, False


def _match_result_to_fixture(
    book_home: str,
    book_away: str,
    candidates: Sequence[tuple[int, str, int | None, str, int | None]],
    alias_map: dict[str, int],
    canonical_names_by_id: dict[int, set[str]],
    threshold: float = _FUZZY_MATCH_THRESHOLD,
) -> tuple[int | None, str, list[int], list[int]]:
    """Match a scraped (book_home, book_away) pair to exactly one due fixture.

    `candidates` is a list of
    (fixture_id, home_norm, home_canonical_id, away_norm, away_canonical_id)
    for fixtures already known to be due — never the whole fixtures table.

    Matching is PER TEAM: each side may match by strict equality, canonical
    identity, or fuzzy (see module comment). A fixture qualifies when BOTH
    sides match in one orientation or the reversed one.

    Returns (fixture_id_or_None, reason, exact_fids, loose_fids). A fixture is
    returned only when it is the UNIQUE match at the strongest available tier
    (identity preferred over fuzzy); ties → None with reason "ambiguous_*".
    """
    bh = _resolve_side(_normalize_team(book_home), None, alias_map, canonical_names_by_id)
    ba = _resolve_side(_normalize_team(book_away), None, alias_map, canonical_names_by_id)
    bh_side = (_normalize_team(book_home), bh[0], bh[1])
    ba_side = (_normalize_team(book_away), ba[0], ba[1])

    exact_fids: list[int] = []
    loose_fids: list[int] = []
    for fid, fh_norm, fh_cid, fa_norm, fa_cid in candidates:
        fh = _resolve_side(fh_norm, fh_cid, alias_map, canonical_names_by_id)
        fa = _resolve_side(fa_norm, fa_cid, alias_map, canonical_names_by_id)
        fh_side = (fh_norm, fh[0], fh[1])
        fa_side = (fa_norm, fa[0], fa[1])

        best_kind: str | None = None  # None | "fuzzy" | "identity"
        for h_book, a_book, h_fix, a_fix in (
            (bh_side, ba_side, fh_side, fa_side),   # same orientation
            (bh_side, ba_side, fa_side, fh_side),   # reversed orientation
        ):
            h_ok, h_id = _sides_match(h_book, h_fix, threshold)
            a_ok, a_id = _sides_match(a_book, a_fix, threshold)
            if h_ok and a_ok:
                kind = "identity" if (h_id and a_id) else "fuzzy"
                if kind == "identity":
                    best_kind = "identity"
                    break
                best_kind = best_kind or "fuzzy"
        if best_kind == "identity":
            exact_fids.append(fid)
        elif best_kind == "fuzzy":
            loose_fids.append(fid)

    if len(exact_fids) == 1:
        return exact_fids[0], "exact", exact_fids, loose_fids
    if len(exact_fids) > 1:
        return None, "ambiguous_exact", exact_fids, loose_fids
    if len(loose_fids) == 1:
        return loose_fids[0], "fuzzy", exact_fids, loose_fids
    if len(loose_fids) > 1:
        return None, "ambiguous_fuzzy", exact_fids, loose_fids
    return None, "none", exact_fids, loose_fids


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
    }
    return mapping.get(league_name.lower())


class OddsScheduler:
    """Drives adaptive scraping of Betclic + Unibet + PMU for upcoming fixtures."""

    async def tick(self, session: AsyncSession) -> tuple[int, list[int]]:
        """Process all fixtures due for a scrape. Returns (count_due, stored_fixture_ids)."""
        from app.ingestion.betclic_grpc_scraper import scrape_betclic_leagues
        from app.ingestion.odds_storage import store_match_scrape_result
        from app.ingestion.pmu_scraper import scrape_all_pmu
        from app.ingestion.unibet_lvs_scraper import scrape_all_unibet
        from app.models.canonical_teams import CanonicalTeam
        from app.models.fixtures import Fixture
        from app.models.odds_scrape_state import OddsScrapeState

        now = datetime.now(UTC)
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

        if not due:
            logger.debug("OddsScheduler.tick: 0 fixtures due")
            return 0, []

        logger.info("OddsScheduler.tick: %d fixtures due for scraping", len(due))

        # Collect leagues needed + per-team match candidates for due fixtures.
        # A single candidate list drives the unified per-team matcher below —
        # each tuple carries both the normalized names AND the canonical ids so
        # a side can match by strict / canonical / fuzzy independently.
        leagues_needed: set[str] = set()
        match_candidates: list[tuple[int, str, int | None, str, int | None]] = []
        for f in due:
            league = _league_key(f.league)
            if league:
                leagues_needed.add(league)
            match_candidates.append(
                (
                    f.id,
                    _normalize_team(f.home_team),
                    f.home_canonical_team_id,
                    _normalize_team(f.away_team),
                    f.away_canonical_team_id,
                )
            )

        if not leagues_needed:
            logger.warning("OddsScheduler.tick: no recognized leagues in due fixtures")
            return 0, []

        # Canonical-team indexes for the per-team matcher. Small table —
        # loading it in full is cheap and avoids missing entries.
        canonical_teams = (await session.execute(select(CanonicalTeam))).scalars().all()
        canonical_alias_map = _build_canonical_alias_map(canonical_teams)
        canonical_names_by_id = _build_canonical_names_by_id(canonical_teams)

        # Scrape les 3 books en parallèle
        betclic_results, unibet_results, pmu_results = await asyncio.gather(
            scrape_betclic_leagues(list(leagues_needed)),
            scrape_all_unibet(list(leagues_needed)),
            scrape_all_pmu(list(leagues_needed)),
            return_exceptions=True,
        )

        all_results = []
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
        for r in all_results:
            # Unified per-team matcher: strict / canonical / fuzzy, each side
            # independent, both sides required, unique-candidate guard.
            fixture_id, reason, exact_fids, loose_fids = _match_result_to_fixture(
                r.home_team, r.away_team, match_candidates,
                canonical_alias_map, canonical_names_by_id,
            )
            if fixture_id:
                logger.info(
                    "OddsScheduler: %s match for '%s' vs '%s' -> fixture %s",
                    reason, r.home_team, r.away_team, fixture_id,
                )
            elif reason in ("ambiguous_exact", "ambiguous_fuzzy"):
                logger.warning(
                    "OddsScheduler: ambiguous match for '%s' vs '%s' skipped "
                    "(ne devine pas) - exact=%s fuzzy=%s",
                    r.home_team, r.away_team, exact_fids, loose_fids,
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
            await store_match_scrape_result(r, session)
            stored_fixture_ids.add(fixture_id)
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
