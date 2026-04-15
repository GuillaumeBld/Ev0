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

import asyncio
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

from app.ingestion.scrape_result import PlayerOdds

logger = logging.getLogger(__name__)

BOOKMAKER = "betclic"
GRPC_ENDPOINT = (
    "https://offering.begmedia.com/web/offering.access.api"
    "/offering.access.api.MatchService/GetMatchWithNotification"
)
BETCLIC_BASE = "https://www.betclic.fr"

BETCLIC_LEAGUES: dict[str, tuple[str, str]] = {
    "ligue_1":          ("ligue-1-mcdonald-s-c4",  "4"),
    "ligue_2":          ("ligue-2-bkt-c19",         "19"),
    "premier_league":   ("premier-league-c3",       "3"),
    "laliga":           ("laliga-c7",               "7"),
    "bundesliga":       ("allemagne-bundesliga-c5", "5"),
    "serie_a":          ("italie-serie-a-c6",       "6"),
    "champions_league": ("ligue-des-champions-c8",  "8"),
    "europa_league":    ("ligue-europa-c3453",      "3453"),
    "coupe_de_france":  ("coupe-de-france-c36",     "36"),
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
_GOALSCORER_LABELS = ("buteur (tps r", "buteur anytime", "scorer anytime")  # used by fetch_match_odds
_ASSIST_LABELS = (
    "joueur passeur d\u00e9cisif",   # Joueur passeur décisif (tps rég.)
    "joueur passeur decisif",         # sans accent
    "passeur d\u00e9cisif",           # fragment générique
    "passeur decisif",
)  # "joueur décisif" exclu : c'est le combo buteur OU passeur, pas un marché assist pur
_H2H_LABELS = ("résultat du match",)
_TOTALS_LABELS = ("nombre total de buts",)
_BTTS_LABELS = ("les 2 équipes marquent",)

_PAGE_SLEEP = 0.5   # between competition page fetches (used by scrape_betclic_leagues)
_MATCH_SLEEP = 0.3  # between match gRPC calls (used by scrape_league)


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
# gRPC-web frame / protobuf helpers
# ---------------------------------------------------------------------------


def _decode_grpc_web_frames(data: bytes) -> list[bytes]:
    """Extract data frame payloads from a gRPC-web response body.

    gRPC-web framing: each frame is 1-byte flags + 4-byte big-endian length + payload.
    Flags=0x00 = data frame. Flags=0x80 = trailers (skip).
    """
    frames = []
    pos = 0
    while pos + 5 <= len(data):
        flags = data[pos]
        length = struct.unpack(">I", data[pos + 1 : pos + 5])[0]
        payload = data[pos + 5 : pos + 5 + length]
        if flags == 0x00:  # Data frame (not trailers)
            frames.append(payload)
        pos += 5 + length
    return frames


def _classify_market(name: str) -> str | None:
    """Return canonical market type or None if not a recognized market."""
    lower = name.lower()
    if any(x in lower for x in _GOALSCORER_LABELS):
        return "goalscorer"
    if any(x in lower for x in _ASSIST_LABELS):
        return "assist"
    if any(x in lower for x in _H2H_LABELS):
        return "h2h"
    if any(x in lower for x in _TOTALS_LABELS):
        return "totals"
    if any(x in lower for x in _BTTS_LABELS):
        return "btts"
    return None


async def _stream_first_grpc_frame(
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    timeout: httpx.Timeout,
) -> bytes:
    """POST a gRPC-web request and stream the response until the first data frame is complete.

    The server sends the response as chunked transfer-encoding and may keep the
    connection open after the last data frame (waiting to send trailers).  We
    explicitly close the response as soon as we have the complete first data
    frame to avoid blocking in the context-manager __aexit__ drain.

    Returns the raw payload bytes of the first data frame, or b'' if none found.
    """
    data = b""
    needed: int | None = None  # total bytes required for the first complete frame

    async with client.stream(
        "POST", url, content=body, headers=_GRPC_HEADERS, timeout=timeout
    ) as r:
        r.raise_for_status()
        async for chunk in r.aiter_bytes():
            data += chunk
            if needed is None and len(data) >= 5:
                flags = data[0]
                frame_len = struct.unpack(">I", data[1:5])[0]
                needed = 5 + frame_len
                if flags != 0x00:
                    # First frame is a trailer — nothing useful
                    await r.aclose()
                    return b""
            if needed is not None and len(data) >= needed:
                # We have the complete first data frame — close immediately
                # to avoid blocking in __aexit__ while the server keeps sending.
                await r.aclose()
                break

    if needed is None or len(data) < needed:
        return b""

    # Return the payload bytes (strip the 5-byte gRPC-web header)
    frame_len = struct.unpack(">I", data[1:5])[0]
    return data[5 : 5 + frame_len]


def _proto_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf varint. Returns (value, new_pos)."""
    result = shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def _proto_fields(data: bytes) -> dict[int, list]:
    """Parse all fields from a protobuf message into {field_num: [values]}.

    Values are:
      - int  for wire type 0 (varint)
      - bytes for wire type 1 (8 bytes, 64-bit)
      - bytes for wire type 2 (length-delimited)
      - bytes for wire type 5 (4 bytes, 32-bit)
    """
    out: dict[int, list] = {}
    pos = 0
    n = len(data)
    while pos < n:
        tag, pos = _proto_varint(data, pos)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            val, pos = _proto_varint(data, pos)
        elif wt == 1:
            val = data[pos: pos + 8]
            pos += 8
        elif wt == 2:
            ln, pos = _proto_varint(data, pos)
            val = data[pos: pos + ln]
            pos += ln
        elif wt == 5:
            val = data[pos: pos + 4]
            pos += 4
        else:
            break  # unknown wire type — stop
        out.setdefault(fn, []).append(val)
    return out


def _parse_match_proto(
    proto_bytes: bytes,
    home_team: str = "",
    away_team: str = "",
) -> dict:
    """Parse all markets from a GetMatchWithNotification protobuf response.

    Returns a dict with keys:
      h2h, totals, btts (dict | None)
      goalscorer, assist (list[PlayerOdds])

    Wire structure (reverse-engineered):
      root.f1.f1.f11.f3[]  = markets
      market.f2            = market name (bytes, UTF-8)
      market.f9            = state varint (3 = suspended → skip)

      Player markets (goalscorer/assist) — field10/field11 path:
        market.f11[] = team groups
        group.f2[]   = selections
        sel.f10|f11  = player name, sel.f12 = odds
        (field10 = first contestant's players, field11 = second contestant's
        players; we try both because either may be absent)

      Match-level markets (h2h/totals/btts) — field10 path:
        market.f10[] = items
        item.f1[]    = sub-items
        sub.f1[]     = selections
        sel.f10|f11  = label, sel.f12 = odds (8-byte IEEE 754 LE double)

    Odds encoding note:
      sel.f12 arrives here as raw 8 bytes from _proto_fields wire type 1,
      decoded with little-endian ("<d").  This differs from decode_odds_float64,
      which receives a blackboxprotobuf integer (network/big-endian fixed64) and
      therefore uses big-endian (">d").
    """
    out: dict = {
        "h2h": None, "totals": None, "btts": None,
        "goalscorer": [], "assist": [],
    }

    try:
        root  = _proto_fields(proto_bytes)
        f1    = _proto_fields(root[1][0])
        f1f1  = _proto_fields(f1[1][0])
        f11   = _proto_fields(f1f1[11][0])
        markets = f11.get(3, [])
    except (KeyError, IndexError, ValueError):
        logger.warning("BetclicGrpcScraper: unexpected protobuf structure")
        return out

    for market_bytes in markets:
        try:
            market = _proto_fields(market_bytes)
        except Exception:
            continue

        name_raw = market.get(2, [b""])[0]
        market_name = name_raw.decode("utf-8", errors="replace") if name_raw else ""
        market_type = _classify_market(market_name)
        if not market_type:
            continue
        if market.get(9, [0])[0] == 3:  # suspended
            continue

        sels: list[tuple[str, float]] = []

        if market_type in ("goalscorer", "assist"):
            # Player markets: market.field11[] → group.field2[] → sel
            for group_bytes in market.get(11, []):
                try:
                    group = _proto_fields(group_bytes)
                except Exception:
                    continue
                for sel_bytes in group.get(2, []):
                    try:
                        sel = _proto_fields(sel_bytes)
                    except Exception:
                        continue
                    name_b = (sel.get(10) or sel.get(11) or [None])[0]
                    if not name_b:
                        continue
                    sel_name = name_b.decode("utf-8", errors="replace").strip()
                    odds_raw = (sel.get(12) or [None])[0]
                    if not odds_raw or len(odds_raw) != 8:
                        continue
                    try:
                        val = struct.unpack("<d", odds_raw)[0]  # raw bytes from wire type 1 — LE, unlike decode_odds_float64 which takes an int
                        if 1.01 <= val <= 1000.0:
                            sels.append((sel_name, round(val, 2)))
                    except struct.error:
                        continue
        else:
            # Match-level markets: market.field10[] → item.field1[] → sub.field1[] → sel
            for item_bytes in market.get(10, []):
                try:
                    item = _proto_fields(item_bytes)
                except Exception:
                    continue
                for sub_bytes in item.get(1, []):
                    try:
                        sub = _proto_fields(sub_bytes)
                    except Exception:
                        continue
                    for sel_bytes in sub.get(1, []):
                        try:
                            sel = _proto_fields(sel_bytes)
                        except Exception:
                            continue
                        name_b = (sel.get(10) or sel.get(11) or [None])[0]
                        if not name_b:
                            continue
                        sel_name = name_b.decode("utf-8", errors="replace").strip()
                        odds_raw = (sel.get(12) or [None])[0]
                        if not odds_raw or len(odds_raw) != 8:
                            continue
                        try:
                            val = struct.unpack("<d", odds_raw)[0]  # raw bytes from wire type 1 — LE, unlike decode_odds_float64 which takes an int
                            if 1.01 <= val <= 1000.0:
                                sels.append((sel_name, round(val, 2)))
                        except struct.error:
                            continue

        # Populate output based on market type
        if market_type == "goalscorer":
            out["goalscorer"].extend(
                PlayerOdds(n, o) for n, o in sels if n and n.lower() != "yes"
            )
        elif market_type == "assist":
            out["assist"].extend(
                PlayerOdds(n, o) for n, o in sels if n and n.lower() != "yes"
            )
        elif market_type == "h2h" and len(sels) == 3:
            # Betclic order: home, draw, away
            out["h2h"] = {"home": sels[0][1], "draw": sels[1][1], "away": sels[2][1]}
        elif market_type == "totals" and sels:
            totals: dict = {}
            for name, odds in sels:
                # French format: "+ de 2,5" / "- de 2,5"
                # Normalize comma to dot for threshold matching
                n = name.replace(",", ".")
                if "1.5" in n:
                    key = "over_1.5" if n.strip().startswith("+") else "under_1.5"
                elif "2.5" in n:
                    key = "over_2.5" if n.strip().startswith("+") else "under_2.5"
                elif "3.5" in n:
                    key = "over_3.5" if n.strip().startswith("+") else "under_3.5"
                elif "4.5" in n:
                    key = "over_4.5" if n.strip().startswith("+") else "under_4.5"
                else:
                    continue
                totals[key] = odds
            if totals:
                out["totals"] = totals
        elif market_type == "btts" and len(sels) == 2:
            out["btts"] = {"yes": sels[0][1], "no": sels[1][1]}

    return out


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
        # Format: "2026-03-05T20:10:00.0000000Z" or "2026-03-05T20:10:00Z"
        cleaned = raw.replace("Z", "").split(".")[0] + "+00:00"  # strip Z, sub-seconds; add tz
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
        self._grpc_client: httpx.AsyncClient | None = None  # set by scrape_betclic_leagues for gRPC calls

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

        slug, comp_id = league_cfg
        url = f"{BETCLIC_BASE}/football-sfootball/{slug}/"

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

            match_id_raw = mx.get("matchId")
            if not match_id_raw:
                continue
            competition = mx.get("competition", {})
            # Filter to only the requested competition to avoid parsing protobufs
            # from other competitions (the page shows all upcoming matches).
            if competition.get("id", "") != comp_id:
                continue
            results.append({
                "match_id": int(match_id_raw),
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

    async def fetch_match_odds(
        self,
        match_id: int,
        home_team: str,
        away_team: str,
        league: str,
        fixture_id: int = 0,
        language: str = "fr",
    ) -> "MatchScrapeResult | None":
        from app.ingestion.scrape_result import MatchScrapeResult

        body = encode_grpc_web_request(match_id, language)
        client = self._grpc_client if self._grpc_client is not None else self._client
        timeout = httpx.Timeout(30.0, connect=10.0)

        try:
            raw = await _stream_first_grpc_frame(client, GRPC_ENDPOINT, body, timeout)
        except Exception as exc:
            logger.warning(
                "BetclicGrpcScraper: gRPC call failed for match %d (%s v %s): %s",
                match_id, home_team, away_team, exc,
            )
            return None

        if not raw:
            logger.warning(
                "BetclicGrpcScraper: empty gRPC-web response for match %d", match_id
            )
            return None

        parsed = _parse_match_proto(raw, home_team, away_team)
        result = MatchScrapeResult(
            fixture_id=fixture_id,
            home_team=home_team,
            away_team=away_team,
            kickoff_utc=None,  # set by caller from match metadata
            league=league,
            bookmaker=BOOKMAKER,
            scraped_at=datetime.now(_dt.UTC),
            h2h=parsed["h2h"],
            totals=parsed["totals"],
            btts=parsed["btts"],
            goalscorer=parsed["goalscorer"],
            assist=parsed["assist"],
        )
        logger.debug(
            "BetclicGrpcScraper: match %d (%s v %s): goalscorer=%d h2h=%s totals=%s btts=%s",
            match_id, home_team, away_team,
            len(result.goalscorer),
            "OK" if result.h2h else "—",
            "OK" if result.totals else "—",
            "OK" if result.btts else "—",
        )
        return result

    async def scrape_league(self, league: str) -> list:
        """Scrape all upcoming matches for a league: match list → gRPC per match."""
        from app.ingestion.scrape_result import MatchScrapeResult
        matches = await self.fetch_competition_matches(league)
        if not matches:
            return []

        results: list[MatchScrapeResult] = []

        for i, mx in enumerate(matches):
            result = await self.fetch_match_odds(
                mx["match_id"], mx["home_team"], mx["away_team"], league
            )
            if result:
                result.kickoff_utc = mx.get("kickoff_utc")
            if result and result.goalscorer:
                results.append(result)
            if i < len(matches) - 1:
                await asyncio.sleep(_MATCH_SLEEP)

        total_sel = sum(len(r.goalscorer) for r in results)
        logger.info(
            "BetclicGrpcScraper %s: %d matches, %d total goalscorer selections",
            league, len(results), total_sel,
        )
        return results


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------


async def scrape_betclic_leagues(leagues: list[str] | None = None) -> list:
    """Scrape all odds from Betclic via gRPC-web for the given leagues.

    Default: all leagues in BETCLIC_LEAGUES.
    Returns MatchScrapeResult objects with goalscorer + match-level odds.
    """
    from app.ingestion.scrape_result import MatchScrapeResult

    if leagues is None:
        leagues = list(BETCLIC_LEAGUES.keys())

    async with (
        httpx.AsyncClient(headers=_PAGE_HEADERS, follow_redirects=True) as page_client,
        httpx.AsyncClient(headers=_GRPC_HEADERS, follow_redirects=True) as grpc_client,
    ):
        scraper = BetclicGrpcScraper(page_client)
        scraper._grpc_client = grpc_client

        all_results: list[MatchScrapeResult] = []
        for i, league in enumerate(leagues):
            try:
                results = await scraper.scrape_league(league)
                all_results.extend(results)
            except Exception as exc:
                logger.error(
                    "BetclicGrpcScraper: league %s failed: %s", league, exc,
                    exc_info=True,
                )
            if i < len(leagues) - 1:
                await asyncio.sleep(_PAGE_SLEEP)

    logger.info(
        "scrape_betclic_leagues: %d total matches, %d leagues",
        len(all_results), len(leagues),
    )
    return all_results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Betclic gRPC-web full scraper")
    parser.add_argument(
        "--league",
        choices=list(BETCLIC_LEAGUES.keys()),
        default=None,
        help="Scrape a single league (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without storing to DB",
    )
    args = parser.parse_args()

    leagues = [args.league] if args.league else None
    results = asyncio.run(scrape_betclic_leagues(leagues))

    total_goalscorer = sum(len(r.goalscorer) for r in results)
    print(f"\n{'='*60}")
    print(f"Betclic gRPC scrape: {len(results)} matches, {total_goalscorer} goalscorer selections")
    print(f"{'='*60}")
    for r in results:
        print(f"\n{r.home_team} vs {r.away_team} ({r.league})")
        print(f"  h2h:     {r.h2h}")
        print(f"  totals:  {r.totals}")
        print(f"  btts:    {r.btts}")
        print(f"  goalscorer: {len(r.goalscorer)} players")
        print(f"  assist:     {len(r.assist)} players")
