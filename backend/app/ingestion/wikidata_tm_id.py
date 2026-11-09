"""Jalon 3/5 (contournement rate-limit Transfermarkt) : resolveur Wikidata.

Deuxieme source de `tm_id` pour `player_tm_ids` (jalon 1), gratuite et hors
Transfermarkt : Wikidata expose `wdt:P2446` (identifiant joueur Transfermarkt)
pour ~221k entites, avec nom (`rdfs:label`) et date de naissance (`wdt:P569`).

Deux fonctions independantes :
  - `fetch_wikidata_players()` : SEUL appel reseau du module, pagine la
    requete SPARQL (voir sa docstring pour le pourquoi du LIMIT/OFFSET).
  - `match_and_store()` : AUCUN appel reseau, prend la liste Wikidata deja
    recuperee en parametre (injectee par l'appelant/le script) pour rester
    testable sans reseau ; indexe par (nom normalise, dob) et n'ecrit
    jamais sur une simple presomption (dob absente ou homonyme ambigu ->
    non retenu, jamais devine).
"""
from __future__ import annotations

import asyncio
import logging
import unicodedata
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzPlayer
from app.models.player_tm_id import PlayerTmId

logger = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "Ev0/1.0 (yohan.resin972@gmail.com)"

# Taille de page : au-dela, l'endpoint public Wikidata timeout plus souvent.
PAGE_SIZE = 20_000
TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS_PER_PAGE = 5
BASE_BACKOFF_SECONDS = 2.0

# Requete VALIDEE (voir cahier des charges) : ?p wdt:P2446 ?tmid (id joueur
# Transfermarkt) ; wdt:P569 ?dob (date de naissance) ; rdfs:label ?name en
# anglais. Un FILTER(CONTAINS(...)) sur le label forcerait un scan complet
# des 221k lignes et timeout -> on pagine plutot par LIMIT/OFFSET.
_SPARQL_QUERY_TEMPLATE = (
    "SELECT ?tmid ?name ?dob WHERE {{ "
    "?p wdt:P2446 ?tmid ; wdt:P569 ?dob ; rdfs:label ?name . "
    "FILTER(LANG(?name)='en') }} "
    "LIMIT {limit} OFFSET {offset}"
)


def normalize_name(name: str) -> str:
    """Nom normalise pour le matching : NFKD, diacritiques retires
    (categorie Unicode 'Mn'), casefold, espaces reduits. Fait matcher
    "Zlatko Tripic" (Wikidata) et "Zlatko Tripić" (bzz_players)."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(without_accents.casefold().split())


def _parse_dob(raw: str | None) -> str | None:
    """Le dob Wikidata est un datetime ISO ('1990-05-05T00:00:00Z'). On ne
    garde que les 10 premiers caracteres (la date), et on rejette toute
    valeur qui ne parse pas comme une date reelle plutot que de deviner
    (dob imprecise/malformee)."""
    if not raw or len(raw) < 10:
        return None
    candidate = raw[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _parse_binding(binding: dict) -> dict | None:
    """Une ligne de resultat SPARQL -> {tm_id, name, dob}, ou None si un
    champ manque/est invalide (jamais de crash, la page continue)."""
    try:
        tm_id = int(binding["tmid"]["value"])
    except (KeyError, ValueError, TypeError):
        return None
    name = binding.get("name", {}).get("value")
    if not name:
        return None
    dob = _parse_dob(binding.get("dob", {}).get("value"))
    if dob is None:
        return None
    return {"tm_id": tm_id, "name": name, "dob": dob}


async def _fetch_page(client: httpx.AsyncClient, offset: int) -> list[dict] | None:
    """Une page SPARQL (LIMIT/OFFSET), avec retry/backoff sur erreur
    reseau/429/5xx. Renvoie `None` (jamais ne leve) si la page reste
    inaccessible apres `MAX_ATTEMPTS_PER_PAGE` tentatives : l'appelant logge
    et arrete la pagination sans faire planter tout le lot deja collecte."""
    query = _SPARQL_QUERY_TEMPLATE.format(limit=PAGE_SIZE, offset=offset)
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS_PER_PAGE + 1):
        try:
            response = await client.get(
                SPARQL_ENDPOINT,
                params={"query": query, "format": "json"},
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/sparql-results+json",
                },
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"retryable status {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            payload = response.json()
            return payload["results"]["bindings"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "Wikidata page offset=%s tentative %s/%s echouee : %s",
                offset, attempt, MAX_ATTEMPTS_PER_PAGE, exc,
            )
            if attempt < MAX_ATTEMPTS_PER_PAGE:
                await asyncio.sleep(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    logger.error(
        "Wikidata page offset=%s abandonnee apres %s tentatives : %s",
        offset, MAX_ATTEMPTS_PER_PAGE, last_error,
    )
    return None


async def fetch_wikidata_players() -> list[dict]:
    """Recupere la totalite du jeu Wikidata (joueur Transfermarkt-id + dob,
    ~221k lignes) en paginant LIMIT/OFFSET jusqu'a une page vide. Jamais de
    crash du lot sur une page : une page qui echoue apres retry/backoff est
    loggee et arrete la pagination, en renvoyant ce qui a deja ete collecte
    plutot que de laisser planter l'appelant."""
    rows: list[dict] = []
    offset = 0
    async with httpx.AsyncClient() as client:
        while True:
            bindings = await _fetch_page(client, offset)
            if bindings is None:
                logger.error(
                    "Pagination Wikidata interrompue a offset=%s (echecs repetes).", offset
                )
                break
            if not bindings:
                break
            for binding in bindings:
                row = _parse_binding(binding)
                if row is not None:
                    rows.append(row)
                else:
                    logger.debug("Ligne Wikidata ignoree (champ manquant/invalide) : %s", binding)
            if len(bindings) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    return rows


