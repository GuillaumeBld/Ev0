"""Resout les compos brutes de bzz_events en places nommees et identifiees.

Pourquoi ce module existe : sur 1 468 matchs -- du 15/08/2025 au 13/04/2026,
soit les deux premiers tiers de la saison en cours -- Bzzoiro renvoie
`"id": null` pour chaque joueur de la compo et ne laisse qu'un nom abrege
("Alisson", "A. Mac Allister"). Le rapprochement par nom complet n'aboutit
alors que dans 2,4 % des cas, car bzz_players stocke le nom entier.

La sortie : bzz_lineup_slots, une place par joueur, avec l'identifiant quand
on a su le retrouver. L'archive brute n'est jamais reecrite.

METHODE. On ne cherche jamais dans les 120 000 joueurs de la base, mais dans
le seul vivier des joueurs ayant une statistique sur CE match -- une
quarantaine. Un nom abrege y devient quasi unique, ce qui rend le
rapprochement sur nom court a la fois efficace et sur : mesure du 09/09/2026,
31 599 places resolues sur 32 298, soit 97,8 %.

CE QU'ON NE FAIT PAS. Une place qu'on n'a pas su resoudre n'est pas jetee :
elle est ecrite avec player_api_id a NULL et resolution = "introuvable" ou
"ambigu". Un trou doit rester visible et denombrable. Les 660 restants sont
des formes a initiales multiples ("R. K. Muani", "I. V. d. Brempt") qu'aucun
rapprochement exact ne peut trancher sans risque de confusion.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.auto_settle import _normalize_name
from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_IDS

logger = logging.getLogger(__name__)

LEAGUES = [v for k, v in TARGET_LEAGUE_INTERNAL_IDS.items() if k != "champions_league"]

# Vivier d'un match : tout joueur y ayant une statistique, avec ses deux
# formes de nom. C'est l'ensemble dans lequel un nom abrege doit etre retrouve.
_VIVIER = text("""
    SELECT s.player_api_id AS pid, p.name AS nom, p.short_name AS court
    FROM bzz_player_match_stats s
    JOIN bzz_players p ON p.api_id = s.player_api_id
    WHERE s.event_api_id = :ev
""")

# Matchs dont la compo est exploitable et pas encore resolue.
_A_RESOUDRE = text("""
    SELECT e.api_id, e.lineups
    FROM bzz_events e
    WHERE e.league_api_id IN :ligues
      AND e.status = 'finished'
      AND jsonb_typeof(e.lineups) = 'object'
      AND e.lineups <> '{}'::jsonb
      AND NOT EXISTS (
          SELECT 1 FROM bzz_lineup_slots l WHERE l.event_api_id = e.api_id
      )
    ORDER BY e.event_date DESC
    LIMIT :limite
""").bindparams(bindparam("ligues", expanding=True))


def _index(vivier: list[dict[str, Any]]) -> dict[str, dict[str, list[int]]]:
    """Deux tables de correspondance nom normalise -> identifiants.

    Une liste et non un identifiant unique : deux joueurs du meme match
    peuvent porter le meme nom abrege, et ce cas doit rester detectable
    plutot que d'etre tranche au hasard.
    """
    par_court: dict[str, list[int]] = {}
    par_nom: dict[str, list[int]] = {}
    for r in vivier:
        if r["court"]:
            par_court.setdefault(_normalize_name(r["court"]), []).append(r["pid"])
        if r["nom"]:
            par_nom.setdefault(_normalize_name(r["nom"]), []).append(r["pid"])
    return {"short_name": par_court, "name": par_nom}


def resoudre_place(
    joueur: dict[str, Any], index: dict[str, dict[str, list[int]]]
) -> tuple[int | None, str]:
    """(identifiant, chemin de resolution) pour une place de compo.

    L'identifiant fourni par l'API prime : aucun rapprochement quand il existe.
    """
    brut = joueur.get("id")
    if brut is not None:
        return int(brut), "id"

    nom = joueur.get("name") or ""
    if not nom:
        return None, "introuvable"

    cle = _normalize_name(nom)
    ambigu = False
    for chemin in ("short_name", "name"):
        candidats = index[chemin].get(cle) or []
        if len(candidats) == 1:
            return candidats[0], chemin
        if len(candidats) > 1:
            ambigu = True
    return None, "ambigu" if ambigu else "introuvable"


def places_du_match(
    lineups: dict[str, Any], index: dict[str, dict[str, list[int]]]
) -> list[dict[str, Any]]:
    """Toutes les places d'un match, titulaires puis remplacants, deux camps.

    Accepte les deux formes archivees : la reponse v2 entiere ou le seul bloc
    par camp.
    """
    blocs = lineups.get("lineups") or lineups
    places: list[dict[str, Any]] = []
    for cote, dom in (("home", True), ("away", False)):
        bloc = blocs.get(cote) or {}
        for section, titulaire in (("players", True), ("substitutes", False)):
            for j in bloc.get(section) or []:
                nom = j.get("name") or ""
                if not nom:
                    continue
                pid, chemin = resoudre_place(j, index)
                places.append({
                    "is_home": dom,
                    "is_starter": titulaire,
                    "player_name": nom,
                    "player_api_id": pid,
                    "resolution": chemin,
                    "position": (j.get("position") or None),
                    "jersey_number": _entier(j.get("jersey_number")),
                })
    return places


def _entier(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def resoudre_compos(session: AsyncSession, limite: int = 500) -> dict[str, int]:
    """Resout les compos non encore traitees. Rend le decompte par chemin."""
    from app.models.bzz_lineup_slots import BzzLineupSlot

    evenements = (await session.execute(
        _A_RESOUDRE, {"ligues": LEAGUES, "limite": limite}
    )).mappings().all()

    totaux: dict[str, int] = {}
    matchs = 0
    for ev in evenements:
        vivier = (await session.execute(
            _VIVIER, {"ev": ev["api_id"]}
        )).mappings().all()
        places = places_du_match(ev["lineups"], _index(list(vivier)))
        if not places:
            continue

        for p in places:
            totaux[p["resolution"]] = totaux.get(p["resolution"], 0) + 1

        stmt = pg_insert(BzzLineupSlot).values(
            [{"event_api_id": ev["api_id"], **p} for p in places]
        )
        await session.execute(stmt.on_conflict_do_update(
            constraint="uq_bzz_lineup_slot",
            set_={
                "is_starter": stmt.excluded.is_starter,
                "player_api_id": stmt.excluded.player_api_id,
                "resolution": stmt.excluded.resolution,
                "position": stmt.excluded.position,
                "jersey_number": stmt.excluded.jersey_number,
            },
        ))
        matchs += 1

    if matchs:
        await session.commit()

    logger.info(
        "Compos resolues : %d matchs, places par chemin %s", matchs, totaux
    )
    return totaux
