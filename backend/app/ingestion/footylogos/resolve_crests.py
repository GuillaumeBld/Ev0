"""Resolution automatique des ecussons (`canonical_teams.crest_url`) via
footylogos.com — gratuit, sans cle API.

Contrairement a `app/ingestion/apifootball/resolve_crests.py` (candidat
UNIQUE par recherche nominative, jamais execute en prod faute de cle),
footylogos.com n'a pas d'API de recherche : la resolution se fait par
devinette de SLUG puis VERIFICATION de l'image.

Algorithme, dans l'ordre, pour chaque club canonique engage sans ecusson :

  1. Dictionnaire FR->EN seed (`FR_EN_SEED`) — nos noms francises
     (Barcelone, Seville, Naples...) ne matchent pas le slug anglais de
     footylogos par simple slugification.
  2. Slug direct — nom normalise (NFKD -> ASCII, minuscules, `&` -> `and`,
     non-alphanumerique -> `-`).
  3. Variantes — affixe de tete retire (fc/fk/ac/as/sc/cf/ud/sd/rc/cd),
     suffixes ajoutes (-fc, -cf, -united, -city, -town), prefixe `afc-`.
  4. Repli : page pays footylogos (`/fr/country/{country_slug}`), qui liste
     tous les clubs du pays via des liens `/fr/logos/{slug}`. Le club est
     matche au slug du pays le plus proche par similarite de chaine
     (`difflib.SequenceMatcher` sur formes normalisees, seuil 0.82 ET marge
     >= 0.08 avec le 2e candidat — restreindre au pays seul ne suffit PAS a
     ecarter les homonymes INTRA-pays, ex. "manchester-city" vs
     "manchester-united" : 0.81 de similarite, tous les deux dans le meme
     pays. En cas de doute (score insuffisant ou trop proche du 2e), le
     repli ne pose RIEN plutot que de risquer un mauvais ecusson). La page
     pays elle-meme est mise en cache en memoire pour la duree du run
     (`resolve_crests`), un seul GET par pays meme si plusieurs clubs y
     tombent.

GARDE-FOU ABSOLU, identique au reste de l'ingestion (Transfermarkt,
API-Football) : `crest_url` n'est POSE que si l'URL image candidate rend
un GET 200 (verifie a chaque etape, jamais sur la seule plausibilite du
slug). Aucun candidat valide -> `crest_url` reste NULL et le club est
consigne dans `CrestReport.not_found` — jamais un faux ecusson.

Verification : GET (jamais HEAD) avec un User-Agent navigateur. Le CDN
footylogos repond 403/faux-negatif a HEAD et a l'UA par defaut
`python-urllib` ; un vrai GET navigateur rend 200.

Non cable dans worker.py : execution manuelle, comme
`app/scripts/build_full_referentiel.py` (voir `_main` / CLI en bas de
fichier).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_teams import CanonicalTeam

logger = logging.getLogger(__name__)

ASSETS_BASE = "https://assets.footylogos.com"
WWW_BASE = "https://www.footylogos.com"
_PREVIEW_PATH = "/previews/{slug}/{slug}-logo-footylogos-320.webp"
_COUNTRY_PAGE_PATH = "/fr/country/{country_slug}"

# UA navigateur obligatoire : HEAD et l'UA par defaut python-urllib sont
# bloques par le CDN (faux negatifs constates manuellement).
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0


def crest_preview_url(slug: str) -> str:
    """URL image complete (webp) pour un slug footylogos donne."""
    return f"{ASSETS_BASE}{_PREVIEW_PATH.format(slug=slug)}"


class FootyLogosClient:
    """Client HTTP footylogos — GET + UA navigateur, retry borne sur 429,
    concurrence limitee (le CDN renvoie 429 sur les rafales).

    Usage reel :
        async with FootyLogosClient() as client:
            ok = await client.image_exists("real-madrid")

    En test, un `httpx.AsyncClient` deja construit (transport mocke) peut
    etre injecte via `http_client=` — aucun appel reseau reel.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    async def __aenter__(self) -> FootyLogosClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get(self, url: str) -> httpx.Response | None:
        """GET avec UA navigateur, retente sur 429 et sur erreur reseau,
        jusqu'a `max_retries` tentatives. Rend `None` seulement si toutes
        les tentatives echouent reseau (jamais lors d'un simple statut non
        200, que l'appelant doit distinguer pour son propre rapport)."""
        if self._client is None:
            raise RuntimeError("Use FootyLogosClient as an async context manager")

        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.get(url, headers={"User-Agent": _USER_AGENT})
            except httpx.HTTPError as exc:
                if attempt >= self._max_retries:
                    logger.warning(
                        "footylogos: echec reseau sur %s apres %d tentative(s): %s",
                        url, attempt, exc,
                    )
                    return None
                await asyncio.sleep(self._retry_delay)
                continue

            if response.status_code == 429 and attempt < self._max_retries:
                logger.info(
                    "footylogos: 429 sur %s, nouvelle tentative (%d/%d)",
                    url, attempt, self._max_retries,
                )
                await asyncio.sleep(self._retry_delay)
                continue

            return response

    async def image_exists(self, slug: str) -> bool:
        """Verifie par un GET (jamais HEAD) que l'image preview du slug
        candidat rend 200 — seule base valable pour poser `crest_url`."""
        async with self._semaphore:
            response = await self._get(crest_preview_url(slug))
        return response is not None and response.status_code == 200

    async def fetch_country_page(self, country_slug: str) -> str | None:
        """HTML de la page pays footylogos, ou `None` si non-200/erreur."""
        async with self._semaphore:
            url = f"{WWW_BASE}{_COUNTRY_PAGE_PATH.format(country_slug=country_slug)}"
            response = await self._get(url)
        if response is not None and response.status_code == 200:
            return response.text
        return None


