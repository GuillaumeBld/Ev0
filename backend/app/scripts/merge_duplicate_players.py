"""Fusion des joueurs bzz_players dupliqués (deux univers d'api_id) — one-shot idempotent.

Origine du problème : /api/players/ renvoie tantôt {id}, tantôt {id, api_id}
(deux espaces d'identifiants) et le sync upsertait sur api_id → jusqu'à deux
lignes par joueur, et sync_player_stats itérant la table produisait des stats
dupliquées par (joueur, match).

Stratégie par ligne « univers B » (api_id != internal_id) :
- noms similaires avec la ligne canonique (api_id == internal_id) →
  repointage des stats (match + saison) vers le canonique, suppression des
  doublons exacts, suppression de la ligne B ;
- noms DISSIMILAIRES (lien internal_id menteur, ex. Rashford→Ronaldo) →
  auto-canonicalisation (internal_id := api_id), aucune fusion.

Usage (dans le conteneur worker) :
    python -m app.scripts.merge_duplicate_players [--dry-run]
"""
from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.pricing.wc2026_tournament import _names_similar

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def plan_merges(
    b_rows: list[dict],
    canonical_names: dict[int, str],
) -> tuple[list[tuple[int, int]], list[int]]:
    """Décide, pour chaque ligne B, fusion vers le canonique ou auto-canonicalisation.

    Returns (merges=[(b_api_id, canon_api_id)], self_canonical=[b_api_id]).
    Fonction pure — testée unitairement.
    """
    merges: list[tuple[int, int]] = []
    self_canonical: list[int] = []
    for row in b_rows:
        canon_name = canonical_names.get(row["internal_id"])
        if canon_name is not None and _names_similar(row["name"], canon_name):
            merges.append((row["api_id"], row["internal_id"]))
        else:
            # Canonique absent OU lien menteur → cette ligne devient canonique
            self_canonical.append(row["api_id"])
    return merges, self_canonical


async def _merge_children(
    session: AsyncSession, table: str, keys: list[str], b_api: int, canon: int
) -> tuple[int, int]:
    """Repointe les lignes enfants de b_api vers canon ; supprime les doublons.

    keys = colonnes formant l'unicité avec player_api_id (ex. event_api_id).
    """
    key_match = " AND ".join(f"c.{k} = t.{k}" for k in keys)
    deleted = (await session.execute(text(f"""
        DELETE FROM {table} t
        WHERE t.player_api_id = :b
          AND EXISTS (SELECT 1 FROM {table} c
                      WHERE c.player_api_id = :canon AND {key_match})
    """), {"b": b_api, "canon": canon})).rowcount or 0
    moved = (await session.execute(text(f"""
        UPDATE {table} SET player_api_id = :canon WHERE player_api_id = :b
    """), {"b": b_api, "canon": canon})).rowcount or 0
    return moved, deleted


async def run(dry_run: bool = False) -> dict:
    async with async_session() as session:
        b_rows = [dict(r) for r in (await session.execute(text("""
            SELECT api_id, internal_id, name FROM bzz_players
            WHERE internal_id IS NOT NULL AND api_id <> internal_id
        """))).mappings().all()]
        canonical_names = {
            r.api_id: r.name
            for r in (await session.execute(text(
                "SELECT api_id, name FROM bzz_players WHERE api_id = internal_id"
            ))).all()
        }

        merges, self_canonical = plan_merges(b_rows, canonical_names)
        logger.info(
            "plan: %d fusions, %d auto-canonicalisations (liens menteurs/canonique absent)",
            len(merges), len(self_canonical),
        )
        if dry_run:
            return {"merges": len(merges), "self_canonical": len(self_canonical), "dry_run": True}

        stats = {"merged": 0, "moved_match": 0, "deleted_match": 0,
                 "moved_season": 0, "deleted_season": 0}
        for i, (b_api, canon) in enumerate(merges):
            m, d = await _merge_children(
                session, "bzz_player_match_stats", ["event_api_id"], b_api, canon)
            stats["moved_match"] += m
            stats["deleted_match"] += d
            m, d = await _merge_children(
                session, "bzz_player_season_stats", ["league_api_id", "season"], b_api, canon)
            stats["moved_season"] += m
            stats["deleted_season"] += d
            await session.execute(
                text("DELETE FROM bzz_players WHERE api_id = :b"), {"b": b_api})
            stats["merged"] += 1
            if (i + 1) % 2000 == 0:
                await session.commit()
                logger.info("… %d/%d fusions", i + 1, len(merges))
        await session.commit()

        if self_canonical:
            await session.execute(text(
                "UPDATE bzz_players SET internal_id = api_id WHERE api_id = ANY(:ids)"
            ), {"ids": self_canonical})
            await session.commit()

        # Validation finale : plus de doublons (identité, match)
        residual = (await session.execute(text("""
            SELECT count(*) FROM bzz_players
            WHERE internal_id IS NOT NULL AND api_id <> internal_id
        """))).scalar_one()
        dup_stats = (await session.execute(text("""
            SELECT count(*) FROM (
              SELECT p.name, s.event_api_id
              FROM bzz_player_match_stats s
              JOIN bzz_players p ON p.api_id = s.player_api_id
              GROUP BY p.name, s.event_api_id HAVING count(*) > 1
            ) x
        """))).scalar_one()
        stats["residual_b_rows"] = int(residual)
        stats["residual_dup_stats"] = int(dup_stats)
        logger.info("terminé: %s", stats)
        return stats


if __name__ == "__main__":
    asyncio.run(run(dry_run="--dry-run" in sys.argv))
