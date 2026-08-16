"""Resolution des `canonical_teams.transfermarkt_club_id` pour les clubs des
ligues couvertes, a partir des pages competition Transfermarkt.

Reutilise le client HTTP durci (`TransfermarktClient` : UA navigateur,
rate-limit >= 2s, retry/backoff 429/5xx) de `app.scripts.transfermarkt_career`
plutot que de le reimplementer.

Regle d'or (identique au reste de l'ingestion Transfermarkt) : un club TM
n'est associe a un `canonical_teams` que si le matching (par nom, insensible
aux accents/casse via `fold_accents`) est UNIVOQUE. Toute ambiguite ou
absence de correspondance est renvoyee dans le rapport, jamais devinee ni
ecrite au hasard. Idempotent : ne recrit pas un id deja correct, ne cree
jamais de doublon (un `transfermarkt_club_id` differe deja pose -> conflit,
retourne en non-resolu, jamais ecrase).
"""
from __future__ import annotations

import difflib
import html
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.transfermarkt.text_utils import fold_accents
from app.models.canonical_teams import CanonicalTeam
from app.scripts.transfermarkt_career import BASE_URL, TransfermarktClient

logger = logging.getLogger(__name__)

# Seuil de similarite (difflib.SequenceMatcher.ratio, sur chaines repliees
# via fold_accents) au-dela duquel deux noms sont consideres comme un match
# fuzzy candidat. Choisi pour capturer "Olympique Lyon" / "Olympique
# Lyonnais" (~0.875) sans devenir trop permissif (cf. `_match_candidates`).
FUZZY_MATCH_THRESHOLD = 0.85

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Codes de competition Transfermarkt (`/wettbewerb/<CODE>`) pour les ligues
# couvertes par la plateforme.
TM_COMPETITION_CODES: dict[str, str] = {
    "premier_league": "GB1",
    "ligue_1": "FR1",
    "bundesliga": "L1",
    "la_liga": "ES1",
    "serie_a": "IT1",
    "champions_league": "CL",
}

# Les clubs d'une page competition TM apparaissent comme des liens
# `/<slug>/startseite/verein/<id>` (parfois suivis de `/saison_id/<annee>`),
# avec le nom lisible du club dans l'attribut `title` (ex:
# `<a title="Paris Saint-Germain" href="/fc-paris-saint-germain/startseite/verein/583/saison_id/2026">`).
# Verifie contre un fetch reel de https://www.transfermarkt.com/-/startseite/wettbewerb/FR1
# (voir backend/tests/fixtures/transfermarkt_ligue1_wettbewerb.html).
CLUB_LINK_RE = re.compile(
    r'<a title="([^"]*)" href="/[a-z0-9\-]+/startseite/verein/(\d+)(?:/saison_id/\d+)?"'
)


@dataclass
class ResolveReport:
    """Resultat d'un passage de resolution club TM -> canonical_teams."""

    resolved: int = 0
    unresolved_tm: list[str] = field(default_factory=list)
    unmatched_canonical: list[str] = field(default_factory=list)


def parse_competition_clubs(html_text: str) -> list[tuple[str, int]]:
    """Extrait la liste (nom_club, tm_club_id) d'une page competition TM.

    Deduplique par id (un meme club peut apparaitre plusieurs fois dans la
    page : icone + lien texte), en conservant l'ordre de premiere apparition.
    """
    seen: dict[int, str] = {}
    for raw_name, raw_id in CLUB_LINK_RE.findall(html_text):
        club_id = int(raw_id)
        if club_id not in seen:
            seen[club_id] = html.unescape(raw_name)
    return [(name, club_id) for club_id, name in seen.items()]


def fetch_competition_clubs(client: TransfermarktClient, code: str) -> list[tuple[str, int]]:
    """Recupere et parse la page competition TM pour `code` (ex: 'FR1')."""
    url = f"{BASE_URL}/-/startseite/wettbewerb/{code}"
    resp = client.get(url, what=f"la page competition Transfermarkt '{code}'")
    clubs = parse_competition_clubs(resp.text)
    logger.info("Competition '%s' -> %d club(s) trouve(s).", code, len(clubs))
    return clubs


def _canonical_name_variants(team: CanonicalTeam) -> list[str]:
    variants = [team.name_fr]
    if team.name_en:
        variants.append(team.name_en)
    variants.extend(team.aliases or [])
    return variants


def _build_folded_index(teams: list[CanonicalTeam]) -> dict[str, set[int]]:
    """folded(nom) -> ensemble des ids canonical_teams qui portent ce nom.

    Un ensemble de taille > 1 pour une meme cle signale une ambiguite (deux
    clubs canoniques differents partagent le meme nom/alias replie) : le
    club TM correspondant sera alors renvoye en non-resolu plutot que
    d'etre associe au hasard.
    """
    index: dict[str, set[int]] = {}
    for team in teams:
        for variant in _canonical_name_variants(team):
            if not variant:
                continue
            key = fold_accents(variant)
            if not key:
                continue
            index.setdefault(key, set()).add(team.id)
    return index


