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

import difflib
import re
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
    session: AsyncSession,
    tm_players: list[TMPlayer],
    today: date,
    bzz_team_id: int | None = None,
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

    if bzz_team_id is not None and unmatched:
        unmatched = await _match_by_dob_at_club(session, unmatched, bzz_team_id, matched)

    return MatchReport(matched=matched, unmatched=unmatched)


# Seconde passe (13/11/2026) : ~30 % de l'univers restait sans tm_id parce que
# le nom differe entre Bzzoiro et TM (diminutif, nom compose, translitteration)
# alors que le joueur EST sur la page effectif de son club. Ancre forte : meme
# club + date de naissance EXACTE (vue Detailed). Garde-fou nom pour ecarter
# "recrue nee le meme jour qu'un joueur parti".
_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NAME_SIMILARITY_MIN = 0.6
_NAME_TOKEN_MIN_LEN = 3


def _names_compatible(tm_name: str, bzz_name: str) -> bool:
    """Au moins un mot (>= 3 lettres) en commun apres repli accents/casse, ou
    similarite globale >= 0.6. Volontairement large : l'ancre est la date
    de naissance exacte + le club, ce filtre n'ecarte que les homonymes de
    date sans aucun rapport de nom."""
    a, b = fold_accents(tm_name), fold_accents(bzz_name)
    ta = {t for t in _NAME_TOKEN_RE.findall(a) if len(t) >= _NAME_TOKEN_MIN_LEN}
    tb = {t for t in _NAME_TOKEN_RE.findall(b) if len(t) >= _NAME_TOKEN_MIN_LEN}
    if ta & tb:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= _NAME_SIMILARITY_MIN


async def _match_by_dob_at_club(
    session: AsyncSession,
    unmatched: list[TMPlayer],
    bzz_team_id: int,
    matched: dict[int, int],
) -> list[TMPlayer]:
    """Rattache un `TMPlayer` non reconnu par son nom a un joueur Bzzoiro DEJA
    rattache a ce club (`current_team_api_id == bzz_team_id`), ne le meme jour
    exact, au nom compatible. Exactement 1 candidat libre, et pas reclame par
    un autre `TMPlayer` de la page -> reconnu ; sinon reste non reconnu.
    Mute `matched` ; renvoie la liste des toujours-non-reconnus."""
    roster = (
        await session.execute(
            select(BzzPlayer.api_id, BzzPlayer.name, BzzPlayer.date_of_birth).where(
                BzzPlayer.current_team_api_id == bzz_team_id,
                BzzPlayer.date_of_birth.is_not(None),
            )
        )
    ).all()
    taken = set(matched.values())
    proposals: dict[int, int] = {}
    for tm in unmatched:
        if tm.dob is None:
            continue
        candidates = [
            row.api_id
            for row in roster
            if row.date_of_birth == tm.dob
            and row.api_id not in taken
            and _names_compatible(tm.name, row.name)
        ]
        if len(candidates) == 1:
            proposals[tm.tm_player_id] = candidates[0]

    claims: dict[int, int] = {}
    for api_id in proposals.values():
        claims[api_id] = claims.get(api_id, 0) + 1
    still_unmatched: list[TMPlayer] = []
    for tm in unmatched:
        api_id = proposals.get(tm.tm_player_id)
        if api_id is not None and claims[api_id] == 1:
            matched[tm.tm_player_id] = api_id
        else:
            still_unmatched.append(tm)
    return still_unmatched
