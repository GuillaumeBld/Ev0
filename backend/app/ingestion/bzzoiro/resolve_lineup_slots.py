"""Resout les compos brutes de bzz_events en places nommees et identifiees.

Pourquoi ce module existe : sur 1 468 matchs -- du 15/08/2025 au 13/04/2026,
soit les deux premiers tiers de la saison en cours -- Bzzoiro renvoie
`"id": null` pour chaque joueur de la compo et ne laisse qu'un nom abrege
("Alisson", "A. Mac Allister"). Le rapprochement par nom complet n'aboutit
alors que dans 2,4 % des cas, car bzz_players stocke le nom entier.

La sortie : bzz_lineup_slots, une place par joueur, avec l'identifiant quand
on a su le retrouver. L'archive brute n'est jamais reecrite.

METHODE. On raisonne comme le ferait un observateur du match, du vivier le
plus etroit au plus large, en s'arretant a la premiere correspondance unique :

  1. les joueurs de CE camp sur CE match -- une vingtaine. Un nom abrege y
     est presque toujours unique, et deux homonymes adverses ("R. Garcia"
     contre "R. Garcia") cessent d'etre confondus, puisqu'ils ne sont jamais
     compares l'un a l'autre ;
  2. les joueurs des DEUX camps, au cas ou le camp serait mal renseigne ;
  3. l'effectif du club, qui rattrape le remplacant non utilise : il figure
     sur la feuille de match sans y avoir de statistique, donc il est absent
     des deux viviers precedents.

Dans chaque vivier, quatre lectures du nom, de la plus stricte a la plus
souple : nom court exact, nom complet exact, meme nom de famille, nom de
famille contenu. Le numero de maillot departage les rares egalites.

Puis, en dernier ressort, l'ELIMINATION. Quand tous les noms d'un camp sont
attribues sauf un, et qu'un seul joueur du vivier n'a ete reclame par
personne, les deux se designent mutuellement. C'est une deduction, pas un
rapprochement -- et c'est le seul moyen de couvrir les surnoms, qu'aucune
comparaison de chaines ne peut atteindre : la compo dit "Savinho" quand la
fiche dit "Savio", "M. Kim" quand elle dit "Kim Min-jae".

CE QU'ON NE FAIT PAS. Une place qu'on n'a pas su resoudre n'est pas jetee :
elle est ecrite avec player_api_id a NULL et resolution = "absent". Un trou
doit rester visible et denombrable. Il s'agit alors de remplacants non
utilises, absents du vivier faute de statistique, et dont le nom abrege ne
correspond a aucune fiche du club.
"""
from __future__ import annotations

import logging
from typing import Any

import re
import unicodedata

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_IDS

logger = logging.getLogger(__name__)

LEAGUES = [v for k, v in TARGET_LEAGUE_INTERNAL_IDS.items() if k != "champions_league"]

# Vivier d'un match : tout joueur y ayant une statistique, avec ses deux
# formes de nom, son camp et son numero. C'est l'ensemble dans lequel un nom
# abrege doit d'abord etre retrouve.
_VIVIER = text("""
    SELECT s.player_api_id AS pid, s.is_home, p.name AS nom,
           p.short_name AS court, p.jersey_number AS maillot
    FROM bzz_player_match_stats s
    JOIN bzz_players p ON p.api_id = s.player_api_id
    WHERE s.event_api_id = :ev
""")

# Effectif d'un club, dernier recours : un remplacant non utilise figure sur
# la feuille de match sans y avoir de statistique.
_EFFECTIF = text("""
    SELECT p.api_id AS pid, p.name AS nom, p.short_name AS court,
           p.jersey_number AS maillot
    FROM bzz_players p
    WHERE p.current_team_api_id = :equipe
""")

