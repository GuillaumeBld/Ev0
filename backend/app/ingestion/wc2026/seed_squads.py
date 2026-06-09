"""Seed WC 2026 squad players from Wikipedia.

Usage:
    python -m app.ingestion.wc2026.seed_squads [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session
from app.models.wc2026 import WC2026SquadPlayer

logger = logging.getLogger(__name__)

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_squads"

_POS_MAP: dict[str, str] = {
    "GK": "GK",
    "DF": "DEF",
    "MF": "MID",
    "FW": "FWD",
}

# Wikipedia English name → {fr: French name, flag: emoji}
_NATION_META: dict[str, dict[str, str]] = {
    "Mexico":                    {"fr": "Mexique",              "flag": "🇲🇽"},
    "South Africa":              {"fr": "Afrique du Sud",       "flag": "🇿🇦"},
    "South Korea":               {"fr": "Corée du Sud",         "flag": "🇰🇷"},
    "Czech Republic":            {"fr": "République Tchèque",   "flag": "🇨🇿"},
    "Bosnia and Herzegovina":    {"fr": "Bosnie-Herzégovine",   "flag": "🇧🇦"},
    "Canada":                    {"fr": "Canada",               "flag": "🇨🇦"},
    "Qatar":                     {"fr": "Qatar",                "flag": "🇶🇦"},
    "Switzerland":               {"fr": "Suisse",               "flag": "🇨🇭"},
    "Croatia":                   {"fr": "Croatie",              "flag": "🇭🇷"},
    "Belgium":                   {"fr": "Belgique",             "flag": "🇧🇪"},
    "Nigeria":                   {"fr": "Nigéria",              "flag": "🇳🇬"},
    "Honduras":                  {"fr": "Honduras",             "flag": "🇭🇳"},
    "Jamaica":                   {"fr": "Jamaïque",             "flag": "🇯🇲"},
    "Paraguay":                  {"fr": "Paraguay",             "flag": "🇵🇾"},
    "Uruguay":                   {"fr": "Uruguay",              "flag": "🇺🇾"},
    "Australia":                 {"fr": "Australie",            "flag": "🇦🇺"},
    "Saudi Arabia":              {"fr": "Arabie Saoudite",      "flag": "🇸🇦"},
    "Ivory Coast":               {"fr": "Côte d'Ivoire",        "flag": "🇨🇮"},
    "United States":             {"fr": "États-Unis",           "flag": "🇺🇸"},
    "Portugal":                  {"fr": "Portugal",             "flag": "🇵🇹"},
    "Argentina":                 {"fr": "Argentine",            "flag": "🇦🇷"},
    "Curaçao":                   {"fr": "Curaçao",              "flag": "🇨🇼"},
    "Ecuador":                   {"fr": "Équateur",             "flag": "🇪🇨"},
    "Egypt":                     {"fr": "Égypte",               "flag": "🇪🇬"},
    "Japan":                     {"fr": "Japon",                "flag": "🇯🇵"},
    "Senegal":                   {"fr": "Sénégal",              "flag": "🇸🇳"},
    "Spain":                     {"fr": "Espagne",              "flag": "🇪🇸"},
    "England":                   {"fr": "Angleterre",           "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "Netherlands":               {"fr": "Pays-Bas",             "flag": "🇳🇱"},
    "New Zealand":               {"fr": "Nouvelle-Zélande",     "flag": "🇳🇿"},
    "France":                    {"fr": "France",               "flag": "🇫🇷"},
    "Chile":                     {"fr": "Chili",                "flag": "🇨🇱"},
    "Germany":                   {"fr": "Allemagne",            "flag": "🇩🇪"},
    "Cameroon":                  {"fr": "Cameroun",             "flag": "🇨🇲"},
    "Colombia":                  {"fr": "Colombie",             "flag": "🇨🇴"},
    "Morocco":                   {"fr": "Maroc",                "flag": "🇲🇦"},
    "Venezuela":                 {"fr": "Venezuela",            "flag": "🇻🇪"},
    "Iran":                      {"fr": "Iran",                 "flag": "🇮🇷"},
    "Italy":                     {"fr": "Italie",               "flag": "🇮🇹"},
    "Poland":                    {"fr": "Pologne",              "flag": "🇵🇱"},
    "Indonesia":                 {"fr": "Indonésie",            "flag": "🇮🇩"},
    "Uzbekistan":                {"fr": "Ouzbékistan",          "flag": "🇺🇿"},
    "Algeria":                   {"fr": "Algérie",              "flag": "🇩🇿"},
    "Tunisia":                   {"fr": "Tunisie",              "flag": "🇹🇳"},
    "Costa Rica":                {"fr": "Costa Rica",           "flag": "🇨🇷"},
    "Panama":                    {"fr": "Panama",               "flag": "🇵🇦"},
    "Peru":                      {"fr": "Pérou",                "flag": "🇵🇪"},
    "Brazil":                    {"fr": "Brésil",               "flag": "🇧🇷"},
    "DR Congo":                  {"fr": "RD Congo",             "flag": "🇨🇩"},
    "Scotland":                  {"fr": "Écosse",               "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿"},
    "Wales":                     {"fr": "Pays de Galles",       "flag": "🏴󠁧󠁢󠁷󠁬󠁳󠁿"},
    "Haiti":                     {"fr": "Haïti",                "flag": "🇭🇹"},
    "Trinidad and Tobago":       {"fr": "Trinité-et-Tobago",   "flag": "🇹🇹"},
    "Cuba":                      {"fr": "Cuba",                 "flag": "🇨🇺"},
    "El Salvador":               {"fr": "Salvador",             "flag": "🇸🇻"},
    "Ghana":                     {"fr": "Ghana",                "flag": "🇬🇭"},
    "Kenya":                     {"fr": "Kenya",                "flag": "🇰🇪"},
    "Mali":                      {"fr": "Mali",                 "flag": "🇲🇱"},
    "Zambia":                    {"fr": "Zambie",               "flag": "🇿🇲"},
    "Cape Verde":                {"fr": "Cap-Vert",             "flag": "🇨🇻"},
    "Guinea":                    {"fr": "Guinée",               "flag": "🇬🇳"},
    "Tanzania":                  {"fr": "Tanzanie",             "flag": "🇹🇿"},
    "Rwanda":                    {"fr": "Rwanda",               "flag": "🇷🇼"},
    "Iraq":                      {"fr": "Irak",                 "flag": "🇮🇶"},
    "Jordan":                    {"fr": "Jordanie",             "flag": "🇯🇴"},
    "Bahrain":                   {"fr": "Bahreïn",              "flag": "🇧🇭"},
    "Oman":                      {"fr": "Oman",                 "flag": "🇴🇲"},
    "UAE":                       {"fr": "Émirats Arabes Unis",  "flag": "🇦🇪"},
    "Thailand":                  {"fr": "Thaïlande",            "flag": "🇹🇭"},
    "Kyrgyzstan":                {"fr": "Kirghizistan",         "flag": "🇰🇬"},
    "Guatemala":                 {"fr": "Guatemala",            "flag": "🇬🇹"},
    "Bolivia":                   {"fr": "Bolivie",              "flag": "🇧🇴"},
    "Serbia":                    {"fr": "Serbie",               "flag": "🇷🇸"},
    "Greece":                    {"fr": "Grèce",                "flag": "🇬🇷"},
    "Turkey":                    {"fr": "Turquie",              "flag": "🇹🇷"},
    "Romania":                   {"fr": "Roumanie",             "flag": "🇷🇴"},
    "Ukraine":                   {"fr": "Ukraine",              "flag": "🇺🇦"},
    "Austria":                   {"fr": "Autriche",             "flag": "🇦🇹"},
    "Hungary":                   {"fr": "Hongrie",              "flag": "🇭🇺"},
    "Slovakia":                  {"fr": "Slovaquie",            "flag": "🇸🇰"},
    "Denmark":                   {"fr": "Danemark",             "flag": "🇩🇰"},
    "Sweden":                    {"fr": "Suède",                "flag": "🇸🇪"},
    "Norway":                    {"fr": "Norvège",              "flag": "🇳🇴"},
    "Iceland":                   {"fr": "Islande",              "flag": "🇮🇸"},
    "China PR":                  {"fr": "Chine",                "flag": "🇨🇳"},
    "China":                     {"fr": "Chine",                "flag": "🇨🇳"},
}


def _parse_squads(html: str) -> list[dict[str, Any]]:
    """Parse Wikipedia WC squads HTML → list of player dicts."""
    soup = BeautifulSoup(html, "html.parser")
    players: list[dict[str, Any]] = []
    current_group: str | None = None
    current_nation: str | None = None

    for element in soup.find_all(["h2", "h3", "table"]):
        if element.name == "h2":
            # Wikipedia newer format: id on h2 directly; older: id on inner span
            h2_id = element.get("id", "")
            span = element.find("span", id=True)
            span_id = span.get("id", "") if span else ""
            matched = False
            for candidate_id in [h2_id, span_id]:
                if "Group_" in candidate_id:
                    letter = candidate_id.split("Group_")[-1][:1]
                    if letter.isalpha() and letter.isupper():
                        current_group = letter
                        matched = True
                        break
            if not matched:
                # Fallback: text content "Group A" … "Group L"
                text = element.get_text(strip=True)
                if "[" in text:
                    text = text[: text.index("[")].strip()
                if text.startswith("Group ") and len(text) == 7:
                    letter = text[-1]
                    if letter.isalpha() and letter.isupper():
                        current_group = letter
        elif element.name == "h3":
            current_nation = element.get_text(strip=True)
            # Strip edit section markers like "[edit]"
            if "[" in current_nation:
                current_nation = current_nation[:current_nation.index("[")].strip()
        elif element.name == "table" and "wikitable" in " ".join(element.get("class", [])):
            if not current_nation or not current_group:
                continue
            if current_nation not in _NATION_META:
                continue
            rows = element.find_all("tr")
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) < 4:
                    continue
                shirt_text = cells[0].get_text(strip=True)
                # Wikipedia prefixes position with sort key: "1GK", "2DF", "3MF", "4FW"
                pos_raw = cells[1].get_text(strip=True).upper().lstrip("0123456789")
                player_a = cells[2].find("a")
                player_name = (player_a.get_text(strip=True) if player_a else cells[2].get_text(strip=True))
                # Club is last column (index -1): No./Pos./Player/DOB/Caps/Goals/Club (7 cols)
                # The cell may start with a flag-icon <a> containing only an <img>;
                # skip empty-text links and use the first one with actual text.
                club_cell = cells[-1]
                club_a = next((a for a in club_cell.find_all("a") if a.get_text(strip=True)), None)
                club = (club_a.get_text(strip=True) if club_a else club_cell.get_text(strip=True)) or None

                pos = _POS_MAP.get(pos_raw, "MID")
                meta = _NATION_META.get(current_nation, {})
                nation_fr = meta.get("fr", current_nation)
                flag = meta.get("flag", "")

                players.append({
                    "nation": nation_fr,
                    "nation_en": current_nation,
                    "group_letter": current_group,
                    "player_name": player_name,
                    "club": club,
                    "position": pos,
                    "shirt_number": int(shirt_text) if shirt_text.isdigit() else None,
                    "flag_emoji": flag if flag else None,
                })

    return players


async def seed(dry_run: bool = False) -> int:
    """Fetch Wikipedia and upsert all WC 2026 squads. Returns count of rows upserted."""
    headers = {
        "User-Agent": "Mozilla/5.0 (ev0-cdm-seed/1.0; contact: admin@ev0.app)",
        "Accept-Language": "en",
    }
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        try:
            resp = await client.get(WIKIPEDIA_URL)
            resp.raise_for_status()
            html = resp.text
        except httpx.HTTPError as exc:
            logger.error("seed_squads: HTTP error fetching Wikipedia: %s", exc)
            return 0

    players = _parse_squads(html)
    if not players:
        logger.error("seed_squads: no players parsed — check Wikipedia HTML structure")
        return 0

    logger.info("seed_squads: parsed %d players from Wikipedia", len(players))

    if dry_run:
        nations = sorted({p["nation"] for p in players})
        logger.info("DRY RUN — %d players, %d nations:", len(players), len(nations))
        for n in nations:
            nation_count = sum(1 for p in players if p["nation"] == n)
            logger.info("  %s: %d", n, nation_count)
        return len(players)

    count = 0
    async with async_session() as session:
        for player in players:
            values = {k: v for k, v in player.items() if k != "nation_en"}
            stmt = (
                pg_insert(WC2026SquadPlayer)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["nation", "player_name"],
                    set_={
                        "club": values["club"],
                        "position": values["position"],
                        "shirt_number": values["shirt_number"],
                        "flag_emoji": values["flag_emoji"],
                        "group_letter": values["group_letter"],
                    },
                )
            )
            try:
                await session.execute(stmt)
                count += 1
            except Exception as exc:
                logger.warning(
                    "seed_squads: failed to upsert %s / %s: %s",
                    player.get("nation", "?"),
                    player.get("player_name", "?"),
                    exc,
                )
        await session.commit()

    logger.info("seed_squads: upserted %d players", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    count = asyncio.run(seed(dry_run=args.dry_run))
    print(f"Done: {count} players")
