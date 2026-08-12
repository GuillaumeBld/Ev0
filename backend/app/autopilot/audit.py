"""Autopilot data-integrity audit — READ-ONLY.

Materialises the manual audit of July 2026 (issues #4, #5, #6) as reproducible
code. NOTHING here modifies decisions, weights or settlement — it only reads
and aggregates, so it can be run safely at any time to produce a verifiable
state of the Autopilot data.

Two layers:
- pure helpers (no DB) — unit-tested, deterministic;
- async audit functions (read-only queries) — aggregated by ``run_audit``.

Cutoff: the settlement bugs were fixed on 2026-07-10 (couverture events,
autopilot settle en panne, double settlement). Decisions settled strictly
before this date carry potentially-corrupted rewards and must be treated
separately from clean ones.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Date à partir de laquelle un settlement est considéré fiable (correctifs du 10/07).
SETTLEMENT_FIX_CUTOFF = datetime(2026, 7, 10, tzinfo=UTC)

# Dimension attendue des features (passage 10→13 le 11/03, commit ce504df).
EXPECTED_FEATURE_DIM = 13

# Résultats porteurs de signal d'apprentissage (void/skip = reward neutre).
LEARNABLE_RESULTS = ("won", "lost")


# ── Pure helpers (testables sans DB) ──────────────────────────────────────────

def feature_dim(features_json: str | None) -> int:
    """Nombre de features dans un vecteur JSON stocké. 0 si illisible/vide."""
    if not features_json:
        return 0
    try:
        arr = json.loads(features_json)
    except (json.JSONDecodeError, TypeError):
        return 0
    return len(arr) if isinstance(arr, list) else 0


def is_feature_dim_valid(features_json: str | None) -> bool:
    """True si le vecteur a la dimension attendue (utilisable par l'agent)."""
    return feature_dim(features_json) == EXPECTED_FEATURE_DIM


def edge_bucket(edge: float | None) -> str:
    """Bucket d'edge pour l'analyse de ROI par tranche."""
    if edge is None:
        return "unknown"
    if edge < 0.0:
        return "negatif"
    if edge < 0.05:
        return "0-5%"
    if edge < 0.10:
        return "5-10%"
    if edge < 0.15:
        return "10-15%"
    return "15%+"


def is_reward_verifiable(
    result: str | None,
    settled_at: datetime | None,
    features_json: str | None,
    cutoff: datetime = SETTLEMENT_FIX_CUTOFF,
) -> bool:
    """True si le reward de cette décision est fiable pour l'entraînement.

    Exige : résultat porteur de signal (won/lost), réglé APRÈS les correctifs,
    et vecteur de features à la bonne dimension.
    """
    if result not in LEARNABLE_RESULTS:
        return False
    if settled_at is None:
        return False
    ts = settled_at if settled_at.tzinfo else settled_at.replace(tzinfo=UTC)
    if ts < cutoff:
        return False
    return is_feature_dim_valid(features_json)


# ── Async audits (read-only) ──────────────────────────────────────────────────

async def feature_dim_distribution(db: AsyncSession) -> dict[str, int]:
    """Répartition des décisions par dimension de features (issue #6)."""
    rows = (await db.execute(text("""
        SELECT (length(features_json) - length(replace(features_json, ',', '')) + 1) AS dim,
               count(*) AS n
        FROM autopilot_decisions
        WHERE features_json IS NOT NULL AND features_json <> ''
        GROUP BY 1 ORDER BY 1
    """))).all()
    return {f"{int(dim)}-dim": int(n) for dim, n in rows}


async def void_breakdown(db: AsyncSession) -> dict[str, Any]:
    """Voids : total et part sur le total réglé (issue #5)."""
    row = (await db.execute(text("""
        SELECT
          count(*) FILTER (WHERE result = 'void')                     AS voids,
          count(*) FILTER (WHERE result IS NOT NULL)                  AS settled,
          count(*) FILTER (WHERE result = 'void'
                           AND settled_at >= :cutoff)                 AS voids_post_fix
        FROM autopilot_decisions
    """), {"cutoff": SETTLEMENT_FIX_CUTOFF})).mappings().one()
    settled = int(row["settled"]) or 0
    voids = int(row["voids"]) or 0
    return {
        "voids": voids,
        "settled": settled,
        "void_rate": round(voids / settled, 4) if settled else 0.0,
        "voids_post_fix": int(row["voids_post_fix"]),
    }


async def settlement_integrity(db: AsyncSession) -> dict[str, Any]:
    """Intégrité du settlement (issue #4) : décisions inaccessibles / bloquées.

    - unreachable : result NULL mais impossible à atteindre par le settle
      (recommendation_id NULL ou reco/fixture orpheline).
    - stuck_finished : result NULL, reco+fixture présentes, match fini.
    - future_pending : result NULL, match pas encore joué (légitime).
    """
    row = (await db.execute(text("""
        SELECT
          count(*) FILTER (WHERE d.result IS NULL)                          AS null_result,
          count(*) FILTER (WHERE d.result IS NULL
                           AND d.recommendation_id IS NULL)                 AS unreachable_no_rec,
          count(*) FILTER (WHERE d.result IS NULL
                           AND d.recommendation_id IS NOT NULL
                           AND r.id IS NULL)                                AS unreachable_orphan,
          count(*) FILTER (WHERE d.result IS NULL AND f.status = 'finished') AS stuck_finished,
          count(*) FILTER (WHERE d.result IS NULL
                           AND f.status IS NOT NULL
                           AND f.status <> 'finished')                      AS future_pending
        FROM autopilot_decisions d
        LEFT JOIN recommendations r ON r.id = d.recommendation_id
        LEFT JOIN fixtures f ON f.id = r.fixture_id
    """))).mappings().one()
    return {k: int(v) for k, v in row.items()}


async def reward_distribution(db: AsyncSession) -> dict[str, Any]:
    """Distribution des résultats + PnL/mise, et part de reward vérifiable."""
    rows = (await db.execute(text("""
        SELECT COALESCE(result, 'unsettled') AS result, count(*) AS n,
               round(COALESCE(sum(pnl), 0)::numeric, 2) AS pnl,
               round(COALESCE(sum(stake), 0)::numeric, 2) AS staked
        FROM autopilot_decisions GROUP BY 1 ORDER BY 2 DESC
    """))).mappings().all()
    verifiable = (await db.execute(text("""
        SELECT count(*) FROM autopilot_decisions
        WHERE result IN ('won', 'lost')
          AND settled_at >= :cutoff
          AND (length(features_json) - length(replace(features_json, ',', '')) + 1) = :dim
    """), {"cutoff": SETTLEMENT_FIX_CUTOFF, "dim": EXPECTED_FEATURE_DIM})).scalar_one()
    return {
        "by_result": [dict(r) for r in rows],
        "reward_verifiable_count": int(verifiable),
    }


async def action_distribution(db: AsyncSession) -> dict[str, int]:
    """Répartition des actions (skip vs mises)."""
    rows = (await db.execute(text("""
        SELECT action_idx, count(*) FROM autopilot_decisions GROUP BY 1 ORDER BY 1
    """))).all()
    labels = {0: "skip", 1: "quarter_kelly", 2: "half_kelly", 3: "kelly"}
    return {labels.get(int(a), str(a)): int(n) for a, n in rows}


async def run_audit(db: AsyncSession) -> dict[str, Any]:
    """Agrège tous les audits read-only en un rapport unique (futur dashboard)."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "settlement_fix_cutoff": SETTLEMENT_FIX_CUTOFF.isoformat(),
        "feature_dim_distribution": await feature_dim_distribution(db),
        "void_breakdown": await void_breakdown(db),
        "settlement_integrity": await settlement_integrity(db),
        "reward_distribution": await reward_distribution(db),
        "action_distribution": await action_distribution(db),
    }
