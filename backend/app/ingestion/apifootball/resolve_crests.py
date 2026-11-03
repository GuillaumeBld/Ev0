"""Resolution automatique des ecussons (`canonical_teams.api_football_id`)
via l'API-Football, pour les ~57 competitions de l'expansion joueurs.

Les ecussons des clubs sont historiquement resolus cote front par un
dictionnaire nom -> id code en dur (`frontend/src/lib/teamLogos.ts`), limite
au big-5 + UCL : faux par collision de noms, et absent pour tout nouveau
club hors de ce perimetre. `canonical_teams.api_football_id` est la cible —
deja la colonne consultee par la page admin des clubs (`TeamLogo`) — mais
elle reste NULL pour l'immense majorite des clubs des nouvelles competitions.

Ce module la remplit automatiquement : pour chaque club canonique engage
(`league_api_id` connu) sans ecusson, recherche par nom (+ pays si connu)
via `GET /teams?search=...` d'API-Football. Regle d'or, identique au reste
de l'ingestion (Transfermarkt notamment) : un ecusson n'est pose que si la
recherche rend un candidat UNIQUE. Zero ou plusieurs candidats -> le club
reste NULL et la raison est consignee dans le rapport. Mieux vaut aucun
ecusson qu'un faux, c'est tout le point de cette tache.

Un candidat UNIQUE ne suffit pas a lui seul : c'est aussi le scenario exact
d'une fausse homonymie (deux clubs du meme nom dans deux pays differents).
Quand le pays canonique du club (`CanonicalTeam.country`) est connu, le
pays du candidat rendu par l'API (`team.country`) DOIT correspondre (apres
normalisation — casse/accents/quelques variantes de libelle courantes) ;
sinon le candidat est REJETE (consigne dans `rejected`, `api_football_id`
reste NULL) — jamais pose sur la foi du seul nom. Quand le pays canonique
est inconnu (`None`), on ne peut pas faire cette verification : l'id est
tout de meme pose (mieux que rien, et c'est deja le filtre "candidat
unique" qui protege), mais range a part dans `resolved_unverified` plutot
que `resolved`, pour que la revue manuelle (Tache 7, `dry_run`) cible
precisement ces cas non verifies.

Execution reelle differee a la Tache 7 : `settings.api_football_key` est vide
en .env a ce jour. Ce module est complet et teste (mocks, aucun reseau) mais
n'est cable dans aucun worker/script planifie — voir `ApiFootballClient`
pour l'erreur claire levee en l'absence de cle.
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.canonical_teams import CanonicalTeam

logger = logging.getLogger(__name__)

BASE_URL = "https://v3.football.api-sports.io"
DEFAULT_TIMEOUT = 20.0

# Variantes de libelle de pays rencontrees en pratique (registre interne en
# anglais, cf. `app.ingestion.bzzoiro.league_registry`, vs libelles parfois
# differents rendus par API-Football). Cle et valeur deja normalisees
# (minuscules, sans accent) par `_normalize_country`. Volontairement court :
# n'ajouter une entree que si un cas reel l'exige, pas par anticipation.
_COUNTRY_ALIASES: dict[str, str] = {
    "czechia": "czech republic",
    "angleterre": "england",
    "ecosse": "scotland",
    "pays de galles": "wales",
    "irlande du nord": "northern ireland",
    "etats-unis": "united states",
    "usa": "united states",
    "coree du sud": "south korea",
    "korea republic": "south korea",
}


def _normalize_country(value: str | None) -> str | None:
    """Normalise un libelle de pays pour comparaison tolerante.

    Minuscules, accents retires, espaces superflus retires, puis repli sur
    `_COUNTRY_ALIASES` pour les quelques variantes connues. `None` reste
    `None` (pas de pays = pas de comparaison possible, gere par l'appelant).
    """
    if not value:
        return None
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    )
    normalise = " ".join(sans_accents.strip().lower().split())
    return _COUNTRY_ALIASES.get(normalise, normalise)


class ApiFootballKeyMissingError(RuntimeError):
    """`settings.api_football_key` est vide.

    Leve explicitement plutot que de laisser httpx echouer avec un 401/403
    obscur — l'appelant doit savoir immediatement qu'il manque la cle, pas
    deviner pourquoi l'API refuse la requete.
    """


@dataclass(frozen=True)
class TeamCandidate:
    """Un club candidat rendu par `GET /teams?search=...`."""

    api_football_id: int
    name: str
    country: str | None = None


class ApiFootballClient:
    """Client minimal API-Football — recherche de clubs par nom.

    Usage :
        async with ApiFootballClient() as client:
            candidates = await client.search_teams("Paris Saint-Germain", country="France")

    Leve `ApiFootballKeyMissingError` a la construction si aucune cle n'est
    fournie explicitement et que `settings.api_football_key` est vide — pas
    au premier appel reseau, pour echouer tot et clairement.
    """

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL) -> None:
        resolved_key = api_key if api_key is not None else settings.api_football_key
        if not resolved_key:
            raise ApiFootballKeyMissingError(
                "API_FOOTBALL_KEY manquante : impossible d'interroger "
                "API-Football sans cle. Renseigner `api_football_key` dans "
                ".env (settings.api_football_key) avant de lancer la "
                "resolution des ecussons — execution reelle differee a la "
                "Tache 7."
            )
        self._api_key = resolved_key
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ApiFootballClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"x-apisports-key": self._api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def search_teams(self, name: str, country: str | None = None) -> list[TeamCandidate]:
        """`GET /teams?search=<name>[&country=<country>]` -> candidats bruts.

        Ne filtre ni ne juge l'ambiguite ici : `resolve_crests` decide seul,
        sur le nombre de candidats rendus, si le match est univoque.
        """
        if self._client is None:
            raise RuntimeError("Use ApiFootballClient as an async context manager")

        params: dict[str, str] = {"search": name}
        if country:
            params["country"] = country

        response = await self._client.get("/teams", params=params)
        response.raise_for_status()
        data = response.json()

        candidates: list[TeamCandidate] = []
        for item in data.get("response", []) or []:
            team = item.get("team") or {}
            team_id = team.get("id")
            if team_id is None:
                continue
            candidates.append(
                TeamCandidate(
                    api_football_id=team_id,
                    name=team.get("name") or name,
                    country=team.get("country"),
                )
            )
        return candidates


@dataclass
class UnresolvedTeam:
    """Un club canonique laisse NULL, avec la raison et les candidats vus."""

    canonical_id: int
    name: str
    candidates: list[str] = field(default_factory=list)


@dataclass
class CrestReport:
    """Bilan d'un passage de resolution d'ecussons.

    `resolved` : candidat unique ET pays verifie (soit candidat et pays
    canonique concordent, soit pas de verification necessaire). Seul ce
    bucket est "sur" au sens plein.

    `resolved_unverified` : candidat unique pose MAIS pays canonique
    inconnu (`CanonicalTeam.country is None`) — pas de verification pays
    possible. L'id est quand meme pose (voir docstring du module), mais
    range a part pour cibler la revue manuelle (Tache 7, `dry_run`).

    `rejected` : candidat unique dont le pays NE correspond PAS au pays
    canonique connu — l'homonymie inter-pays exacte que cette tache existe
    pour eviter. `api_football_id` reste NULL, jamais pose sur ce candidat.
    """

    resolved: list[str] = field(default_factory=list)
    resolved_unverified: list[str] = field(default_factory=list)
    rejected: list[UnresolvedTeam] = field(default_factory=list)
    ambiguous: list[UnresolvedTeam] = field(default_factory=list)
    not_found: list[UnresolvedTeam] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved)


async def resolve_crests(
    session: AsyncSession,
    client: ApiFootballClient,
    *,
    dry_run: bool = False,
) -> CrestReport:
    """Resout `api_football_id` pour les clubs canoniques engages qui n'en
    ont pas encore.

    Perimetre : `CanonicalTeam.league_api_id IS NOT NULL` (club engage dans
    une competition suivie) ET `api_football_id IS NULL` (pas deja resolu —
    idempotent, ne retouche jamais une valeur existante, meme fausse : la
    correction manuelle reste la voie, voir la page admin des clubs).

    Pour chaque club : recherche par `name_fr` (+ `country` si connu). Zero
    ou plusieurs candidats -> laisse NULL, consigne dans le rapport
    (`not_found` / `ambiguous`) — jamais un ecusson devine. Un candidat
    UNIQUE : si le pays canonique est connu, le pays du candidat doit
    correspondre (normalise) pour que l'id soit pose (`resolved`) ; sinon
    rejete (`rejected`, NULL) — jamais un faux ecusson par homonymie
    inter-pays. Si le pays canonique est inconnu, l'id est pose mais range
    a part (`resolved_unverified`), voir `CrestReport`.

    Un echec reseau sur un club ne bloque pas les suivants : consigne dans
    `errors` et continue, pour ne pas perdre tout le lot sur une seule
    requete en erreur.
    """
    report = CrestReport()

    teams = (
        await session.execute(
            select(CanonicalTeam).where(
                CanonicalTeam.league_api_id.is_not(None),
                CanonicalTeam.api_football_id.is_(None),
            )
        )
    ).scalars().all()

    for team in teams:
        name = team.name_fr
        country = team.country or None

        try:
            candidates = await client.search_teams(name, country=country)
        except Exception as exc:  # jamais un echec reseau silencieux
            logger.error("API-Football: recherche echouee pour %r: %s", name, exc)
            report.errors.append(f"{name}: {exc}")
            continue

        if len(candidates) == 1:
            candidate = candidates[0]
            if country is None:
                # Pays canonique inconnu : impossible de verifier. Pose
                # quand meme (candidat unique), mais trace a part.
                team.api_football_id = candidate.api_football_id
                report.resolved_unverified.append(name)
                logger.info(
                    "API-Football: %r -> %d (pays canonique inconnu, non verifie)",
                    name,
                    candidate.api_football_id,
                )
            elif _normalize_country(candidate.country) == _normalize_country(country):
                team.api_football_id = candidate.api_football_id
                report.resolved.append(name)
                logger.info("API-Football: %r -> %d", name, candidate.api_football_id)
            else:
                # Candidat unique mais pays incoherent : homonymie
                # inter-pays. Jamais un faux ecusson -> rejete, pas pose.
                logger.warning(
                    "API-Football: candidat rejete pour %r - pays incoherent "
                    "(canonique=%r, candidat=%r)",
                    name,
                    country,
                    candidate.country,
                )
                report.rejected.append(
                    UnresolvedTeam(
                        canonical_id=team.id,
                        name=name,
                        candidates=[f"{candidate.name} ({candidate.country})"],
                    )
                )
        elif len(candidates) == 0:
            logger.warning("API-Football: aucun candidat pour %r (pays=%r)", name, country)
            report.not_found.append(UnresolvedTeam(canonical_id=team.id, name=name))
        else:
            noms = [c.name for c in candidates]
            logger.warning(
                "API-Football: %d candidats ambigus pour %r: %s", len(candidates), name, noms
            )
            report.ambiguous.append(
                UnresolvedTeam(canonical_id=team.id, name=name, candidates=noms)
            )

    if not dry_run:
        await session.commit()

    return report