async def match_and_store(session: AsyncSession, wikidata_rows: list[dict]) -> dict[str, int]:
    """Apparie `bzz_players` aux lignes Wikidata par (nom normalise, date de
    naissance exacte) et persiste `player_tm_ids(source='wikidata')`.

    Aucun appel reseau ici (`wikidata_rows` est deja recupere par
    l'appelant) : c'est ce qui rend la fonction testable sans reseau.

    Regles dures (jamais de faux positif) :
      - `bzz_player.date_of_birth` absente -> non retenu
        (`skipped_no_dob`).
      - 0 candidat Wikidata de meme (nom normalise, dob) -> ignore
        silencieusement (aucun match, rien a ecrire).
      - >= 2 candidats Wikidata de meme (nom normalise, dob) -> ambigu,
        non retenu, jamais devine (`skipped_ambiguous`).
      - exactement 1 candidat -> upsert `player_tm_ids` source='wikidata',
        SAUF si une ligne `source='squad'` existe deja pour ce joueur
        (squad = exact, prime toujours sur wikidata) -> alors non ecrase
        (`skipped_squad_exists`).
    """
    index: dict[tuple[str, str], list[dict]] = {}
    for row in wikidata_rows:
        key = (normalize_name(row["name"]), row["dob"])
        index.setdefault(key, []).append(row)

    players = (await session.execute(select(BzzPlayer))).scalars().all()
    existing_rows = (await session.execute(select(PlayerTmId))).scalars().all()
    existing_by_player = {row.player_api_id: row for row in existing_rows}

    stats = {
        "matched": 0,
        "skipped_no_dob": 0,
        "skipped_ambiguous": 0,
        "skipped_squad_exists": 0,
    }

    for player in players:
        if player.date_of_birth is None:
            stats["skipped_no_dob"] += 1
            continue

        key = (normalize_name(player.name), player.date_of_birth.isoformat())
        candidates = index.get(key, [])

        if len(candidates) == 0:
            continue
        if len(candidates) >= 2:
            stats["skipped_ambiguous"] += 1
            logger.info(
                "Wikidata ambigu pour bzz_player api_id=%s (%s candidats, nom=%r dob=%s) : non retenu.",
                player.api_id, len(candidates), player.name, player.date_of_birth,
            )
            continue

        tm_id = candidates[0]["tm_id"]
        existing = existing_by_player.get(player.api_id)
        if existing is not None and existing.source == "squad":
            stats["skipped_squad_exists"] += 1
            logger.info(
                "player_tm_ids deja source='squad' pour api_id=%s : wikidata (tm_id=%s) non ecrase.",
                player.api_id, tm_id,
            )
            continue

        if existing is not None:
            existing.tm_id = tm_id
            existing.source = "wikidata"
        else:
            new_row = PlayerTmId(player_api_id=player.api_id, tm_id=tm_id, source="wikidata")
            session.add(new_row)
            existing_by_player[player.api_id] = new_row
        stats["matched"] += 1

    await session.commit()
    return stats
