"""Matching des joueurs d'un effectif Transfermarkt (`TMPlayer`) vers les
lignes `bzz_players` correspondantes, par nom complet + age.

Contexte : la page effectif TM (vue "Compact", voir `squad_scraper.py`)
n'expose jamais la date de naissance -> `TMPlayer.dob` vaut toujours `None`,
seul `TMPlayer.age` (entier) est disponible. Le matching ne peut donc PAS
utiliser une date de naissance exacte comme le fait deja `find_player_api_id`
(`app.scripts.import_career`) pour les donnees de carriere ; il utilise a la
place un nom complet identique (replie accents/casse via `fold_accents`, deja
utilise par `resolve_clubs.py` pour le meme type de matching cote clubs) +
une tolerance d'age de +/-1 an calculee a partir de `bzz_players.date_of_birth`.

Regle d'or (comme partout ailleurs dans l'ingestion Transfermarkt) : zero
faux positif prime sur le rappel. Toute ambiguite (0 ou plusieurs candidats
apres filtrage) est renvoyee en `unmatched`, jamais devinee.

Strategie de requete candidats (perf) :
`bzz_players` compte ~106k lignes -> hors de question de tout charger en RAM
(contrairement a `resolve_clubs.py` qui charge `canonical_teams`, une table
de quelques centaines de lignes, en RAM pour construire un index reple une
fois pour toutes). A la place, pour chaque `TMPlayer` on lance UNE requete SQL
qui pre-filtre les candidats par nom au niveau de la base : `lower(unaccent(
bzz_players.name)) = <nom TM replie>`. `unaccent()` est l'extension Postgres
deja utilisee ailleurs dans ce backend pour le meme besoin (voir
`app.api.wc2026._WC_CTE` : `lower(regexp_replace(unaccent(name), ...))`),
donc deja active sur la base cible -> pas de nouvelle dependance. Combine a
`lower()`, cette expression est l'equivalent SQL quasi-exact de `fold_accents`
(a la compression des espaces multiples pres, un cas limite qui n'affecte pas
des noms de joueurs "propres"). Chaque requete ne ramene donc que les
quelques lignes (le plus souvent 0 a 2) qui partagent le nom du joueur
recherche, jamais la table entiere.

Cette egalite SQL n'est qu'un PRE-filtre : le veritable filtre d'egalite
(la garantie "zero faux positif") est ensuite reapplique cote Python via
`fold_accents(candidate.name) == fold_accents(tm.name)` sur les quelques
lignes ramenees, en defense en profondeur contre tout ecart mineur entre la
semantique de l'extension Postgres `unaccent()` et celle, NFD, de
`fold_accents` (ex: espaces multiples, formes Unicode non canoniques rares).
Cote SQLite (tests), l'extension `unaccent` n'existe pas nativement : les
tests l'emulent en enregistrant une fonction SQL du meme nom sur la
connexion (voir `backend/tests/test_tm_player_match.py`), pour exercer le
meme chemin de requete que la production plutot que de le contourner.

Un index fonctionnel Postgres sur `lower(unaccent(name))` accelererait
encore ces requetes (actuellement `bzz_players.name` n'est pas indexe) ; non
ajoute ici car hors perimetre de cette tache (pas de migration demandee) et
non necessaire pour la correction du matching, seulement pour sa vitesse a
tres grande echelle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.transfermarkt.squad_scraper import TMPlayer
from app.ingestion.transfermarkt.text_utils import fold_accents
from app.models.bzzoiro import BzzPlayer


@dataclass
class MatchReport:
    """Resultat d'une passe de matching TM -> bzz_players.

    `matched` : `{tm_player_id: bzz_api_id}`, un seul candidat retenu.
    `unmatched` : les `TMPlayer` sans candidat unique (0 ou ambigu).
    """

    matched: dict[int, int]
    unmatched: list[TMPlayer]


def _age_at(dob: date, today: date) -> int:
    """Age en annees revolues de `dob` a la date `today`."""
    age = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        age -= 1
    return age


async def _folded_name_candidates(
    session: AsyncSession, folded_name: str
) -> list[tuple[int, str, date | None]]:
    """Candidats `bzz_players` dont le nom, une fois replie (accents/casse),
    egale `folded_name`. Pre-filtre SQL (`lower(unaccent(name))`, voir
    docstring du module) + confirmation exacte cote Python via
    `fold_accents` (defense en profondeur)."""
    stmt = select(BzzPlayer.api_id, BzzPlayer.name, BzzPlayer.date_of_birth).where(
        func.lower(func.unaccent(BzzPlayer.name)) == folded_name
    )
    rows = (await session.execute(stmt)).all()
    return [
        (row.api_id, row.name, row.date_of_birth)
        for row in rows
        if fold_accents(row.name) == folded_name
    ]


async def match_players(
    session: AsyncSession, tm_players: list[TMPlayer], today: date
) -> MatchReport:
    """Matche chaque `TMPlayer` a une ligne `bzz_players`, par nom complet
    replie (`fold_accents`) + age (+/-1 an, calcule a `today`).

    Pour un `TMPlayer` donne :
      - candidats = lignes `bzz_players` dont `fold_accents(name) ==
        fold_accents(tm.name)` (egalite du nom COMPLET, jamais une
        inclusion, pour eviter les faux positifs) ;
      - si `tm.age` est connu : on ne garde que les candidats dont l'age
        (calcule depuis `date_of_birth` a `today`) est a +/-1 an de
        `tm.age`. Un candidat dont `date_of_birth` est NULL a un age
        inconnu -> jamais retenu par ce filtre. Exactement 1 candidat
        retenu -> matche ; 0 ou plusieurs -> unmatched ;
      - si `tm.age` est `None` (page TM sans age exploitable) : aucun
        filtre d'age n'est applicable -> on ne matche que si le nom repli
        est UNIQUE parmi tous les candidats (exactement 1 candidat au
        total), sinon unmatched (jamais de devinette).
    """
    matched: dict[int, int] = {}
    unmatched: list[TMPlayer] = []

    for tm in tm_players:
        folded_name = fold_accents(tm.name)
        candidates = await _folded_name_candidates(session, folded_name)

        if tm.age is None:
            if len(candidates) == 1:
                matched[tm.tm_player_id] = candidates[0][0]
            else:
                unmatched.append(tm)
            continue

        age_filtered = [
            candidate
            for candidate in candidates
            if candidate[2] is not None and abs(_age_at(candidate[2], today) - tm.age) <= 1
        ]

        if len(age_filtered) == 1:
            matched[tm.tm_player_id] = age_filtered[0][0]
        else:
            unmatched.append(tm)

    return MatchReport(matched=matched, unmatched=unmatched)
