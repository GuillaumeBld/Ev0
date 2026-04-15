# backend/scripts/audit_betclic_markets.py
"""Dump ALL market names from a live Betclic gRPC response.
Run: cd /tmp/ev0-repo/backend && python scripts/audit_betclic_markets.py
"""
import asyncio
import httpx
from app.ingestion.betclic_grpc_scraper import (
    BetclicGrpcScraper, _PAGE_HEADERS, _GRPC_HEADERS,
    _stream_first_grpc_frame, encode_grpc_web_request,
    _proto_fields, GRPC_ENDPOINT,
)

async def main():
    async with (
        httpx.AsyncClient(headers=_PAGE_HEADERS, follow_redirects=True) as page_client,
        httpx.AsyncClient(headers=_GRPC_HEADERS, follow_redirects=True) as grpc_client,
    ):
        scraper = BetclicGrpcScraper(page_client)
        # Try multiple leagues to find a match
        for league in ["ligue_1", "premier_league", "bundesliga", "serie_a", "laliga"]:
            matches = await scraper.fetch_competition_matches(league)
            if matches:
                print(f"Using league: {league}, found {len(matches)} matches")
                break
        else:
            print("No matches found in any league"); return

        m = matches[0]
        print(f"Auditing: {m['home_team']} vs {m['away_team']} (match_id={m['match_id']})")
        body = encode_grpc_web_request(m["match_id"])
        raw = await _stream_first_grpc_frame(
            grpc_client, GRPC_ENDPOINT, body,
            httpx.Timeout(30.0, connect=10.0)
        )
        if not raw:
            print("Empty response"); return
        # Walk proto tree to find markets
        try:
            root  = _proto_fields(raw)
            f1    = _proto_fields(root[1][0])
            f1f1  = _proto_fields(f1[1][0])
            f11   = _proto_fields(f1f1[11][0])
            markets = f11.get(3, [])
        except (KeyError, IndexError) as e:
            print(f"Proto structure error: {e}"); return
        print(f"\nFound {len(markets)} markets:")
        for mb in markets:
            try:
                mf = _proto_fields(mb)
            except Exception:
                continue
            name_raw = mf.get(2, [b""])[0]
            name = name_raw.decode("utf-8", errors="replace") if name_raw else "?"
            state = mf.get(9, [0])[0]
            n_groups = len(mf.get(11, []))
            n_sels = sum(len(_proto_fields(g).get(2, [])) for g in mf.get(11, []))
            print(f"  [{state}] {name!r:50s}  groups={n_groups}  sels={n_sels}")

asyncio.run(main())