# ---------------------------------------------------------------------------
# Slugification + generation de candidats
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """NFKD -> ASCII, `&` -> `and`, non-alphanumerique -> `-`, minuscules,
    tirets superflus retires."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_only = ascii_only.replace("&", "and")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_only).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)


# Affixes de tete frequents dans nos noms de club mais absents du slug
# footylogos (ex: "FC Koln" -> "fc-koln" existe deja via le seed, mais
# "AFC Bournemouth" ou similaire tente aussi la forme sans affixe).
_LEADING_AFFIXES = {"fc", "fk", "ac", "as", "sc", "cf", "ud", "sd", "rc", "cd"}
_SUFFIXES = ("fc", "cf", "united", "city", "town")


def _variant_slugs(base_slug: str) -> list[str]:
    words = base_slug.split("-")
    variants: list[str] = []
    if len(words) > 1 and words[0] in _LEADING_AFFIXES:
        variants.append("-".join(words[1:]))
    for suffix in _SUFFIXES:
        variants.append(f"{base_slug}-{suffix}")
    variants.append(f"afc-{base_slug}")
    return variants


# Dictionnaire FR->EN seed : nos noms francises (canonical_teams.name_fr)
# qui ne matchent pas le slug footylogos par simple slugification. Cle
# normalisee (minuscules, sans accent) sur name_fr OU name_en. Completer
# au besoin — n'ajouter une entree que pour un cas reel constate, pas par
# anticipation.
FR_EN_SEED: dict[str, str] = {
    "barcelone": "fc-barcelona",
    "seville": "sevilla",
    "valence": "valencia",
    "betis": "real-betis",
    "espanyol": "rcd-espanyol",
    "naples": "ssc-napoli",
    "cologne": "fc-koln",
    "francfort": "eintracht-frankfurt",
    "munich": "bayern-munich",
    "bayern munich": "bayern-munich",
    "milan ac": "ac-milan",
    "ac milan": "ac-milan",
}


def _normalize_name_key(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_only.strip().lower().split())


def _seed_slug(name: str | None) -> str | None:
    if not name:
        return None
    return FR_EN_SEED.get(_normalize_name_key(name))


def candidate_slugs(team: CanonicalTeam) -> list[str]:
    """Liste ordonnee et dedupliquee de slugs a essayer pour `team`.

    Ordre : seed FR->EN -> slug direct (name_en puis name_fr si different)
    -> variantes (affixe retire / suffixes ajoutes / prefixe afc-). Aucun
    appel reseau ici — la verification 200 est a la charge de l'appelant.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(slug: str | None) -> None:
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)

    _add(_seed_slug(team.name_fr))
    _add(_seed_slug(team.name_en))

    primary_name = team.name_en or team.name_fr
    primary_slug = slugify(primary_name)
    _add(primary_slug)

    if team.name_fr and team.name_fr != primary_name:
        _add(slugify(team.name_fr))

    for variant in _variant_slugs(primary_slug):
        _add(variant)

    return ordered


