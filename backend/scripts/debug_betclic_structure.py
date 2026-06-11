#!/usr/bin/env python3
"""Script de diagnostic — dump la structure de la page Betclic WC2026.

Usage :
    python scripts/debug_betclic_structure.py
    python scripts/debug_betclic_structure.py --screenshot
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def main(screenshot: bool) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERREUR: playwright non installé — pip install playwright && playwright install chromium")
        sys.exit(1)

    captured_requests: list[dict] = []
    captured_json: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # headless=False pour voir le rendu
        ctx = await browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        async def on_response(response):
            url = response.url
            if "begmedia.com" in url or "betclic.fr" in url:
                captured_requests.append({"url": url, "status": response.status})
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        body = await response.json()
                        captured_json.append({"url": url, "body": body})
                    except Exception:
                        pass

        page.on("response", on_response)

        url = "https://www.betclic.fr/football-sfootball/coupe-du-monde-2026-c1"
        print(f"\n[1] Navigation vers {url} …")
        await page.goto(url, wait_until="load", timeout=30_000)
        await page.wait_for_timeout(4_000)

        print(f"    URL finale : {page.url}")
        print(f"    Titre      : {await page.title()}")

        # ── Dump navigation tabs ───────────────────────────────────────────
        print("\n[2] Onglets / tabs :")
        tabs = await page.query_selector_all("[role='tab'], [class*='tab'], [class*='Tab']")
        for tab in tabs[:20]:
            txt = (await tab.inner_text()).strip()
            href = await tab.get_attribute("href") or ""
            if txt:
                print(f"    tab: '{txt}' href='{href}'")

        # ── Cherche Spéciaux ──────────────────────────────────────────────
        print("\n[3] Recherche 'Spéciaux' :")
        specials_labels = ["Spéciaux", "Speciaux", "Paris spéciaux", "Outrights", "Spécial"]
        for label in specials_labels:
            els = await page.get_by_text(label, exact=False).all()
            if els:
                print(f"    TROUVÉ '{label}' — {len(els)} élément(s)")
                for el in els[:3]:
                    tag = await el.evaluate("e => e.tagName")
                    href = await el.get_attribute("href") or ""
                    cls = await el.get_attribute("class") or ""
                    print(f"      <{tag}> class='{cls[:60]}' href='{href}'")
            else:
                print(f"    non trouvé: '{label}'")

        # ── Liens compétition ─────────────────────────────────────────────
        print("\n[4] Liens relatifs à la compétition WC2026 :")
        links = await page.query_selector_all("a[href*='coupe-du-monde-2026']")
        seen = set()
        for link in links[:30]:
            href = await link.get_attribute("href") or ""
            txt = (await link.inner_text()).strip()[:60]
            if href not in seen:
                seen.add(href)
                print(f"    href='{href}' texte='{txt}'")

        # ── Market groups ─────────────────────────────────────────────────
        print("\n[5] Groupes de marchés trouvés :")
        groups = await page.query_selector_all(
            "[class*='marketGroup'], [class*='accordionItem'], [class*='market_']"
        )
        print(f"    {len(groups)} groupes")
        for g in groups[:5]:
            cls = await g.get_attribute("class") or ""
            txt = (await g.inner_text()).strip()[:80]
            print(f"    class='{cls[:60]}' texte='{txt}'")

        # ── Click sur "Spéciaux" si trouvé ────────────────────────────────
        specials_el = None
        for label in specials_labels:
            try:
                el = page.get_by_role("tab", name=label, exact=False)
                if await el.count() > 0:
                    specials_el = el.first
                    break
                el2 = page.get_by_text(label, exact=False)
                if await el2.count() > 0:
                    specials_el = el2.first
                    break
            except Exception:
                continue

        if specials_el:
            print(f"\n[6] Click sur l'onglet Spéciaux …")
            await specials_el.click()
            await page.wait_for_timeout(3_000)

            groups_after = await page.query_selector_all(
                "[class*='marketGroup'], [class*='accordionItem'], [class*='market_']"
            )
            print(f"    {len(groups_after)} groupes après click")
            for g in groups_after[:10]:
                cls = await g.get_attribute("class") or ""
                txt = (await g.inner_text()).strip()[:80]
                print(f"    class='{cls[:60]}' texte='{txt}'")
        else:
            print("\n[6] Pas d'onglet Spéciaux cliquable.")

        # ── Requêtes réseau capturées ─────────────────────────────────────
        print(f"\n[7] Requêtes réseau ({len(captured_requests)} total) :")
        for r in captured_requests[:30]:
            print(f"    [{r['status']}] {r['url'][:120]}")

        if captured_json:
            print(f"\n[8] Réponses JSON ({len(captured_json)}) :")
            for j in captured_json[:5]:
                print(f"    {j['url'][:100]}")
                print(f"    keys: {list(j['body'].keys()) if isinstance(j['body'], dict) else type(j['body']).__name__}")

        if screenshot:
            path = Path("/tmp/betclic_wc2026.png")
            await page.screenshot(path=str(path), full_page=True)
            print(f"\n[screenshot] Sauvegardé : {path}")

        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(screenshot=args.screenshot))
