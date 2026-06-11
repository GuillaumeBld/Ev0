#!/usr/bin/env python3
"""Diagnostic ciblé — cherche les cotes outrights sur Betclic WC2026.

Stratégies testées :
  1. Capture des requêtes offering.begmedia.com (gRPC-Web)
  2. Extraction des cartes topPlayerCard (widget Top des Buteurs)
  3. Navigation vers /speciaux / /paris-speciaux depuis la page compétition

Usage : python scripts/debug_betclic_outrights.py
"""
from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERREUR: pip install playwright && playwright install chromium")
        sys.exit(1)

    offering_calls: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        async def on_request(request):
            if "offering.begmedia.com" in request.url:
                offering_calls.append({
                    "type": "req",
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers),
                })

        async def on_response(response):
            if "offering.begmedia.com" in response.url:
                try:
                    body = await response.body()
                    offering_calls.append({
                        "type": "resp",
                        "url": response.url,
                        "status": response.status,
                        "ct": response.headers.get("content-type", ""),
                        "body_hex": body[:200].hex(),
                        "body_len": len(body),
                    })
                except Exception as exc:
                    offering_calls.append({"type": "resp_err", "url": response.url, "err": str(exc)})

        page.on("request", on_request)
        page.on("response", on_response)

        # ── 1. Page compétition ────────────────────────────────────────
        print("\n[1] Page compétition WC2026…")
        await page.goto(
            "https://www.betclic.fr/football-sfootball/coupe-du-monde-2026-c1",
            wait_until="load", timeout=30_000,
        )
        await page.wait_for_timeout(5_000)

        print(f"    URL finale : {page.url}")
        print(f"    Requêtes offering.begmedia.com : {len(offering_calls)}")
        for c in offering_calls:
            if c["type"] == "resp":
                method_name = c["url"].split("/")[-1][:50]
                print(f"    [{c['status']}] {method_name} — {c['body_len']} bytes")
                # Essaie de trouver des strings lisibles dans le binaire
                try:
                    raw = bytes.fromhex(c["body_hex"])
                    # protobuf: cherche des strings (length-delimited, tag type 2)
                    printable = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw[:200])
                    print(f"         raw(200): {printable}")
                except Exception:
                    pass

        # ── 2. Cartes topPlayerCard ────────────────────────────────────
        print("\n[2] Cartes topPlayerCard…")
        cards = await page.query_selector_all("[class*='topPlayerCard']")
        print(f"    {len(cards)} cartes trouvées")
        for card in cards[:10]:
            txt = (await card.inner_text()).strip().replace("\n", " | ")
            html = await card.inner_html()
            # Cherche des boutons de cotes
            odds_btn = await card.query_selector("button, [class*='odds'], [class*='cote'], [class*='price']")
            odds_txt = ""
            if odds_btn:
                odds_txt = (await odds_btn.inner_text()).strip()
            print(f"    card: '{txt[:80]}' | odds_btn='{odds_txt}'")

        # ── 3. Liens vers sections spéciales ──────────────────────────
        print("\n[3] Liens vers sections spéciales…")
        special_patterns = ["speciaux", "special", "outright", "paris-sp", "buteur", "passeur", "vainqueur"]
        all_links = await page.query_selector_all("a[href]")
        for link in all_links:
            href = await link.get_attribute("href") or ""
            if any(p in href.lower() for p in special_patterns):
                txt = (await link.inner_text()).strip()[:60]
                print(f"    href='{href}' texte='{txt}'")

        # ── 4. Essaie de naviguer vers /speciaux depuis la compétition ─
        print("\n[4] Navigation tentée vers variantes 'spéciaux'…")
        specials_urls = [
            "https://www.betclic.fr/football-sfootball/coupe-du-monde-2026-c1/paris-speciaux",
            "https://www.betclic.fr/football-sfootball/coupe-du-monde-2026-c1/paris-speciaux-group",
            "https://www.betclic.fr/paris-sportifs/football/coupe-du-monde-2026/outrights",
        ]
        for url in specials_urls:
            offering_calls.clear()
            await page.goto(url, wait_until="load", timeout=15_000)
            await page.wait_for_timeout(2_000)
            final = page.url
            title = await page.title()
            print(f"    {url.split('/')[-1][:40]} → {final[-60:]} | {title[:50]}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
