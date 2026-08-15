"""Cadence de la sync effectifs Transfermarkt : quotidien en mercato, hebdo
sinon (Tache 7).

Deux "fenetres mercato" (ete + hiver) sur lesquelles le marche des transferts
est actif et les effectifs bougent vite -> `sync_squads` doit tourner tous
les jours. Hors de ces fenetres, les effectifs sont stables (rares
changements en cours de saison, prets/blessures a part, deja geres par
`sync_loan_teams`) -> un passage hebdomadaire suffit et evite de spammer
Transfermarkt pour rien.

`should_run_today` est une fonction PURE (aucun acces DB/reseau) : le
`last_weekly` (date du dernier run hebdo REUSSI, `status in {"ok","partial"}`)
est charge par l'appelant (`worker.job_sync_squads`) puis passe ici pour
decider du mode du jour.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, NamedTuple


class MercatoWindow(NamedTuple):
    """Fenetre mercato, bornes incluses, exprimee en (mois, jour) — ignore
    l'annee (aucune des deux fenetres ne chevauche le 31 decembre / 1er
    janvier au sens d'un intervalle continu, donc une comparaison
    (mois, jour) suffit, pas besoin de gerer un `date` complet ni un
    wrap-around d'annee)."""

    start_month: int
    start_day: int
    end_month: int
    end_day: int

    def contains(self, today: date) -> bool:
        start = (self.start_month, self.start_day)
        end = (self.end_month, self.end_day)
        return start <= (today.month, today.day) <= end


# Mercato ete : 10 juin -> 2 septembre (bornes incluses).
# Mercato hiver : 1er janvier -> 31 janvier (bornes incluses).
MERCATO_WINDOWS: tuple[MercatoWindow, ...] = (
    MercatoWindow(6, 10, 9, 2),
    MercatoWindow(1, 1, 1, 31),
)

# Cadence hebdomadaire hors mercato, en jours.
WEEKLY_INTERVAL_DAYS = 7


def _in_mercato_window(today: date) -> bool:
    return any(window.contains(today) for window in MERCATO_WINDOWS)


def should_run_today(today: date, last_weekly: date | None) -> Literal["daily", "weekly"] | None:
    """Determine le mode de sync du jour, ou `None` si rien a faire aujourd'hui.

    - En fenetre mercato (ete ou hiver) : `"daily"`, systematiquement.
    - Hors mercato : `"weekly"` si aucun run hebdo reussi n'a encore eu lieu
      (`last_weekly is None`) ou si le dernier date d'au moins
      `WEEKLY_INTERVAL_DAYS` jours ; sinon `None` (deja fait cette semaine).
    """
    if _in_mercato_window(today):
        return "daily"

    if last_weekly is None:
        return "weekly"

    if (today - last_weekly).days >= WEEKLY_INTERVAL_DAYS:
        return "weekly"

    return None