# ---------------------------------------------------------------------------
# Repli page pays
# ---------------------------------------------------------------------------

# leagues.country (registre interne, anglais) -> slug pays footylogos.
_COUNTRY_SLUGS: dict[str, str] = {
    "England": "england",
    "Spain": "spain",
    "Italy": "italy",
    "Germany": "germany",
    "France": "france",
    "Portugal": "portugal",
    "Netherlands": "netherlands",
    "Turkey": "turkey",
    "Belgium": "belgium",
    "Switzerland": "switzerland",
    "Scotland": "scotland",
    "Greece": "greece",
    "Poland": "poland",
    "Romania": "romania",
    "Norway": "norway",
    "Sweden": "sweden",
    "Denmark": "denmark",
    "Czechia": "czechia",
    "Bulgaria": "bulgaria",
    "Finland": "finland",
}

# Repli quand le slug pays primaire rend une page non-200.
_COUNTRY_SLUG_FALLBACKS: dict[str, list[str]] = {
    "Czechia": ["czech-republic"],
}


def _country_slug_candidates(country: str) -> list[str]:
    primary = _COUNTRY_SLUGS.get(country) or slugify(country)
    return [primary, *_COUNTRY_SLUG_FALLBACKS.get(country, [])]


_LOGO_LINK_RE = re.compile(r"/(?:fr/)?logos/([a-z0-9-]+)")


def extract_country_slugs(html: str) -> list[str]:
    """Slugs de club disponibles sur une page pays footylogos, extraits des
    liens `/fr/logos/{slug}` ou `/logos/{slug}`."""
    return sorted({m.group(1) for m in _LOGO_LINK_RE.finditer(html)})


# Seuil et marge du repli page-pays : des homonymes INTRA-pays (meme
# registre "pays" donc non ecartes par la restriction au pays) peuvent
# depasser un seuil permissif — ex. mesure reelle :
#   manchester-city vs manchester-united = 0.81
#   real-madrid     vs atletico-madrid   = 0.62
#   ac-milan        vs inter-milan       = 0.63
#   sporting        vs sporting-gijon    = 0.73
# Un seuil haut (0.82) ecarte deja la plupart ; la marge (0.08) ecarte en
# plus les cas ambigus ou deux candidats sont proches sans vrai gagnant.
# En cas de doute : on ne pose rien (None), jamais un mauvais ecusson.
_MATCH_THRESHOLD = 0.82
_MATCH_MIN_MARGIN = 0.08


def _normalize_for_match(slug: str) -> str:
    """Forme normalisee pour la comparaison floue : NFKD -> ASCII,
    minuscules, tirets/underscores -> espace simple. Appliquee aux DEUX
    cotes (slug cible et slug candidat) avant `SequenceMatcher`, pour que
    la similarite ne depende pas d'une difference de ponctuation plutot
    que de nom."""
    nfkd = unicodedata.normalize("NFKD", slug)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[-_]+", " ", ascii_only.lower()).strip()