def _tokenize(name: str) -> frozenset[str]:
    """Decoupe `name` en un ensemble de tokens alphanumeriques repliés
    (accents/casse) pour un matching insensible a l'ordre et a la
    ponctuation.

    Le token "1" est systematiquement ignore : c'est le prefixe ordinal
    allemand ("1.FC Koln", "1. FSV Mainz 05", "1.FC Union Berlin") qui ne
    porte aucune information d'identite de club et ne doit jamais empecher
    un match avec un nom canonique qui ne le porte pas (ex: "FC Koln").
    """
    tokens = _TOKEN_RE.findall(fold_accents(name))
    return frozenset(tok for tok in tokens if tok != "1")


def _build_token_set_index(teams: list[CanonicalTeam]) -> dict[frozenset[str], set[int]]:
    """tokens(nom) (ensemble, ordre-independant) -> ensemble des ids
    canonical_teams qui portent un nom/alias avec exactement cet ensemble
    de tokens. Capture par ex. "FC Toulouse" == "Toulouse FC"."""
    index: dict[frozenset[str], set[int]] = {}
    for team in teams:
        for variant in _canonical_name_variants(team):
            if not variant:
                continue
            tokens = _tokenize(variant)
            if not tokens:
                continue
            index.setdefault(tokens, set()).add(team.id)
    return index


def _token_subset_candidates(tm_tokens: frozenset[str], teams: list[CanonicalTeam]) -> set[int]:
    """Clubs canoniques dont AU MOINS UN variant a un ensemble de tokens
    entierement contenu dans `tm_tokens` (containment, pas egalite -
    l'egalite est deja traitee par `_build_token_set_index` en amont).

    Capture par ex. tokens("RC Strasbourg") = {rc, strasbourg} contenu dans
    tokens("RC Strasbourg Alsace") = {rc, strasbourg, alsace}.
    """
    candidates: set[int] = set()
    if not tm_tokens:
        return candidates
    for team in teams:
        for variant in _canonical_name_variants(team):
            if not variant:
                continue
            variant_tokens = _tokenize(variant)
            # Sous-ensemble STRICT : l'egalite (variant_tokens == tm_tokens)
            # est deja couverte par `_build_token_set_index` au niveau
            # "token_set" precedent, atteint avant celui-ci.
            if variant_tokens and variant_tokens < tm_tokens:
                candidates.add(team.id)
                break
    return candidates


def _fuzzy_candidates(tm_folded: str, teams: list[CanonicalTeam]) -> set[int]:
    """Clubs canoniques dont le meilleur variant (par ratio difflib sur
    chaines repliees) atteint `FUZZY_MATCH_THRESHOLD`. Dernier recours,
    utilise seulement si aucun candidat n'a ete trouve aux niveaux plus
    stricts. Capture par ex. "Olympique Lyon" / "Olympique Lyonnais"
    (ratio ~0.875)."""
    candidates: set[int] = set()
    for team in teams:
        best_ratio = 0.0
        for variant in _canonical_name_variants(team):
            if not variant:
                continue
            ratio = difflib.SequenceMatcher(None, tm_folded, fold_accents(variant)).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
        if best_ratio >= FUZZY_MATCH_THRESHOLD:
            candidates.add(team.id)
    return candidates


def _match_candidates(
    tm_name: str,
    teams: list[CanonicalTeam],
    folded_index: dict[str, set[int]],
    token_set_index: dict[frozenset[str], set[int]],
) -> tuple[set[int], str]:
    """Retourne (candidats, niveau_atteint) pour `tm_name`, en essayant
    chaque niveau de matching dans l'ordre, du plus strict au plus
    permissif, et en s'arretant au PREMIER niveau qui produit au moins un
    candidat (0, 1 ou plusieurs) :

      1. "exact"         egalite normalisee exacte (fold_accents), nom
                          complet contre nom complet.
      2. "token_set"      egalite de l'ensemble de tokens, ordre-independant
                          (ex: "FC Toulouse" == "Toulouse FC").
      3. "token_subset"   containment de tokens : tokens(canonique) contenu
                          dans tokens(TM) (ex: "RC Strasbourg" contenu dans
                          "RC Strasbourg Alsace" ; gere aussi le prefixe
                          ordinal allemand "1." via `_tokenize`).
      4. "fuzzy"          similarite difflib >= FUZZY_MATCH_THRESHOLD,
                          dernier recours.

    Important : si un niveau produit PLUSIEURS candidats (ambigu), on
    s'arrete la et on renvoie cet ensemble ambigu tel quel -> on ne
    redescend JAMAIS vers un niveau plus permissif pour tenter de
    "departager", ce qui reviendrait a deviner. L'appelant traite tout
    ensemble de taille != 1 comme non resolu.
    """
    folded = fold_accents(tm_name)
    candidates = folded_index.get(folded, set())
    if candidates:
        return candidates, "exact"

    tm_tokens = _tokenize(tm_name)
    candidates = token_set_index.get(tm_tokens, set())
    if candidates:
        return candidates, "token_set"

    candidates = _token_subset_candidates(tm_tokens, teams)
    if candidates:
        return candidates, "token_subset"

    candidates = _fuzzy_candidates(folded, teams)
    if candidates:
        return candidates, "fuzzy"

    return set(), "none"


