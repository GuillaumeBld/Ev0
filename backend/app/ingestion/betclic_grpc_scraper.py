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
import logging
import re
import struct
import unicodedata
from datetime import datetime
from typing import Any

import httpx

from app.ingestion.book_competitions import BETCLIC_IDS
from app.ingestion.scrape_result import AVEC_SUB, SANS_SUB, PlayerOdds, prefer_avec_sub

logger = logging.getLogger(__name__)

BOOKMAKER = "betclic"
GRPC_ENDPOINT = (
    "https://offering.begmedia.com/web/offering.access.api"
    "/offering.access.api.MatchService/GetMatchWithNotification"
)
GRPC_COMPETITION_ENDPOINT = (
    "https://offering.begmedia.com/web/offering.access.api"
    "/offering.access.api.MatchService/GetMatchesByCompetitionWithNotifications"
)

# Maps scheduler league key → Betclic competition_id (int).
# Used by fetch_competition_matches to call GetMatchesByCompetitionWithNotifications.
BETCLIC_COMPETITION_IDS: dict[str, int] = {
    "ligue_1":               4,
    "premier_league":        3,
    "la_liga":               7,
    "bundesliga":            5,
    "serie_a":               6,
    "champions_league":      8,
    "europa_league":         3453,
    "ligue_2":               19,
    "coupe_de_france":       36,
    "friendly_international": 79,
    "world_cup_2026":        1,
    "uefa_super_cup":        228,  # Supercoupe de l'UEFA — buteur + cotes équipe (passeur pur non exposé dans le flux gRPC)
}
# + toutes les compétitions de la table commune (clé « comp_<id> »).
BETCLIC_COMPETITION_IDS.update(BETCLIC_IDS)

_GRPC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "content-type": "application/grpc-web+proto",
    "x-grpc-web": "1",
    "x-bg-ref-platform": "DESKTOP",
    "x-bg-regulation": "FR",
    "x-bg-ref-brand": "BETCLIC",
    "x-bg-ref-regulator-zone": "FR",
    "origin": "https://www.betclic.fr",
    "referer": "https://www.betclic.fr/",
}

# Marchés joueur : nom EXACT (une fois normalisé) -> (type, variante).
#
# Le nom exact, et non un fragment : Betclic expose une quinzaine de marchés
# dont le nom contient « buteur » ou « passeur décisif » (extra gains, double
# chance, buteur 2 fois, buteur ET passeur...) et un fragment en attrape
# toujours un de trop. Si Betclic renomme un marché, il n'est plus reconnu et
# fetch_match_odds le signale dans les logs : jamais de cote d'un autre pari.
#
# Règle du 23/11/2026 : la cote « ou son remplaçant » (avec sub) est celle
# qu'on veut ; la cote sans sub n'est gardée que si l'autre n'existe pas.
_PLAYER_MARKETS: dict[str, tuple[str, str]] = {
    "buteur ou son remplacant (tr)": ("goalscorer", AVEC_SUB),
    "buteur (tr)": ("goalscorer", SANS_SUB),
    "buteur anytime": ("goalscorer", SANS_SUB),
    "scorer anytime": ("goalscorer", SANS_SUB),
    "joueur passeur decisif + son remplacant (tr)": ("assist", AVEC_SUB),
    "joueur passeur decisif (tr)": ("assist", SANS_SUB),
}

# Un marché « à colonnes » (1 fois ou + / 2 fois ou + ; ou encore & son équipe
# gagne / fait match nul / perd) porte le même nom qu'un marché simple. Seule
# la colonne « 1 fois ou + » est le pari « marque / fait une passe ».
_ANYTIME_COLUMN = "1 fois ou +"

# Onglet « SuperSub » du site : c'est là que Betclic range le passeur avec
# remplaçant, absent de la réponse par défaut.
BETCLIC_SUPERSUB_TEMPLATE = "ca_ftb_pssb"

_TEMPS_REGLEMENTAIRE_RE = re.compile(r"\(\s*t(?:ps)?\.?\s*reg\.?\s*\)")


