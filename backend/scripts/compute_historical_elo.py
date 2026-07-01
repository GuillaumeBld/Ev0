"""Compute historical ELO ratings for WC2026 nations from international match results.

Data source: https://github.com/martj42/international_results (martj42/international_results)
Covers all international matches since 1872. We use 2018-01-01 to 2026-06-10 (pre-WC2026).

Usage:
    python backend/scripts/compute_historical_elo.py

Outputs the resulting ELO dict to stdout and optionally writes historical_elo.py.
"""
from __future__ import annotations

import csv
import io
import math
import sys
import urllib.request
from datetime import date

# ── Constants ─────────────────────────────────────────────────────────────────

BASE_ELO = 1500.0
START_DATE = date(2018, 1, 1)      # Post-2018 WC to focus on current generation
END_DATE   = date(2026, 6, 10)     # Just before WC2026 group stage opens

# K factor by tournament type
def _k(tournament: str) -> float:
    t = tournament.lower()
    if "world cup" in t and "qualif" not in t:
        return 60.0
    if any(x in t for x in ("euros", "euro ", "copa am", "african cup of nations", "afcon", "afc asian cup", "gold cup", "copa america")):
        if "qualif" not in t:
            return 50.0
    if any(x in t for x in ("qualif", "nations league", "concacaf nations")):
        return 40.0
    if "friendly" in t or "fifa series" in t:
        return 15.0
    return 30.0

# ── Name mapping: CSV name → TEAM_BM canonical name ──────────────────────────

CSV_TO_CANON: dict[str, str] = {
    "United States":          "United States",
    "Ivory Coast":            "Ivory Coast",
    "Bosnia and Herzegovina": "Bosnia-Herzegovina",
    "Cape Verde":             "Cape Verde Islands",
    "Czech Republic":         "Czechia",
    "South Korea":            "South Korea",
    "DR Congo":               "Congo DR",
    # These match directly
    "Spain":           "Spain",
    "Brazil":          "Brazil",
    "Germany":         "Germany",
    "England":         "England",
    "France":          "France",
    "Argentina":       "Argentina",
    "Portugal":        "Portugal",
    "Belgium":         "Belgium",
    "Switzerland":     "Switzerland",
    "Netherlands":     "Netherlands",
    "Colombia":        "Colombia",
    "Norway":          "Norway",
    "Mexico":          "Mexico",
    "Ecuador":         "Ecuador",
    "Uruguay":         "Uruguay",
    "Canada":          "Canada",
    "Croatia":         "Croatia",
    "Morocco":         "Morocco",
    "Austria":         "Austria",
    "Turkey":          "Turkey",
    "Japan":           "Japan",
    "Senegal":         "Senegal",
    "Egypt":           "Egypt",
    "Scotland":        "Scotland",
    "Sweden":          "Sweden",
    "Algeria":         "Algeria",
    "Paraguay":        "Paraguay",
    "Iran":            "Iran",
    "Ghana":           "Ghana",
    "Australia":       "Australia",
    "Panama":          "Panama",
    "New Zealand":     "New Zealand",
    "South Africa":    "South Africa",
    "Uzbekistan":      "Uzbekistan",
    "Tunisia":         "Tunisia",
    "Saudi Arabia":    "Saudi Arabia",
    "Curaçao":         "Curaçao",
    "Haiti":           "Haiti",
    "Jordan":          "Jordan",
    "Qatar":           "Qatar",
    "Iraq":            "Iraq",
}

WC2026_NATIONS = set(CSV_TO_CANON.values())

# ── ELO Engine ────────────────────────────────────────────────────────────────

def elo_expected(elo_a: float, elo_b: float, D: float = 400.0) -> float:
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / D))


def update_elo(elo: dict[str, float], ta: str, tb: str, sa: int, sb: int, k: float) -> None:
    ea = elo_expected(elo[ta], elo[tb])
    ra = 1.0 if sa > sb else (0.5 if sa == sb else 0.0)
    delta = k * (ra - ea)
    elo[ta] += delta
    elo[tb] -= delta


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> dict[str, float]:
    url = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
    print(f"Fetching {url} …", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=30) as resp:
        content = resp.read().decode("utf-8")

    elo: dict[str, float] = {n: BASE_ELO for n in WC2026_NATIONS}

    reader = csv.DictReader(io.StringIO(content))
    processed = 0
    for row in reader:
        try:
            d = date.fromisoformat(row["date"])
        except ValueError:
            continue
        if d < START_DATE or d > END_DATE:
            continue

        home_csv = row["home_team"].strip()
        away_csv = row["away_team"].strip()
        home = CSV_TO_CANON.get(home_csv)
        away = CSV_TO_CANON.get(away_csv)

        # Skip if neither team is a WC2026 nation
        if home not in WC2026_NATIONS and away not in WC2026_NATIONS:
            continue

        try:
            sh, sa = int(row["home_score"]), int(row["away_score"])
        except (ValueError, KeyError):
            continue

        # Initialise unknown teams at base ELO (non-WC opponents)
        if home not in elo:
            elo[home] = BASE_ELO
        if away not in elo:
            elo[away] = BASE_ELO

        k = _k(row["tournament"])
        update_elo(elo, home, away, sh, sa, k)
        processed += 1

    print(f"Processed {processed} matches.", file=sys.stderr)

    # Only return WC2026 nations
    result = {n: round(v, 1) for n, v in elo.items() if n in WC2026_NATIONS}

    # Sort descending by ELO
    result = dict(sorted(result.items(), key=lambda x: -x[1]))

    print("\nHistorical ELO (WC2026 nations):", file=sys.stderr)
    for nation, e in result.items():
        print(f"  {nation:<28} {e:.1f}", file=sys.stderr)

    return result


if __name__ == "__main__":
    result = main()

    # Write historical_elo.py
    out_path = "backend/app/ingestion/wc2026/historical_elo.py"
    with open(out_path, "w") as f:
        f.write('"""Pre-tournament ELO ratings computed from historical international results (2018–2026-06-10).\n')
        f.write("\nSource: https://github.com/martj42/international_results\n")
        f.write("Generated by scripts/compute_historical_elo.py — do not edit manually.\n")
        f.write('"""\n\n')
        f.write("HISTORICAL_ELO: dict[str, float] = {\n")
        for nation, e in result.items():
            f.write(f'    "{nation}":{" " * (28 - len(nation))}{e},\n')
        f.write("}\n")

    print(f"\nWrote {out_path}", file=sys.stderr)
