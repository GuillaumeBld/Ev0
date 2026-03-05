"""
Betclic full scraper via gRPC-web API (offering.begmedia.com).

Endpoint: GetMatchWithNotification
- Accepts: {match_id: int64, language: str}
- Returns: full protobuf with all market selections (buteurs, passeurs)
- No Playwright required; endpoint resolves globally including from VPS.

Odds are stored as IEEE 754 float64 in protobuf field 12 of each selection.
Player name is in field 10 (bytes, UTF-8).
"""
from __future__ import annotations

import codecs
import datetime as _dt
import json
import logging
import re
import struct
import unicodedata
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BOOKMAKER = "betclic"
GRPC_ENDPOINT = (
    "https://offering.begmedia.com/web/offering.access.api"
    "/offering.access.api.MatchService/GetMatchWithNotification"
)
BETCLIC_BASE = "https://www.betclic.fr"

BETCLIC_LEAGUES: dict[str, tuple[str, str]] = {
    "ligue_1":          ("ligue-1-s4",            "4"),
    "ligue_2":          ("ligue-2-s19",            "19"),
    "premier_league":   ("premier-league-s3",      "3"),
    "laliga":           ("laliga-s7",              "7"),
    "bundesliga":       ("bundesliga-s5",           "5"),
    "serie_a":          ("serie-a-s6",             "6"),
    "champions_league": ("champions-league-s15",   "8"),
    "europa_league":    ("ligue-europa-s3453",     "3453"),
    "coupe_de_france":  ("coupe-de-france-s36",    "36"),
}

_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

_GRPC_HEADERS = {
    **_PAGE_HEADERS,
    "content-type": "application/grpc-web+proto",
    "x-grpc-web": "1",
    "x-bg-ref-platform": "DESKTOP",
    "x-bg-regulation": "FR",
    "x-bg-ref-brand": "BETCLIC",
    "x-bg-ref-regulator-zone": "FR",
    "origin": "https://www.betclic.fr",
    "referer": "https://www.betclic.fr/",
}

# Market name fragments → canonical type
_GOALSCORER_LABELS = ("buteur (tps r", "buteur anytime", "scorer anytime")
_ASSIST_LABELS = ("passeur", "joueur d\u00e9cisif", "joueur decisif")

_PAGE_SLEEP = 0.5   # seconds between competition page fetches
_MATCH_SLEEP = 0.3  # seconds between match gRPC calls


# ---------------------------------------------------------------------------
# Encode / decode helpers
# ---------------------------------------------------------------------------