def _norm_label(name: str) -> str:
    """Nom de marché plié : minuscules, sans accent, « (t. rég) » et
    « (tps rég.) » ramenés à la même écriture « (tr) »."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = _TEMPS_REGLEMENTAIRE_RE.sub("(tr)", s)
    return re.sub(r"\s+", " ", s).strip()


_H2H_LABELS = ("résultat du match",)
_TOTALS_LABELS = ("nombre total de buts",)
_BTTS_LABELS = ("les 2 équipes marquent",)

_MATCH_SLEEP = 0.3  # between match gRPC calls (used by scrape_league)
# Un match de la liste est lu s'il démarre à moins de ça d'un match attendu.
_KICKOFF_WINDOW = _dt.timedelta(hours=3)


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


# Market-template code (field 3) requested by the site's "Stats équipes et
# joueurs" tab. Without it, GetMatchWithNotification returns only the main
# markets (no pure "Joueur passeur décisif"); with it, the response carries the
# player-props markets, including the pure passeur market.
BETCLIC_PROPS_TEMPLATE = "ca_ftb_prp"


def encode_grpc_web_request(
    match_id: int, language: str = "fr", template: str | None = None
) -> bytes:
    """Build a gRPC-web request body for GetMatchWithNotification.

    Protobuf message:
        field 1 (int64):  match_id
        field 2 (string): language
        field 3 (string): market-template code (optional; e.g. player props)
    """
    lang_bytes = language.encode("utf-8")
    proto = (
        b"\x08" + _encode_varint(match_id)
        + b"\x12" + _encode_varint(len(lang_bytes)) + lang_bytes
    )
    if template:
        tmpl_bytes = template.encode("utf-8")
        proto += b"\x1a" + _encode_varint(len(tmpl_bytes)) + tmpl_bytes
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


def _classify_player_market(name: str) -> tuple[str, str] | None:
    """(type, variante) d'un marché joueur reconnu, sinon None."""
    return _PLAYER_MARKETS.get(_norm_label(name))


def _classify_market(name: str) -> str | None:
    """Return canonical market type or None if not a recognized market."""
    player = _classify_player_market(name)
    if player:
        return player[0]
    lower = name.lower()
    if any(x in lower for x in _H2H_LABELS):
        return "h2h"
    if any(x in lower for x in _TOTALS_LABELS):
        return "totals"
    if any(x in lower for x in _BTTS_LABELS):
        return "btts"
    return None


# Autres paris joueur de Betclic, connus et volontairement ignorés (relevé du
# 24/11/2026). Tout marché « buteur » / « passeur décisif » qui n'est ni dans
# _PLAYER_MARKETS ni ici est un nom nouveau : il est signalé.
_IGNORED_PLAYER_MARKETS: frozenset[str] = frozenset({
    "buteur (tr) - extra gains a chaque but du joueur",
    "buteur + son remplacant (tr)",
    "buteur et passeur decisif",
    "buteur et passeur decisif (titulaire et remplacant cumules - t. reg)",
    "decisif (buteur ou passeur dans le t. reg) - extra gains a chaque action decisive du joueur",
    "double chance - passeur decisif",
    "double chance buteur (remplacants inclus - t. reg)",
    "double chance buteur (tr)",
    "double chance decisif (buteur ou passeur decisif dans le t. reg)",
    "joueur decisif (buteur ou passeur decisif dans le t. reg)",
    "joueur decisif (buteur ou passeur decisif) + son remplacant (tr)",
    "joueur ou son remplacant 1er buteur (tr)",
    "les deux joueurs marquent (remplacants inclus - t. reg)",
    "les deux joueurs sont decisifs (buteur ou passeur decisif)",
    "passeur decisif (tr)",
    "passeur decisif (tr) - extra gains a chaque passe decisive du joueur",
    "triple chance - passeur decisif",
    "un remplacant marque un but (tr)",
})


def _looks_like_player_market(name: str) -> bool:
    """Marché buteur/passeur que ni la table ni la liste des ignorés ne
    connaissent : Betclic a renommé ou ajouté un marché (voir fetch_match_odds)."""
    n = _norm_label(name)
    if n in _IGNORED_PLAYER_MARKETS:
        return False
    return "buteur" in n or "passeur decisif" in n