async def resolve_and_store_club_ids(
    session: AsyncSession,
    *,
    client: TransfermarktClient | None = None,
    competitions: dict[str, str] | None = None,
) -> ResolveReport:
    """Resout les `transfermarkt_club_id` des `canonical_teams` pour toutes
    les competitions de `competitions` (par defaut `TM_COMPETITION_CODES`).

    Pour chaque club TM trouve sur les pages competition, le matching est
    tente sur name_en / name_fr / aliases[] a travers 4 niveaux, du plus
    strict au plus permissif (voir `_match_candidates`) :
      1. egalite normalisee exacte (fold_accents) ;
      2. egalite d'ensemble de tokens, ordre-independant ;
      3. containment de tokens (nom canonique contenu dans le nom TM) ;
      4. similarite fuzzy (difflib) >= `FUZZY_MATCH_THRESHOLD`, dernier
         recours.
    Le premier niveau qui produit un resultat non vide est retenu (jamais
    de fallback vers un niveau plus permissif si le niveau strict a deja
    trouve un candidat, meme ambigu) :
      - match UNIVOQUE a ce niveau (exactement 1 canonical_team candidat)
        -> ecrit `transfermarkt_club_id` (si absent) ou confirme (si deja
        identique, idempotent, aucune ecriture) ;
      - 0 candidat, >1 candidat (ambigu), ou candidat deja associe a un
        `transfermarkt_club_id` DIFFERENT -> jamais ecrit, le club TM est
        ajoute a `unresolved_tm`.

    `unmatched_canonical` liste les `canonical_teams` qui, a l'issue du
    passage, n'ont toujours pas de `transfermarkt_club_id` (ni prealable, ni
    resolu ce run).
    """
    codes = competitions if competitions is not None else TM_COMPETITION_CODES
    owns_client = client is None
    client = client or TransfermarktClient()

    try:
        # 1. Collecte de tous les clubs TM des competitions demandees,
        #    dedupliques par tm_club_id (un meme club peut apparaitre dans
        #    plusieurs competitions, ex: sa ligue nationale + la C1).
        tm_clubs: dict[int, str] = {}
        for code in codes.values():
            for name, club_id in fetch_competition_clubs(client, code):
                tm_clubs.setdefault(club_id, name)
    finally:
        if owns_client:
            client.close()

    # 2. Charge les canonical_teams et construit l'index de matching.
    teams = list((await session.execute(select(CanonicalTeam))).scalars().all())
    teams_by_id = {team.id: team for team in teams}
    folded_index = _build_folded_index(teams)
    token_set_index = _build_token_set_index(teams)

    report = ResolveReport()
    matched_team_ids: set[int] = {
        team.id for team in teams if team.transfermarkt_club_id is not None
    }

    for tm_id, tm_name in tm_clubs.items():
        candidates, level = _match_candidates(tm_name, teams, folded_index, token_set_index)

        if len(candidates) != 1:
            reason = "aucune correspondance" if not candidates else f"ambigu (plusieurs clubs canoniques, niveau={level})"
            logger.warning("Club TM '%s' (id=%d) non resolu : %s.", tm_name, tm_id, reason)
            report.unresolved_tm.append(tm_name)
            continue

        team_id = next(iter(candidates))
        team = teams_by_id[team_id]

        if team.transfermarkt_club_id == tm_id:
            # Deja correctement associe (run precedent) -> idempotent, rien
            # a ecrire, mais bien compte comme resolu.
            matched_team_ids.add(team_id)
            report.resolved += 1
            continue

        if team.transfermarkt_club_id is not None:
            # Deja associe a un AUTRE id TM -> conflit, ne jamais ecraser
            # silencieusement une valeur existante differente.
            logger.warning(
                "Club TM '%s' (id=%d) matche canonical_team '%s' (id=%d) mais celui-ci a "
                "deja transfermarkt_club_id=%d -> conflit, non resolu.",
                tm_name, tm_id, team.name_fr, team.id, team.transfermarkt_club_id,
            )
            report.unresolved_tm.append(tm_name)
            continue

        team.transfermarkt_club_id = tm_id
        session.add(team)
        matched_team_ids.add(team_id)
        report.resolved += 1

    report.unmatched_canonical = sorted(
        team.name_fr for team in teams if team.id not in matched_team_ids
    )

    await session.commit()
    return report