def _best_match(
    target_slug: str,
    available: list[str],
    *,
    threshold: float = _MATCH_THRESHOLD,
    min_margin: float = _MATCH_MIN_MARGIN,
) -> str | None:
    """Meilleur candidat de `available` pour `target_slug`, ou `None` si le
    doute n'est pas leve. Conservateur par construction :

    - le meilleur score doit atteindre `threshold` (0.82) — un slug "assez
      proche" ne suffit pas, il doit etre TRES proche ;
    - il doit aussi depasser le 2e meilleur score d'au moins `min_margin`
      (0.08) — deux candidats proches sans vrai gagnant (homonymes
      intra-pays type manchester-city/manchester-united) sont rejetes
      meme si le meilleur depasse le seuil.

    Les deux cotes sont normalises de la meme facon (`_normalize_for_match`)
    avant `SequenceMatcher`, qui reste la mesure de similarite."""
    if not available:
        return None

    target_norm = _normalize_for_match(target_slug)
    scored = sorted(
        (
            (SequenceMatcher(None, target_norm, _normalize_for_match(candidate)).ratio(), candidate)
            for candidate in available
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    best_ratio, best_slug = scored[0]
    if best_ratio < threshold:
        return None

    second_ratio = scored[1][0] if len(scored) > 1 else 0.0
    if best_ratio - second_ratio < min_margin:
        return None

    return best_slug


async def _fetch_country_available_slugs(country: str, client: FootyLogosClient) -> list[str] | None:
    """Slugs de club disponibles pour `country`, en essayant le slug pays
    primaire puis ses eventuels repli (`_COUNTRY_SLUG_FALLBACKS`). Pas de
    cache ici — cf. `_get_country_available_slugs` pour le cache par run."""
    for country_slug in _country_slug_candidates(country):
        html = await client.fetch_country_page(country_slug)
        if html is None:
            continue
        available = extract_country_slugs(html)
        if available:
            return available
    return None


async def _get_country_available_slugs(
    country: str,
    client: FootyLogosClient,
    cache: dict[str, asyncio.Task[list[str] | None]],
) -> list[str] | None:
    """Comme `_fetch_country_available_slugs`, mais memoise par pays dans
    `cache` pour la duree d'un run de `resolve_crests` : plusieurs clubs du
    meme pays tombant sur le repli ne (re)telechargent la page pays qu'une
    seule fois. `cache` stocke des `Task` (pas des valeurs) pour que des
    appels concurrents (le run traite les clubs via `asyncio.gather`) sur
    le meme pays attendent la meme requete en vol plutot que d'en lancer
    une deuxieme."""
    task = cache.get(country)
    if task is None:
        task = asyncio.ensure_future(_fetch_country_available_slugs(country, client))
        cache[country] = task
    return await task


async def _resolve_team_slug(
    team: CanonicalTeam,
    client: FootyLogosClient,
    country_cache: dict[str, asyncio.Task[list[str] | None]],
) -> tuple[str | None, str | None]:
    """Rend `(slug, methode)` ou `(None, None)` si aucun candidat ne rend
    une image 200. `methode` in {"slug", "country_page"} — jamais autre
    chose qu'un slug deja verifie 200 a cet instant."""
    for slug in candidate_slugs(team):
        if await client.image_exists(slug):
            return slug, "slug"

    if team.country:
        target_slug = slugify(team.name_en or team.name_fr)
        available = await _get_country_available_slugs(team.country, client, country_cache)
        if available:
            best = _best_match(target_slug, available)
            if best and await client.image_exists(best):
                return best, "country_page"

    return None, None


async def _resolve_one_team(
    team: CanonicalTeam,
    client: FootyLogosClient,
    country_cache: dict[str, asyncio.Task[list[str] | None]],
) -> tuple[str | None, str | None]:
    try:
        return await _resolve_team_slug(team, client, country_cache)
    except Exception as exc:  # jamais un bug/echec sur un club ne bloque les autres
        logger.error("footylogos: erreur inattendue pour %r: %s", team.name_fr, exc)
        return None, None


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------


@dataclass
class UnresolvedTeam:
    """Un club canonique laisse NULL, avec son pays pour cibler la revue."""

    canonical_id: int
    name: str
    country: str | None = None


@dataclass
class CrestReport:
    """Bilan d'un passage de resolution d'ecussons.

    `resolved` : pose via un slug direct/variante/seed (verifie 200).
    `resolved_country_page` : pose via le repli page pays + match flou
    (verifie 200 egalement) — range a part pour cibler la revue manuelle,
    ce chemin etant moins direct que le slug.
    `not_found` : aucun candidat n'a rendu 200 — `crest_url` reste NULL.
    `by_country` : `{pays: {"resolved": n, "not_found": n}}`.
    """

    resolved: list[str] = field(default_factory=list)
    resolved_country_page: list[str] = field(default_factory=list)
    not_found: list[UnresolvedTeam] = field(default_factory=list)
    by_country: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved) + len(self.resolved_country_page)

    def _tally(self, country: str | None, *, resolved: bool) -> None:
        bucket = self.by_country.setdefault(country or "Inconnu", {"resolved": 0, "not_found": 0})
        bucket["resolved" if resolved else "not_found"] += 1


async def resolve_crests(
    session: AsyncSession,
    client: FootyLogosClient | None = None,
    *,
    dry_run: bool = False,
    only_missing: bool = True,
) -> CrestReport:
    """Resout `crest_url` pour les clubs canoniques engages.

    Perimetre : `CanonicalTeam.league_api_id IS NOT NULL` ET, si
    `only_missing` (par defaut), `crest_url IS NULL` — idempotent, ne
    retouche jamais un ecusson deja pose (correction manuelle = la voie,
    comme pour `api_football_id`). `only_missing=False` retraite aussi les
    clubs deja resolus (utile apres un fix de l'algorithme).

    `client` optionnel : si omis, un `FootyLogosClient` reel (reseau) est
    construit et ferme automatiquement. En test, injecter un client dont
    le `httpx.AsyncClient` sous-jacent a un transport mocke — aucun reseau
    reel necessaire pour couvrir toute la logique.

    GARDE-FOU : `team.crest_url` n'est assigne QUE pour un slug dont
    `client.image_exists` a rendu `True` (verification GET 200, UA
    navigateur) — jamais sur la seule plausibilite d'un slug devine.

    `dry_run=True` pose les `crest_url` sur les objets ORM en memoire
    (utile pour inspecter le rapport) mais ne commit jamais.
    """
    report = CrestReport()

    conditions = [CanonicalTeam.league_api_id.is_not(None)]
    if only_missing:
        conditions.append(CanonicalTeam.crest_url.is_(None))

    teams = (
        await session.execute(select(CanonicalTeam).where(*conditions))
    ).scalars().all()

    owns_client = client is None
    if owns_client:
        client = FootyLogosClient()
        await client.__aenter__()

    try:
        country_cache: dict[str, asyncio.Task[list[str] | None]] = {}
        results = await asyncio.gather(
            *[_resolve_one_team(team, client, country_cache) for team in teams]
        )
        for team, (slug, method) in zip(teams, results, strict=True):
            if slug:
                team.crest_url = crest_preview_url(slug)
                if method == "country_page":
                    report.resolved_country_page.append(team.name_fr)
                else:
                    report.resolved.append(team.name_fr)
                report._tally(team.country, resolved=True)
                logger.info("footylogos: %r -> %s (%s)", team.name_fr, slug, method)
            else:
                report.not_found.append(
                    UnresolvedTeam(canonical_id=team.id, name=team.name_fr, country=team.country)
                )
                report._tally(team.country, resolved=False)
                logger.warning(
                    "footylogos: aucun candidat valide (image 200) pour %r (pays=%r)",
                    team.name_fr, team.country,
                )
    finally:
        if owns_client:
            await client.__aexit__(None, None, None)

    if not dry_run:
        await session.commit()

    return report


# ---------------------------------------------------------------------------
# CLI — execution manuelle uniquement, jamais cablee dans worker.py
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolution des ecussons de clubs via footylogos.com (gratuit, sans cle)."
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="N'ecrit rien : affiche le rapport et s'arrete avant le commit.",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--only-missing",
        dest="only_missing",
        action="store_true",
        default=True,
        help="(defaut) Ne traite que les clubs sans crest_url.",
    )
    scope.add_argument(
        "--all",
        dest="only_missing",
        action="store_false",
        help="Retraite aussi les clubs ayant deja un crest_url.",
    )
    return parser.parse_args(argv)


def _print_report(report: CrestReport) -> None:
    print()
    print(
        f"Ecussons resolus : {report.resolved_count} "
        f"(slug: {len(report.resolved)}, page pays: {len(report.resolved_country_page)})"
    )
    print(f"Non resolus       : {len(report.not_found)}")
    print()
    print(f"{'Pays':<20} {'Resolus':>8} {'Non resolus':>12}")
    for country in sorted(report.by_country):
        bucket = report.by_country[country]
        print(f"{country:<20} {bucket['resolved']:>8} {bucket['not_found']:>12}")
    if report.not_found:
        print()
        print("Clubs non resolus :")
        for team in report.not_found:
            print(f"  - {team.name} (id={team.canonical_id}, pays={team.country})")
    print()


async def _main(argv: list[str] | None = None) -> None:
    from app.db import async_session

    args = _parse_args(argv)
    async with async_session() as session:
        report = await resolve_crests(session, dry_run=args.dry_run, only_missing=args.only_missing)
    _print_report(report)


if __name__ == "__main__":
    asyncio.run(_main())
