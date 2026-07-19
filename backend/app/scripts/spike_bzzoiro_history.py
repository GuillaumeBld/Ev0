"""Spike read-only : l'API Bzzoiro expose-t-elle l'historique ? (spec 2026-07-18, §3.5)

Sonde 3 questions, ~15 requêtes max, aucun write en DB :
  A. /api/leagues/ — quelles ligues au-delà des 6 cibles (Eredivisie, Liga
     Portugal, Championship…) et quels season_id/saisons y sont listés ?
  B. /api/events/ avec une fenêtre 2024-25 (date_from=2024-08-01,
     date_to=2025-06-30) sur la Premier League — les matchs historiques
     sont-ils servis ?
  C. /api/player-stats/ pour un joueur connu — les lignes couvrent-elles les
     matchs d'avant août 2025 ?

Si la réponse A révèle un paramètre season/season_id, le script relance B et C
avec ce paramètre pour la saison 2024-25 (D et E ci-dessous) — c'est le cœur
de la décision entre les 3 options de la spec §3.5.

Usage : cd backend && python -m app.scripts.spike_bzzoiro_history
"""

import asyncio
import json
import re

from app.config import settings
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_IDS

SEASON_KEY_PATTERN = re.compile(r"season", re.IGNORECASE)


def _dump(label: str, payload: object, limit: int = 4000) -> None:
    print(f"=== {label} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:limit])


def _find_season_hints(payload: object) -> list[tuple[str, object]]:
    """Recherche superficielle de clés contenant 'season' dans la réponse A."""
    hints: list[tuple[str, object]] = []

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if SEASON_KEY_PATTERN.search(key):
                    hints.append((key, value))
                if isinstance(value, (dict, list)):
                    _walk(value)
        elif isinstance(node, list):
            for item in node[:5]:  # échantillon, pas toute la liste
                _walk(item)

    _walk(payload)
    return hints


async def main() -> None:
    assert settings.bzzoiro_api_key, "BZZOIRO_API_KEY manquante"
    async with BzzoiroClient(settings.bzzoiro_api_key) as client:
        # A. Ligues disponibles (périmètre + saisons listées)
        leagues = await client.get_page("/api/leagues/")
        _dump("A. LEAGUES", leagues)

        season_hints = _find_season_hints(leagues)
        print("=== A-bis. INDICES 'season' TROUVÉS DANS LA RÉPONSE A ===")
        print(json.dumps(season_hints[:20], indent=2, ensure_ascii=False, default=str))

        # B. Matchs historiques 2024-25 (Premier League, internal_id=1)
        pl = TARGET_LEAGUE_INTERNAL_IDS["premier_league"]
        events = await client.get_page(
            "/api/events/",
            {"league": pl, "date_from": "2024-08-01", "date_to": "2024-09-01"},
        )
        _dump("B. EVENTS 2024-25 (PL, août 2024)", events)

        # C. Stats par joueur — profondeur temporelle
        # Note: endpoint ne filtre que par player=<internal_id>, pas par league (découverte du spike)
        stats = await client.get_page("/api/player-stats/", {"player": 1792})
        _dump("C. PLAYER-STATS (échantillon, regarder les dates)", stats)

        # D/E. Si un paramètre season/season_id est apparu en A, on relance
        # B et C avec ce paramètre pour la saison 2024-25.
        if season_hints:
            print(
                "=== D/E. RELANCE B ET C AVEC season=2024-2025 "
                "(indices season détectés en A) ==="
            )
            events_season = await client.get_page(
                "/api/events/",
                {
                    "league": pl,
                    "season": "2024-2025",
                    "date_from": "2024-08-01",
                    "date_to": "2024-09-01",
                },
            )
            _dump("D. EVENTS avec season=2024-2025 (PL, août 2024)", events_season)

            # Note: endpoint ne filtre que par player=<internal_id> (+ season=<id
            # numérique>), pas par league/season texte — cf. preuve bloc C
            # (player=1792, season=336).
            stats_season = await client.get_page(
                "/api/player-stats/",
                {"player": 1792, "season": 336},
            )
            _dump("E. PLAYER-STATS avec player=1792, season=336", stats_season)
        else:
            print(
                "=== D/E. Aucun indice 'season'/'season_id' trouvé en A "
                "— pas de relance ==="
            )


if __name__ == "__main__":
    asyncio.run(main())