# Matchs dont la compo est exploitable et pas encore resolue.
_A_RESOUDRE = text("""
    SELECT e.api_id, e.lineups, e.home_team_api_id, e.away_team_api_id
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


def _aplati(s: str) -> str:
    """Minuscules, sans accents. « Groß » et « Gross » doivent se rejoindre."""
    s = (s or "").lower().replace("ß", "ss")
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _compacte(s: str) -> str:
    """Ne garde que les lettres et chiffres : « N'Soki » et « Nsoki » coincident."""
    return re.sub(r"[^a-z0-9]", "", _aplati(s))


def nom_de_famille(nom: str) -> str:
    """Dernier morceau qui n'est pas une simple initiale.

    « I. V. d. Brempt » rend « brempt », « R. K. Muani » rend « muani ». C'est
    la seule partie stable d'un nom abrege : les prenoms y sont reduits a des
    lettres, la fin ne l'est jamais.
    """
    morceaux = [m for m in _aplati(nom).split() if len(m.strip(".")) > 1]
    return _compacte(morceaux[-1]) if morceaux else ""


# Les quatre lectures d'un nom, de la plus stricte a la plus souple. L'ordre
# est le garde-fou : on ne descend d'un cran que faute de correspondance unique.
_LECTURES: tuple[tuple[str, Any], ...] = (
    ("court", lambda v, c, f: _compacte(v.get("court")) == c),
    ("complet", lambda v, c, f: _compacte(v.get("nom")) == c),
    # Nom de famille contre nom de famille, et non contre la fin du nom
    # complet : « X. Ann » se rapprochait sinon d'« Antoine Griezmann », dont
    # la chaine compactee finit par « ann ».
    ("famille", lambda v, c, f: bool(f) and nom_de_famille(v.get("nom")) == f),
    # Un nom de famille noye au milieu du nom complet (« Mpasi » dans « Lionel
    # Mpasi Nzau »). Exige quatre lettres : en deca, le hasard suffirait.
    ("inclus", lambda v, c, f: len(f) > 3 and f in _compacte(v.get("nom"))),
)


def chercher(
    vivier: list[dict[str, Any]], nom: str, maillot: Any
) -> tuple[int | None, str | None]:
    """(identifiant, lecture) du seul joueur du vivier portant ce nom.

    Rend (None, None) si personne ne correspond, ou si plusieurs correspondent
    sans que le numero de maillot ne departage. On ne tranche jamais au hasard.
    """
    compact, famille = _compacte(nom), nom_de_famille(nom)
    for lecture, test in _LECTURES:
        candidats = [v for v in vivier if test(v, compact, famille)]
        if len(candidats) == 1:
            return candidats[0]["pid"], lecture
        if len(candidats) > 1 and maillot is not None:
            memes = [
                v for v in candidats if str(v.get("maillot")) == str(maillot)
            ]
            if len(memes) == 1:
                return memes[0]["pid"], f"{lecture}+maillot"
    return None, None


def resoudre_place(
    joueur: dict[str, Any], viviers: list[tuple[str, list[dict[str, Any]]]]
) -> tuple[int | None, str]:
    """(identifiant, chemin) pour une place, du vivier le plus etroit au plus large.

    L'identifiant fourni par l'API prime : aucun rapprochement quand il existe.
    """
    brut = joueur.get("id")
    if brut is not None:
        return int(brut), "id"

    nom = joueur.get("name") or ""
    if not nom:
        return None, "absent"

    maillot = joueur.get("jersey_number")
    for etiquette, vivier in viviers:
        pid, lecture = chercher(vivier, nom, maillot)
        if pid is not None:
            return pid, f"{etiquette}/{lecture}"
    return None, "absent"


def places_du_match(
    lineups: dict[str, Any],
    vivier_par_camp: dict[bool, list[dict[str, Any]]],
    effectif_par_camp: dict[bool, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Toutes les places d'un match, titulaires puis remplacants, deux camps.

    Accepte les deux formes archivees : la reponse v2 entiere ou le seul bloc
    par camp.
    """
    blocs = lineups.get("lineups") or lineups
    effectif_par_camp = effectif_par_camp or {}
    tout_le_match = vivier_par_camp.get(True, []) + vivier_par_camp.get(False, [])

    places: list[dict[str, Any]] = []
    for cote, dom in (("home", True), ("away", False)):
        bloc = blocs.get(cote) or {}
        viviers = [
            ("camp", vivier_par_camp.get(dom, [])),
            ("match", tout_le_match),
            ("effectif", effectif_par_camp.get(dom, [])),
        ]
        # Rang sur la feuille du camp : c'est lui qui identifie une place. Deux
        # joueurs d'un meme camp peuvent porter le meme nom abrege -- Osasuna
        # aligne deux "R. Garcia" -- et une cle nominale les confondrait.
        rang = 0
        for section, titulaire in (("players", True), ("substitutes", False)):
            for j in bloc.get(section) or []:
                nom = j.get("name") or ""
                if not nom:
                    continue
                pid, chemin = resoudre_place(j, viviers)
                places.append({
                    "is_home": dom,
                    "slot": rang,
                    "is_starter": titulaire,
                    "player_name": nom,
                    "player_api_id": pid,
                    "resolution": chemin,
                    "position": (j.get("position") or None),
                    "jersey_number": _entier(j.get("jersey_number")),
                })
                rang += 1

    for dom in (True, False):
        _par_elimination(
            [p for p in places if p["is_home"] == dom], vivier_par_camp.get(dom, [])
        )
    return places


def _par_elimination(places: list[dict[str, Any]], vivier: list[dict[str, Any]]) -> None:
    """Dernier recours : le seul nom restant designe le seul joueur restant.

    C'est ce que fait un observateur devant une feuille de match. Quand tous
    les noms d'un camp sont attribues sauf un, et qu'un seul joueur du vivier
    n'a ete reclame par personne, les deux se designent mutuellement -- sans
    qu'aucune chaine n'ait a se ressembler.

    C'est ce qui rattrape les surnoms, que nul rapprochement litteral ne peut
    couvrir : la compo dit "Savinho" quand la fiche dit "Savio", "M. Kim"
    quand elle dit "Kim Min-jae", "P. T. Jimenez" quand elle dit "Pol
    Tristan".

    La deduction n'est valable qu'a UN contre UN. Des qu'il reste deux noms ou
    deux joueurs libres, on s'abstient : rien ne dit lequel va avec lequel.
    """
    perdus = [p for p in places if p["player_api_id"] is None]
    if len(perdus) != 1:
        return
    reclames = {p["player_api_id"] for p in places if p["player_api_id"] is not None}
    libres = [v for v in vivier if v["pid"] not in reclames]
    if len(libres) != 1:
        return
    perdus[0]["player_api_id"] = libres[0]["pid"]
    perdus[0]["resolution"] = "elimination"


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
    # Les effectifs de club servent de dernier recours et se repetent d'un
    # match a l'autre : on ne les relit qu'une fois par equipe.
    effectifs: dict[int, list[dict[str, Any]]] = {}

    async def effectif(equipe: int | None) -> list[dict[str, Any]]:
        if equipe is None:
            return []
        if equipe not in effectifs:
            effectifs[equipe] = [
                dict(r) for r in (await session.execute(
                    _EFFECTIF, {"equipe": equipe}
                )).mappings()
            ]
        return effectifs[equipe]

    for ev in evenements:
        vivier = [dict(r) for r in (await session.execute(
            _VIVIER, {"ev": ev["api_id"]}
        )).mappings()]
        places = places_du_match(
            ev["lineups"],
            {
                True: [v for v in vivier if v["is_home"]],
                False: [v for v in vivier if not v["is_home"]],
            },
            {
                True: await effectif(ev["home_team_api_id"]),
                False: await effectif(ev["away_team_api_id"]),
            },
        )
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
                "player_name": stmt.excluded.player_name,
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