def _encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as protobuf varint."""
    if value < 0:
        raise ValueError(f"_encode_varint requires a non-negative integer, got {value}")
    result = b""
    while value > 0x7F:
        result += bytes([(value & 0x7F) | 0x80])
        value >>= 7
    result += bytes([value & 0x7F])
    return result


def encode_grpc_web_request(match_id: int, language: str = "fr") -> bytes:
    """Build a gRPC-web request body for GetMatchWithNotification.

    Protobuf message:
        field 1 (int64):  match_id
        field 2 (string): language
    """
    lang_bytes = language.encode("utf-8")
    proto = (
        b"\x08" + _encode_varint(match_id)
        + b"\x12" + _encode_varint(len(lang_bytes)) + lang_bytes
    )
    # gRPC-web frame: 1-byte flags (0x00) + 4-byte big-endian length
    return b"\x00" + struct.pack(">I", len(proto)) + proto


def decode_bytes_field(v: Any) -> str:
    """Convert a blackboxprotobuf bytes value to a plain Python str.

    blackboxprotobuf returns bytes fields as repr strings like b'...' when
    serialised through JSON. This function handles both raw bytes and those
    repr strings.
    """
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.decode("latin-1")
    # blackboxprotobuf repr uses single-quotes: b'...' not b"..."
    if isinstance(v, str) and v.startswith("b'") and v.endswith("'"):
        inner = v[2:-1]
        try:
            # Decode \xNN escape sequences: unicode_escape interprets them as
            # latin-1 code points; re-encoding as latin-1 bytes then decoding
            # as UTF-8 recovers the original multi-byte UTF-8 characters.
            return codecs.decode(inner, "unicode_escape").encode("latin-1").decode("utf-8")
        except Exception:
            return inner
    return str(v) if v is not None else ""


def decode_odds_float64(raw: Any) -> float | None:
    """Decode protobuf fixed64 field as IEEE 754 double-precision float.

    Betclic stores decimal odds in field 12 of each selection as a fixed64
    (big-endian IEEE 754 double). Valid odds are between 1.01 and 1000.0.

    Big-endian is correct here because blackboxprotobuf decodes fixed64 fields
    as unsigned integers using network byte order. Verified against a live
    Betclic response: raw=4613419904283925545 → 2.77.
    """
    if raw is None:
        return None
    try:
        val = struct.unpack(">d", struct.pack(">Q", int(raw)))[0]
        if 1.01 <= val <= 1000.0:
            return round(val, 2)
    except (struct.error, ValueError, OverflowError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# HTML / ng-state helpers
# ---------------------------------------------------------------------------


def _slugify(s: str) -> str:
    """URL-slugify a string (lower-case, ASCII-only, hyphens)."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _parse_kickoff(raw: str | None) -> datetime | None:
    """Parse Betclic UTC kickoff string to datetime."""
    if not raw:
        return None
    try:
        # Format: "2026-03-05T20:10:00.0000000Z"
        cleaned = raw.split(".")[0] + "+00:00"  # strip sub-seconds, add tz
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _parse_ng_state(html: str) -> dict:
    """Extract the Angular ng-state JSON from page HTML."""
    m = re.search(
        r'<script[^>]*id="ng-state"[^>]*type="application/json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<script[^>]*type="application/json"[^>]*id="ng-state"[^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Scraper class
# ---------------------------------------------------------------------------


class BetclicGrpcScraper:
    """Scrape Betclic odds via direct gRPC-web API calls to offering.begmedia.com."""

    BOOKMAKER = "betclic"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._grpc_client: httpx.AsyncClient | None = None

    async def fetch_competition_matches(self, league: str) -> list[dict]:
        """Return upcoming match metadata for a league from the competition page ng-state.

        Fetches the Betclic competition page HTML, extracts the Angular ng-state
        JSON, and returns match metadata from the gRPC payload.

        Returns:
            List of dicts with keys: match_id, home_team, away_team,
            kickoff_utc, competition_id, competition_name, league.
            Past matches are excluded.
        """
        league_cfg = BETCLIC_LEAGUES.get(league)
        if not league_cfg:
            logger.warning("BetclicGrpcScraper: unknown league %s", league)
            return []

        slug, _comp_id = league_cfg
        url = f"{BETCLIC_BASE}/football-s1/{slug}/"

        try:
            r = await self._client.get(url, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            logger.warning("BetclicGrpcScraper: page fetch failed %s: %s", url, exc)
            return []

        ng = _parse_ng_state(r.text)
        payload = (
            ng.get("grpc:4011162472", {})
            .get("response", {})
            .get("payload", {})
        )
        if not payload:
            logger.warning(
                "BetclicGrpcScraper: no grpc:4011162472 payload in ng-state for %s", league
            )
            return []

        now = datetime.now(_dt.UTC)
        results: list[dict] = []

        for mx in payload.get("matches", []):
            kickoff = _parse_kickoff(mx.get("matchDateUtc"))
            if kickoff and kickoff < now:
                continue  # skip past matches

            contestants = mx.get("contestants", [])
            if len(contestants) < 2:
                continue

            home = contestants[0].get("name", "")
            away = contestants[1].get("name", "")
            if not home or not away:
                continue

            competition = mx.get("competition", {})
            results.append({
                "match_id": int(mx["matchId"]),
                "home_team": home,
                "away_team": away,
                "kickoff_utc": kickoff,
                "competition_id": competition.get("id", ""),
                "competition_name": competition.get("name", ""),
                "league": league,
            })

        logger.info(
            "BetclicGrpcScraper %s: %d upcoming matches found", league, len(results)
        )
        return results
