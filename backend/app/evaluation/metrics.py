"""Métriques d'évaluation des modèles de pricing (spec 2026-07-18, §3.2).

Fonctions pures — pas d'accès DB. Le delta apparié est la métrique reine du
duel Alpha/Beta : le bruit commun aux deux modèles s'annule ticket par ticket.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-12


def _check(probs: list[float], outcomes: list[bool]) -> None:
    if not probs or len(probs) != len(outcomes):
        raise ValueError(
            f"Listes vides ou incohérentes: {len(probs)} probs vs {len(outcomes)} outcomes"
        )


def _ticket_loss(p: float, won: bool) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return -math.log(p) if won else -math.log(1.0 - p)


def log_loss(probs: list[float], outcomes: list[bool]) -> float:
    _check(probs, outcomes)
    return sum(_ticket_loss(p, o) for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def brier_score(probs: list[float], outcomes: list[bool]) -> float:
    _check(probs, outcomes)
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


@dataclass
class CalibrationBin:
    low: float
    high: float
    count: int
    avg_prob: float
    hit_rate: float


def calibration_bins(
    probs: list[float], outcomes: list[bool], n_bins: int = 10
) -> list[CalibrationBin]:
    _check(probs, outcomes)
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes, strict=True):
        idx = min(int(p * n_bins), n_bins - 1)  # p=1.0 → dernier bin
        buckets[idx].append((p, o))
    bins: list[CalibrationBin] = []
    for i, bucket in enumerate(buckets):
        count = len(bucket)
        bins.append(
            CalibrationBin(
                low=i / n_bins,
                high=(i + 1) / n_bins,
                count=count,
                avg_prob=sum(p for p, _ in bucket) / count if count else 0.0,
                hit_rate=sum(1 for _, o in bucket if o) / count if count else 0.0,
            )
        )
    return bins


@dataclass
class PairedDelta:
    deltas: list[float]  # perte_A − perte_B par ticket ; > 0 ⟹ B meilleur
    mean_delta: float
    n: int


def paired_delta_log_loss(
    probs_a: list[float], probs_b: list[float], outcomes: list[bool]
) -> PairedDelta:
    _check(probs_a, outcomes)
    _check(probs_b, outcomes)
    deltas = [
        _ticket_loss(pa, o) - _ticket_loss(pb, o)
        for pa, pb, o in zip(probs_a, probs_b, outcomes, strict=True)
    ]
    return PairedDelta(deltas=deltas, mean_delta=sum(deltas) / len(deltas), n=len(deltas))
