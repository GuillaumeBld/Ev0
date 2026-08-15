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


async def resolve_and_store_club_ids(
    session: AsyncSession,
    *,
    client: TransfermarktClient | None = None,
    competitions: dict[str, str] | None = None,
) -> ResolveReport:
    """Resout les `transfermarkt_club_id` des `canonical_teams` pour toutes
    les competitions de `competitions` (par defaut `TM_COMPETITION_CODES`).

    Pour chaque club TM trouve sur les pages competition :
      - matching par `fold_accents` sur name_en / name_fr / aliases[] ;
      - match UNIVOQUE (exactement 1 canonical_team candidat) -> ecrit
        `transfermarkt_club_id` (si absent) ou confirme (si deja identique,
        idempotent, aucune ecriture) ;
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

    report = ResolveReport()
    matched_team_ids: set[int] = {
        team.id for team in teams if team.transfermarkt_club_id is not None
    }

    for tm_id, tm_name in tm_clubs.items():
        folded = fold_accents(tm_name)
        candidates = folded_index.get(folded, set())

        if len(candidates) != 1:
            reason = "aucune correspondance" if not candidates else "ambigu (plusieurs clubs canoniques)"
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