def _unknown_kind(name: str) -> str:
    """Type de cote qu'un marché inconnu pourrait porter."""
    return "assist" if "passeur" in _norm_label(name) else "goalscorer"


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
        # Noms des marchés buteur/passeur non reconnus (renommage Betclic).
        "unknown_player_markets": [],
        # Marchés reconnus dont aucune colonne n'est lisible (libellé changé).
        "unreadable_player_markets": [],
    }

    try:
        root  = _proto_fields(proto_bytes)
        f1    = _proto_fields(root[1][0])
        f1f1  = _proto_fields(f1[1][0])
    except (KeyError, IndexError, ValueError):
        logger.warning("BetclicGrpcScraper: unexpected protobuf structure")
        return out

    # f1f1.field11 is a list of market-group containers. The default response
    # keeps every market in the first container; the player-props response
    # (template ca_ftb_prp) spreads them across several containers — iterate all.
    markets: list = []
    for container_bytes in f1f1.get(11, []):
        try:
            container = _proto_fields(container_bytes)
        except Exception:
            continue
        markets.extend(container.get(3, []))

    for market_bytes in markets:
        try:
            market = _proto_fields(market_bytes)
        except Exception:
            continue

        name_raw = market.get(2, [b""])[0]
        market_name = name_raw.decode("utf-8", errors="replace") if name_raw else ""
        market_type = _classify_market(market_name)
        if not market_type:
            if _looks_like_player_market(market_name):
                out["unknown_player_markets"].append(market_name)
            continue

        sels: list[tuple[str, float]] = []

        if market_type in ("goalscorer", "assist"):
            # Marché simple : market.field11[] → group.field2[] → sel
            # Marché à colonnes : market.field13[] = une colonne, portant son
            #   libellé en field2 (« 1 fois ou + », « & son équipe gagne »...) ;
            #   colonne.field11[] → group.field2[] → sel.
            # Seule la colonne « 1 fois ou + » est le pari voulu. Un marché à
            # colonnes sans cette colonne (« Buteur ou son remplaçant » de
            # l'onglet Joueurs & résultats = buteur ET résultat du match) est
            # écarté en entier.
            source_groups: list[dict] = []
            for group_bytes in market.get(11, []):
                try:
                    source_groups.append(_proto_fields(group_bytes))
                except Exception:
                    continue
            if not source_groups:
                column_labels: list[str] = []
                for outer_bytes in market.get(13, []):
                    try:
                        outer = _proto_fields(outer_bytes)
                    except Exception:
                        continue
                    label_b = (outer.get(2) or [b""])[0]
                    label = label_b.decode("utf-8", errors="replace") if isinstance(label_b, bytes) else ""
                    column_labels.append(label)
                    if _norm_label(label) != _ANYTIME_COLUMN:
                        continue
                    for inner_bytes in outer.get(11, []):
                        try:
                            source_groups.append(_proto_fields(inner_bytes))
                        except Exception:
                            continue

            seen_sel_names: set[str] = set()
            for group in source_groups:
                for sel_bytes in group.get(2, []):
                    try:
                        sel = _proto_fields(sel_bytes)
                    except Exception:
                        continue
                    name_b = (sel.get(10) or sel.get(11) or [None])[0]
                    if not name_b:
                        continue
                    sel_name = name_b.decode("utf-8", errors="replace").strip()
                    if sel_name in seen_sel_names:
                        continue
                    seen_sel_names.add(sel_name)
                    odds_raw = (sel.get(12) or [None])[0]
                    if not odds_raw or len(odds_raw) != 8:
                        continue
                    try:
                        val = struct.unpack("<d", odds_raw)[0]  # raw bytes from wire type 1 — LE
                        if 1.01 <= val <= 1000.0:
                            sels.append((sel_name, round(val, 2)))
                    except struct.error:
                        continue
        else:
            # Match-level markets: try f16 flat path first (newer API layout),
            # then fall back to f10 nested path (older layout used by totals/btts).
            #
            # f16 path: market.f16[] = direct selections (h2h uses this layout)
            #   sel.f10|f11 = label bytes, sel.f12 = IEEE 754 LE double (8 bytes)
            for sel_bytes in market.get(16, []):
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
                    val = struct.unpack("<d", odds_raw)[0]
                    if 1.01 <= val <= 1000.0:
                        sels.append((sel_name, round(val, 2)))
                except struct.error:
                    continue

            # f10 nested path: market.f10[] → item.f1[] → sub.f1[] → sel
            if not sels:
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
                                val = struct.unpack("<d", odds_raw)[0]
                                if 1.01 <= val <= 1000.0:
                                    sels.append((sel_name, round(val, 2)))
                            except struct.error:
                                continue

        # Populate output based on market type
        if market_type in ("goalscorer", "assist"):
            # Marché à colonnes sans colonne « 1 fois ou + ». Normal pour le
            # jumeau « & son équipe gagne / fait match nul / perd » ; sinon
            # Betclic a changé le libellé de la colonne : à signaler.
            if not sels and not market.get(11) and market.get(13):
                if not all(_norm_label(c).startswith("& son equipe") for c in column_labels):
                    out["unreadable_player_markets"].append(f"{market_name} {column_labels}")
            variant = _classify_player_market(market_name)[1]
            out[market_type].extend(
                PlayerOdds(n, o, variant) for n, o in sels if n and n.lower() != "yes"
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

    # Une cote par joueur : avec sub si le marché existe, sinon sans sub.
    out["goalscorer"] = prefer_avec_sub(out["goalscorer"])
    out["assist"] = prefer_avec_sub(out["assist"])
    return out


# ---------------------------------------------------------------------------
# Competition matches helper
# ---------------------------------------------------------------------------


def _parse_kickoff(raw: bytes | None) -> datetime | None:
    """Parse Betclic UTC kickoff bytes to datetime.

    Format: b"2026-03-05T20:10:00.0000000Z"
    """
    if not raw or not isinstance(raw, bytes):
        return None
    try:
        s = raw.decode("utf-8")
        cleaned = s.replace("Z", "").split(".")[0] + "+00:00"
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


async def _fetch_competition_matches_grpc(
    client: httpx.AsyncClient,
    competition_id: int,
    league: str,
    language: str = "fr",
) -> list[dict]:
    """Call GetMatchesByCompetitionWithNotifications and return upcoming match metadata.

    Returns:
        List of dicts with keys: match_id (int), home_team, away_team,
        kickoff_utc (datetime), league (str).
        Past matches are excluded.

    Wire format of GetCompetitionRequest (input message):
        field 1 (int64):  competition_id
        field 3 (string): language
    Response proto structure (first data frame):
        root.f1 = payload message
        payload.f3 = repeated match messages
        match.f1 = match_id (int64/varint)
        match.f2 = "Home - Away" (bytes, UTF-8)
        match.f3 = kickoff UTC string (bytes, UTF-8)
    """
    lang_b = language.encode("utf-8")
    proto = (
        b"\x08" + _encode_varint(competition_id)
        + b"\x1a" + _encode_varint(len(lang_b)) + lang_b
    )
    body = b"\x00" + struct.pack(">I", len(proto)) + proto

    timeout = httpx.Timeout(30.0, connect=10.0)
    try:
        raw = await _stream_first_grpc_frame(client, GRPC_COMPETITION_ENDPOINT, body, timeout)
    except Exception as exc:
        logger.warning(
            "BetclicGrpcScraper: competition fetch failed for comp_id=%d (%s): %s",
            competition_id, league, exc,
        )
        return []

    if not raw:
        logger.warning(
            "BetclicGrpcScraper: empty competition response for comp_id=%d (%s)",
            competition_id, league,
        )
        return []

    try:
        root = _proto_fields(raw)
        payload = _proto_fields(root[1][0])
        match_messages = payload.get(3, [])
    except (KeyError, IndexError, ValueError):
        logger.warning(
            "BetclicGrpcScraper: unexpected competition proto structure for comp_id=%d",
            competition_id,
        )
        return []

    now = datetime.now(_dt.UTC)
    results: list[dict] = []

    for m_bytes in match_messages:
        try:
            mx = _proto_fields(m_bytes)
        except Exception:
            continue

        match_id = mx.get(1, [None])[0]
        if not isinstance(match_id, int):
            continue

        name_raw = mx.get(2, [b""])[0]
        name = name_raw.decode("utf-8", errors="replace") if isinstance(name_raw, bytes) else ""
        kickoff = _parse_kickoff(mx.get(3, [None])[0])

        if not name or not kickoff or kickoff < now:
            continue

        # Match name format: "Home Team - Away Team"
        parts = name.split(" - ", 1)
        if len(parts) != 2:
            continue

        home, away = parts
        results.append({
            "match_id": match_id,
            "home_team": home,
            "away_team": away,
            "kickoff_utc": kickoff,
            "league": league,
        })

    return results


# ---------------------------------------------------------------------------
# Scraper class
# ---------------------------------------------------------------------------


class BetclicGrpcScraper:
    """Scrape Betclic odds via direct gRPC-web API calls to offering.begmedia.com."""

    BOOKMAKER = "betclic"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_competition_matches(self, league: str) -> list[dict]:
        """Return upcoming match metadata for a league via GetMatchesByCompetitionWithNotifications.

        Uses the gRPC endpoint directly (no HTML scraping) to get all matches
        for the requested competition across the next 2 matchdays (~18–20 matches).

        Returns:
            List of dicts with keys: match_id (int), home_team, away_team,
            kickoff_utc (datetime), league (str).
            Past matches are excluded.
        """
        competition_id = BETCLIC_COMPETITION_IDS.get(league)
        if not competition_id:
            logger.warning("BetclicGrpcScraper: unknown league %s", league)
            return []

        results = await _fetch_competition_matches_grpc(self._client, competition_id, league)
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
        timeout = httpx.Timeout(30.0, connect=10.0)

        try:
            raw = await _stream_first_grpc_frame(self._client, GRPC_ENDPOINT, body, timeout)
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

        # Deuxième appel, onglet SuperSub : le passeur « + son remplaçant »
        # n'est que là. Troisième appel (onglet Stats équipes et joueurs)
        # seulement si le match n'a pas de passeur avec sub : la cote sans sub
        # est un second recours. Aucun des deux n'est bloquant.
        async def _extra(template: str) -> dict | None:
            """Marchés d'un autre onglet ; None si l'appel a échoué."""
            try:
                raw_extra = await _stream_first_grpc_frame(
                    self._client, GRPC_ENDPOINT,
                    encode_grpc_web_request(match_id, language, template=template),
                    timeout,
                )
            except Exception as exc:
                logger.warning(
                    "BetclicGrpcScraper: appel %s échoué pour le match %d (%s v %s): %s",
                    template, match_id, home_team, away_team, exc,
                )
                return None
            if not raw_extra:
                return {"goalscorer": [], "assist": [],
                        "unknown_player_markets": [], "unreadable_player_markets": []}
            return _parse_match_proto(raw_extra, home_team, away_team)

        unknown: list[str] = list(parsed["unknown_player_markets"])
        unreadable: list[str] = list(parsed["unreadable_player_markets"])
        supersub = await _extra(BETCLIC_SUPERSUB_TEMPLATE)
        if supersub is None:
            # Panne passagère de l'onglet SuperSub : on n'écrit AUCUNE cote
            # passeur ce tour-ci. Retomber sur le sans sub remplacerait les
            # cotes avec sub déjà enregistrées, ferait expirer les recos puis
            # les recréerait au tour suivant.
            parsed["assist"] = []
        else:
            parsed["goalscorer"] = prefer_avec_sub(parsed["goalscorer"] + supersub["goalscorer"])
            parsed["assist"] = prefer_avec_sub(parsed["assist"] + supersub["assist"])
            unknown += supersub["unknown_player_markets"]
            unreadable += supersub["unreadable_player_markets"]
            if not any(p.variant == AVEC_SUB for p in parsed["assist"]):
                props = await _extra(BETCLIC_PROPS_TEMPLATE)
                if props:
                    parsed["assist"] = prefer_avec_sub(parsed["assist"] + props["assist"])
                    unknown += props["unknown_player_markets"]
                    unreadable += props["unreadable_player_markets"]

        # Jamais d'échec silencieux. Un marché buteur/passeur inconnu, ou un
        # marché connu devenu illisible, alors que ce match n'a pas de cote
        # avec sub de ce type : Betclic a renommé quelque chose et on est
        # retombé (ou on va retomber) sur le sans sub sans le voir. À corriger
        # dans _PLAYER_MARKETS / _IGNORED_PLAYER_MARKETS, jamais à deviner.
        for kind in ("goalscorer", "assist"):
            if any(p.variant == AVEC_SUB for p in parsed[kind]):
                continue
            suspects = sorted(
                {n for n in unknown if _unknown_kind(n) == kind}
                | {n for n in unreadable if _unknown_kind(n) == kind}
            )
            if suspects:
                logger.warning(
                    "BetclicGrpcScraper: match %d (%s v %s) — aucune cote %s avec sub, "
                    "marché(s) non reconnu(s) ou illisible(s) : %s",
                    match_id, home_team, away_team, kind, suspects,
                )

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

    async def scrape_league(self, league: str, wanted_kickoffs: list[datetime] | None = None) -> list:
        """Scrape all upcoming matches for a league: match list → gRPC per match.

        `wanted_kickoffs` : ne lit que les matchs qui démarrent à moins de 3 h
        d'un de ces horaires (None = tous). Avec 40 compétitions, relire tout
        un championnat (2 à 3 demandes par match) pour un seul match à
        rafraîchir ferait des centaines de demandes inutiles par passage.
        """
        from app.ingestion.scrape_result import MatchScrapeResult
        matches = await self.fetch_competition_matches(league)
        if wanted_kickoffs is not None:
            matches = [
                mx for mx in matches
                if mx.get("kickoff_utc") is None
                or any(abs(mx["kickoff_utc"] - ko) <= _KICKOFF_WINDOW for ko in wanted_kickoffs)
            ]
        if not matches:
            return []

        results: list[MatchScrapeResult] = []

        for i, mx in enumerate(matches):
            result = await self.fetch_match_odds(
                mx["match_id"], mx["home_team"], mx["away_team"], league
            )
            if result:
                result.kickoff_utc = mx.get("kickoff_utc")
                if result.h2h or result.totals or result.btts or result.goalscorer or result.assist:
                    if not result.assist:
                        logger.warning(
                            "BetclicGrpcScraper: 0 assist odds for %s vs %s [%s]",
                            mx["home_team"], mx["away_team"], league,
                        )
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


async def scrape_betclic_leagues(
    leagues: list[str] | None = None,
    wanted_kickoffs: dict[str, list[datetime]] | None = None,
) -> list:
    """Scrape all odds from Betclic via gRPC-web for the given leagues.

    Default: all leagues in BETCLIC_COMPETITION_IDS.
    Returns MatchScrapeResult objects with goalscorer + match-level odds.
    """
    from app.ingestion.scrape_result import MatchScrapeResult

    if leagues is None:
        leagues = list(BETCLIC_COMPETITION_IDS.keys())

    async with httpx.AsyncClient(headers=_GRPC_HEADERS, follow_redirects=True) as grpc_client:
        scraper = BetclicGrpcScraper(grpc_client)

        all_results: list[MatchScrapeResult] = []
        for i, league in enumerate(leagues):
            try:
                results = await scraper.scrape_league(
                    league, wanted_kickoffs.get(league) if wanted_kickoffs is not None else None,
                )
                all_results.extend(results)
            except Exception as exc:
                logger.error(
                    "BetclicGrpcScraper: league %s failed: %s", league, exc,
                    exc_info=True,
                )
            if i < len(leagues) - 1:
                await asyncio.sleep(_MATCH_SLEEP)

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
        choices=list(BETCLIC_COMPETITION_IDS.keys()),
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
