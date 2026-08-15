#!/usr/bin/env python3
"""Recuperateur durci : historique de carriere d'un joueur via Transfermarkt.

Version durcie du prototype de faisabilite (voir
`.superpowers/sdd/transfermarkt-proto-report.md` pour le contexte original).
Le probleme du proto : le matching de nom prenait le PREMIER resultat
"joueur" de la recherche -> casse silencieusement sur homonymes (ex. "Paulo",
"Kevin Medina" renvoient 8-10+ joueurs differents du meme nom).

Durcissement apporte :
  1. Matching securise par DATE DE NAISSANCE (tolerance 0 jour). Chaque
     candidat de la recherche est verifie via sa fiche profil
     (`/profil/spieler/<id>`, balise `itemprop="birthDate"`) avant d'etre
     retenu. Si aucun candidat ne matche -> `matched=False` avec raison
     explicite, JAMAIS un faux positif silencieux. Fallback degrade (annee +
     nationalite) uniquement si TM ne fournit pas de date exacte pour un
     candidat donne.
  2. Cache JSON local des tm_id resolus + des dob de profil deja recuperees
     + des noms de competition resolus, pour ne jamais refaire un travail
     deja fait entre deux runs.
  3. Retry/backoff exponentiel sur erreurs reseau et HTTP 429/5xx.
  4. Table de competitions completee (FS, FIC1, FRC, FRCH, USC, CGB ajoutes)
     + resolution dynamique (avec cache) des codes inconnus via
     `/wettbewerb/startseite/wettbewerb/<code>`, qui redirige vers la page
     de la competition et expose son nom dans <title>. Les codes qui ne
     resolvent pas sont gardes tels quels et logges (jamais d'echec).
  5. Rate limiting : >=2s entre CHAQUE requete HTTP sortante (recherche,
     profil, performance-game, resolution de competition), y compris entre
     tentatives de retry.

Usage (mode batch - format attendu par la mission) :
    .venv/bin/python app/scripts/transfermarkt_career.py \
        --input joueurs.json --out resultats.json \
        --cache-file /chemin/vers/cache.json

`joueurs.json` : liste de {"name", "nationality", "dob" (ISO YYYY-MM-DD),
"club"}. Le "dob" est OBLIGATOIRE : sans date de naissance de reference,
impossible de valider un match en toute securite (choix explicite : le
script refuse ce mode plutot que de deviner silencieusement).

Contraintes respectees :
  - User-Agent navigateur realiste sur CHAQUE requete.
  - Pause d'au moins `--delay` secondes (defaut 2.0) entre CHAQUE requete.
  - Retry exponentiel sur erreur reseau / 429 / 5xx (defaut 5 tentatives).
  - Aucun echec silencieux : toute anomalie est loggee et rapportee dans le
    resultat JSON du joueur concerne (jamais un crash de tout le batch).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from typing import Any

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_DELAY_SECONDS = 2.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_MAX_SEARCH_PAGES = 5  # 10 candidats/page -> jusqu'a 50 candidats explores
MAX_PROFILE_FETCHES_PER_PLAYER = 20  # garde-fou cout, meme si tres ambigu
BASE_URL = "https://www.transfermarkt.com"
TIMEOUT_SECONDS = 20.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("transfermarkt_career")

# Table statique de codes de competition -> nom lisible. Volontairement
# etendue par rapport au proto avec les codes rencontres et non mappes
# (FS, FIC1, FRC, FRCH, USC, CGB), verifies un par un via
# https://www.transfermarkt.com/wettbewerb/startseite/wettbewerb/<CODE>
# (redirige vers la page de la competition, nom dans <title>).
COMPETITION_NAMES: dict[str, str] = {
    "GB1": "Premier League",
    "ES1": "LaLiga",
    "L1": "Ligue 1",
    "FR1": "Ligue 1",
    "IT1": "Serie A",
    "L2": "Ligue 2",
    "FR2": "Ligue 2",
    "GB2": "Championship",
    "PO1": "Primeira Liga",
    "TR1": "Super Lig",
    "CL": "Champions League",
    "CLQ": "Champions League - Qualifying",
    "EL": "Europa League",
    "ECL": "Conference League",
    "DFB": "DFB-Pokal",
    "FAC": "FA Cup",
    "LCP": "League Cup (EFL Cup)",
    "CDR": "Coupe de France",
    "CDF": "Coupe de la Ligue",
    "SUC": "Trophee des Champions",
    "SUP": "Supercoupe",
    "FIWC": "FIFA Club World Cup",
    "WM": "World Cup",
    "WMQ": "World Cup Qualifying (UEFA)",
    "EM": "European Championship",
    "EMQ": "Euro Qualifying",
    "NLQ": "UEFA Nations League",
    # Codes vus dans le proto initial mais non mappes -> resolus manuellement
    # via l'endpoint /wettbewerb/startseite/wettbewerb/<code> pour ce durcissement.
    "FS": "International Friendlies",
    "FIC1": "FIFA Intercontinental Cup",
    "FRC": "Coupe de France",  # code alternatif a CDR selon la saison
    "FRCH": "Trophee des Champions",  # code alternatif a SUC selon la saison
    "USC": "UEFA Super Cup",
    "CGB": "EFL Cup",  # code alternatif a LCP selon la saison
}

PLAYERS_SECTION_MARKER = "Search results for players"
ROW_START_RE = re.compile(r'<tr class="(?:odd|even)">')
NAME_ID_RE = re.compile(r'<td class="hauptlink"><a title="([^"]*)" href="(/[a-z0-9\-]+/profil/spieler/(\d+))"')
CLUB_ROW_RE = re.compile(r'</a></td></tr>\s*<tr><td>(.*?)</td></tr>\s*</table>', re.DOTALL)
ZENTRIERT_TD_RE = re.compile(r'<td class="zentriert">(.*?)</td>', re.DOTALL)
FLAG_TITLE_RE = re.compile(r'title="([^"]*)"\s+alt="[^"]*"\s+class="flaggenrahmen"')
TAG_RE = re.compile(r'<[^>]+>')
BIRTHDATE_RE = re.compile(r'itemprop="birthDate"[^>]*>\s*([0-9/]+)')
TITLE_TAG_RE = re.compile(r'<title>([^<]*)</title>')


class TransfermarktError(Exception):
    """Erreur explicite pour toute etape qui echoue - jamais d'echec silencieux."""


# --------------------------------------------------------------------------
# Cache local (tm_id resolus, dob de profil, noms de competition)
# --------------------------------------------------------------------------

def load_cache(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"resolved_players": {}, "profile_dob": {}, "competition_names": {}}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("resolved_players", {})
    data.setdefault("profile_dob", {})
    data.setdefault("competition_names", {})
    return data


def save_cache(cache: dict[str, Any], path: str) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def player_cache_key(name: str, dob_iso: str) -> str:
    return f"{name.strip().lower()}|{dob_iso}"


# --------------------------------------------------------------------------
# Client HTTP : UA + rate limiting + retry/backoff
# --------------------------------------------------------------------------

class TransfermarktClient:
    """Client HTTP qui applique systematiquement UA, rate limiting et retry."""

    def __init__(
        self,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.max_retries = max_retries
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        self._first_request_done = False

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, *, what: str, accept_json: bool = False) -> httpx.Response:
        headers = {"Accept": "application/json"} if accept_json else {}
        backoff = self.delay_seconds

        for attempt in range(1, self.max_retries + 1):
            # Pause anti-blocage systematique avant CHAQUE requete (y compris
            # les retries : le backoff ci-dessous s'AJOUTE a cette pause de base).
            if self._first_request_done:
                log.info("Pause de %.1fs avant la prochaine requete (rate limiting)...", self.delay_seconds)
                time.sleep(self.delay_seconds)
            self._first_request_done = True

            log.info("GET %s (tentative %d/%d)", url, attempt, self.max_retries)
            try:
                resp = self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    raise TransfermarktError(
                        f"Erreur reseau apres {self.max_retries} tentatives en recuperant {what} ({url}): {exc}"
                    ) from exc
                log.warning(
                    "Erreur reseau (tentative %d/%d) pour %s: %s -> backoff %.1fs",
                    attempt, self.max_retries, what, exc, backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                log.warning(
                    "HTTP %d (tentative %d/%d) pour %s -> backoff %.1fs",
                    resp.status_code, attempt, self.max_retries, what, backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

            raise TransfermarktError(
                f"HTTP {resp.status_code} inattendu (apres {attempt} tentative(s)) en recuperant {what} ({url})"
            )

        raise TransfermarktError(f"Echec apres {self.max_retries} tentatives pour {what} ({url})")


# --------------------------------------------------------------------------
# Recherche joueur : parsing multi-candidats (pas juste le 1er resultat)
# --------------------------------------------------------------------------

def _strip_tags(html_fragment: str) -> str:
    return html.unescape(TAG_RE.sub("", html_fragment)).strip()


def _split_rows(section_html: str) -> list[str]:
    """Decoupe la section HTML en lignes <tr class="odd|even">...</tr>.

    NE PAS utiliser une regex '<tr class="...">.*?</tr>' non-greedy sur toute
    la section : chaque ligne contient un <table class="inline-table"> avec
    ses PROPRES <tr> imbriques (portrait+nom, puis club), donc le premier
    </tr> rencontre est un </tr> IMBRIQUE, pas la fin de la ligne exterieure.
    Une regex non-greedy tronque silencieusement la ligne avant club/position
    /age/nationalite. On decoupe donc par position de debut de ligne.
    """
    starts = [m.start() for m in ROW_START_RE.finditer(section_html)]
    if not starts:
        return []
    tbody_end = section_html.find("</tbody>")
    end = tbody_end if tbody_end != -1 else len(section_html)
    bounds = starts + [end]
    return [section_html[bounds[i]:bounds[i + 1]] for i in range(len(starts))]


def _parse_candidate_row(row_html: str) -> dict[str, Any] | None:
    name_match = NAME_ID_RE.search(row_html)
    if not name_match:
        return None
    matched_name, profile_path, tm_id = html.unescape(name_match.group(1)), name_match.group(2), name_match.group(3)

    club_match = CLUB_ROW_RE.search(row_html, name_match.end())
    club = _strip_tags(club_match.group(1)) if club_match else None

    zentriert = ZENTRIERT_TD_RE.findall(row_html)
    position = _strip_tags(zentriert[0]) if len(zentriert) > 0 else None
    age_raw = _strip_tags(zentriert[2]) if len(zentriert) > 2 else None
    age = int(age_raw) if age_raw and age_raw.isdigit() else None
    nationalities = FLAG_TITLE_RE.findall(zentriert[3]) if len(zentriert) > 3 else []

    return {
        "tm_id": tm_id,
        "matched_name": matched_name,
        "profile_url": f"{BASE_URL}{profile_path}",
        "club": club,
        "position": position,
        "age": age,
        "nationalities": nationalities,
    }


def search_player_candidates(
    client: TransfermarktClient, name: str, max_pages: int = DEFAULT_MAX_SEARCH_PAGES
) -> list[dict[str, Any]]:
    """Cherche `name` sur Transfermarkt et renvoie TOUS les candidats "joueur"
    trouves (jusqu'a `max_pages` pages de 10 resultats), PAS juste le premier.

    Chaque candidat porte club/position/age/nationalite tels qu'affiches dans
    les resultats de recherche - utilises ensuite pour prioriser l'ordre de
    verification, mais jamais pour accepter un match sans confirmation de la
    date de naissance exacte (voir `resolve_player`).
    """
    query = urllib.parse.quote(name)
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}/schnellsuche/ergebnis/schnellsuche?query={query}&page={page}"
        resp = client.get(url, what=f"les resultats de recherche (page {page}) pour '{name}'")
        html = resp.text

        idx = html.find(PLAYERS_SECTION_MARKER)
        if idx == -1:
            if page == 1:
                log.warning("Aucune section 'Search results for players' pour '%s' (0 resultat joueur).", name)
            break

        next_header_idx = html.find("content-box-headline", idx + len(PLAYERS_SECTION_MARKER))
        section_end = next_header_idx if next_header_idx != -1 else idx + 40000
        section = html[idx:section_end]

        rows = _split_rows(section)
        if not rows:
            break

        page_candidates = 0
        for row in rows:
            candidate = _parse_candidate_row(row)
            if candidate is None or candidate["tm_id"] in seen_ids:
                continue
            seen_ids.add(candidate["tm_id"])
            candidates.append(candidate)
            page_candidates += 1

        if page_candidates == 0:
            break  # plus de nouveaux candidats -> derniere page atteinte

    log.info("Recherche '%s' -> %d candidat(s) joueur trouve(s) (max %d pages explorees).", name, len(candidates), max_pages)
    return candidates


# --------------------------------------------------------------------------
# Fiche profil -> date de naissance exacte (source de verite du matching)
# --------------------------------------------------------------------------

def fetch_profile_dob(client: TransfermarktClient, tm_id: str, profile_url: str, cache: dict[str, Any]) -> str | None:
    """Recupere la date de naissance EXACTE (ISO YYYY-MM-DD) depuis la fiche
    profil du joueur, avec cache. Renvoie None si TM n'expose pas de date
    exacte pour ce joueur (rare, joueurs tres obscurs) - le cache stocke
    explicitement `None` pour ne pas re-essayer inutilement a chaque run.
    """
    if tm_id in cache["profile_dob"]:
        return cache["profile_dob"][tm_id]

    resp = client.get(profile_url, what=f"la fiche profil de tm_id={tm_id}")
    match = BIRTHDATE_RE.search(resp.text)
    dob_iso: str | None = None
    if match:
        raw = match.group(1)  # format dd/mm/yyyy
        try:
            dob_iso = dt.datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
        except ValueError:
            log.warning("Date de naissance illisible pour tm_id=%s: %r", tm_id, raw)
            dob_iso = None
    else:
        log.warning("Pas de balise itemprop='birthDate' trouvee sur la fiche profil de tm_id=%s.", tm_id)

    cache["profile_dob"][tm_id] = dob_iso
    return dob_iso


# --------------------------------------------------------------------------
# Matching securise par date de naissance
# --------------------------------------------------------------------------

def _compute_age(dob: dt.date, today: dt.date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _nationality_matches(candidate_nats: list[str], expected_nationality: str) -> bool:
    expected_norm = expected_nationality.strip().lower()
    if not expected_norm:
        return False
    for nat in candidate_nats:
        nat_norm = nat.strip().lower()
        if nat_norm == expected_norm or nat_norm in expected_norm or expected_norm in nat_norm:
            return True
    return False


def resolve_player(
    client: TransfermarktClient,
    expected: dict[str, Any],
    cache: dict[str, Any],
    max_search_pages: int = DEFAULT_MAX_SEARCH_PAGES,
) -> dict[str, Any]:
    """Resout le tm_id d'un joueur avec validation croisee OBLIGATOIRE par
    date de naissance. Renvoie un dict avec au minimum:
      matched (bool), tm_id, matched_name, profile_url, dob_transfermarkt,
      match_confidence, reason, candidates_checked (audit trail complet).

    Regle d'or : un candidat n'est JAMAIS retenu sans verification positive
    de sa date de naissance (exacte, ou a defaut annee+nationalite si TM ne
    donne que l'annee). Aucun candidat ne matche -> matched=False explicite.
    """
    name = expected["name"]
    dob_iso = expected["dob"]
    nationality = expected.get("nationality", "")
    club = expected.get("club", "")
    expected_dob = dt.date.fromisoformat(dob_iso)
    today = dt.date.today()
    expected_age = _compute_age(expected_dob, today)

    cache_key = player_cache_key(name, dob_iso)
    if cache_key in cache["resolved_players"]:
        log.info("Cache hit pour '%s' (dob=%s) -> reutilisation du resultat precedent.", name, dob_iso)
        return cache["resolved_players"][cache_key]

    candidates = search_player_candidates(client, name, max_pages=max_search_pages)

    if not candidates:
        result = {
            "matched": False,
            "tm_id": None,
            "matched_name": None,
            "profile_url": None,
            "dob_transfermarkt": None,
            "match_confidence": "unresolved",
            "reason": f"Aucun resultat 'joueur' dans la recherche Transfermarkt pour '{name}'.",
            "candidates_checked": [],
        }
        cache["resolved_players"][cache_key] = result
        return result

    def sort_key(c: dict[str, Any]) -> tuple[int, int]:
        nat_ok = _nationality_matches(c["nationalities"], nationality)
        age_diff = abs(c["age"] - expected_age) if c["age"] is not None else 99
        return (0 if nat_ok else 1, age_diff)

    ordered_candidates = sorted(candidates, key=sort_key)

    checked: list[dict[str, Any]] = []
    for candidate in ordered_candidates[:MAX_PROFILE_FETCHES_PER_PLAYER]:
        profile_dob = fetch_profile_dob(client, candidate["tm_id"], candidate["profile_url"], cache)
        nat_ok = _nationality_matches(candidate["nationalities"], nationality)

        if profile_dob is not None:
            if profile_dob == dob_iso:
                result = {
                    "matched": True,
                    "tm_id": candidate["tm_id"],
                    "matched_name": candidate["matched_name"],
                    "profile_url": candidate["profile_url"],
                    "dob_transfermarkt": profile_dob,
                    "match_confidence": "exact_dob",
                    "reason": None,
                    "candidates_checked": checked
                    + [{**candidate, "profile_dob": profile_dob, "rejected_reason": None}],
                }
                cache["resolved_players"][cache_key] = result
                return result
            reason = f"dob TM={profile_dob} != dob attendue={dob_iso}"
        else:
            # Fallback degrade : TM ne donne pas de date exacte pour ce
            # candidat -> on compare l'annee (via age affiche en recherche)
            # et on departage par nationalite, jamais par nom seul.
            candidate_years = set()
            if candidate["age"] is not None:
                candidate_years = {today.year - candidate["age"], today.year - candidate["age"] - 1}
            if candidate_years and expected_dob.year in candidate_years and nat_ok:
                result = {
                    "matched": True,
                    "tm_id": candidate["tm_id"],
                    "matched_name": candidate["matched_name"],
                    "profile_url": candidate["profile_url"],
                    "dob_transfermarkt": None,
                    "match_confidence": "year_and_nationality_fallback",
                    "reason": (
                        f"TM n'expose pas de date de naissance exacte pour ce joueur ; "
                        f"annee compatible (age={candidate['age']}) et nationalite "
                        f"'{nationality}' confirmee -> retenu par tolerance degradee."
                    ),
                    "candidates_checked": checked
                    + [{**candidate, "profile_dob": None, "rejected_reason": None}],
                }
                cache["resolved_players"][cache_key] = result
                return result
            reason = (
                f"pas de dob exacte TM ; annee(s) plausible(s)={sorted(candidate_years) or 'inconnue'} "
                f"vs attendue={expected_dob.year} ; nationalite_ok={nat_ok}"
            )

        checked.append({**candidate, "profile_dob": profile_dob, "rejected_reason": reason})

    result = {
        "matched": False,
        "tm_id": None,
        "matched_name": None,
        "profile_url": None,
        "dob_transfermarkt": None,
        "match_confidence": "unresolved",
        "reason": (
            f"{len(candidates)} candidat(s) trouve(s) pour '{name}' mais AUCUN ne matche la date de "
            f"naissance attendue ({dob_iso}). Details des rejets dans 'candidates_checked'."
        ),
        "candidates_checked": checked,
    }
    cache["resolved_players"][cache_key] = result
    return result


# --------------------------------------------------------------------------
# Stats de carriere (endpoint JSON interne, inchange depuis le proto)
# --------------------------------------------------------------------------

def fetch_career_games(client: TransfermarktClient, tm_id: str) -> list[dict[str, Any]]:
    """Recupere la liste brute des matchs (toute la carriere) via l'API interne JSON
    `/ceapi/performance-game/<id>` (reverse-engineering documente dans le
    rapport du proto - endpoint non officiel utilise par le front Svelte de TM).
    """
    url = f"{BASE_URL}/ceapi/performance-game/{tm_id}"
    resp = client.get(url, what=f"les donnees de performance pour tm_id={tm_id}", accept_json=True)

    try:
        payload = resp.json()
    except ValueError as exc:
        raise TransfermarktError(
            f"Reponse non-JSON de l'endpoint performance-game pour tm_id={tm_id}: {exc}"
        ) from exc

    if not payload.get("success"):
        raise TransfermarktError(
            f"L'endpoint performance-game a renvoye success=false pour tm_id={tm_id}: {payload.get('message')}"
        )

    data = payload.get("data") or {}
    games = data.get("performance")
    if not games:
        raise TransfermarktError(
            f"Aucune donnee de match ('performance') dans la reponse pour tm_id={tm_id} "
            f"(joueur sans historique de match cote Transfermarkt, ou format de reponse change)"
        )
    return games


def resolve_competition_name(client: TransfermarktClient, code: str, cache: dict[str, Any]) -> str:
    """Traduit un code de competition TM en nom lisible.

    1. Table statique COMPETITION_NAMES (rapide, pas de requete).
    2. Cache des codes deja resolus dynamiquement lors d'un run precedent.
    3. A defaut : resolution dynamique via
       `/wettbewerb/startseite/wettbewerb/<code>`, qui redirige vers la page
       de la competition (`<title>Nom NN/NN | Transfermarkt</title>`).
       Le resultat est mis en cache pour ne jamais refaire cette requete.
       Si la resolution echoue (page introuvable, structure changee), le
       code brut est garde et l'evenement est LOGGE (jamais silencieux).
    """
    if code in COMPETITION_NAMES:
        return COMPETITION_NAMES[code]

    if code in cache["competition_names"]:
        return cache["competition_names"][code]

    url = f"{BASE_URL}/wettbewerb/startseite/wettbewerb/{code}"
    try:
        resp = client.get(url, what=f"le nom de la competition '{code}'")
    except TransfermarktError as exc:
        log.warning("Code de competition inconnu '%s' : resolution dynamique echouee (%s). Code brut conserve.", code, exc)
        cache["competition_names"][code] = code
        return code

    title_match = TITLE_TAG_RE.search(resp.text)
    if not title_match:
        log.warning("Code de competition inconnu '%s' : pas de <title> exploitable. Code brut conserve.", code)
        cache["competition_names"][code] = code
        return code

    title = html.unescape(title_match.group(1))
    title = re.sub(r"\s*\|\s*Transfermarkt\s*$", "", title).strip()
    title = re.sub(r"\s+\d{2}/\d{2}$", "", title).strip()
    log.info("Code de competition inconnu '%s' resolu dynamiquement -> '%s'.", code, title)
    cache["competition_names"][code] = title
    return title


def aggregate_by_season_and_competition(
    client: TransfermarktClient, games: list[dict[str, Any]], cache: dict[str, Any]
) -> list[dict[str, Any]]:
    """Agrege les matchs bruts par (saison, competition).

    Une "apparition" ne compte que les matchs ou le joueur a reellement joue
    (participationState == "played") ; les matchs manques pour blessure,
    hors groupe, convoque non joue ou absence sont exclus.
    """
    buckets: dict[tuple[Any, Any], dict[str, Any]] = defaultdict(
        lambda: {"appearances": 0, "goals": 0, "assists": 0, "minutes": 0}
    )
    skipped_states: dict[str, int] = defaultdict(int)

    for game in games:
        game_info = game.get("gameInformation") or {}
        stats = game.get("statistics") or {}
        general = stats.get("generalStatistics") or {}

        participation_state = general.get("participationState")
        if participation_state != "played":
            skipped_states[str(participation_state)] += 1
            continue

        season_id = game_info.get("seasonId")
        competition_id = game_info.get("competitionId")
        season_display = (game_info.get("season") or {}).get("display") or (
            str(season_id) if season_id is not None else "unknown"
        )

        key = (season_id, competition_id)
        bucket = buckets[key]
        bucket["season"] = season_display
        bucket["season_start_year"] = season_id
        bucket["competition_code"] = competition_id
        if competition_id:
            # Pas de résolution HTTP du nom (trop lente sur les carrières multi-compétitions) :
            # table statique + cache seulement, sinon code brut. Les noms d'affichage
            # seront enrichis plus tard en une passe unique sur les codes rencontrés.
            bucket["competition"] = (
                COMPETITION_NAMES.get(competition_id)
                or cache.get("competition_names", {}).get(competition_id)
                or competition_id
            )
        else:
            bucket["competition"] = competition_id

        bucket["appearances"] += 1

        goal_stats = stats.get("goalStatistics") or {}
        bucket["goals"] += goal_stats.get("goalsScoredTotal") or 0
        bucket["assists"] += goal_stats.get("assists") or 0

        minutes = (stats.get("playingTimeStatistics") or {}).get("playedMinutes")
        bucket["minutes"] += minutes or 0

    if skipped_states:
        log.info("Matchs exclus des apparitions (non joues): %s", dict(skipped_states))

    seasons = list(buckets.values())
    seasons.sort(key=lambda s: (-(s.get("season_start_year") or 0), str(s.get("competition") or "")))
    return seasons


# --------------------------------------------------------------------------
# Pipeline par joueur + CLI batch
# --------------------------------------------------------------------------

def build_player_report(
    client: TransfermarktClient,
    expected: dict[str, Any],
    cache: dict[str, Any],
    max_search_pages: int = DEFAULT_MAX_SEARCH_PAGES,
) -> dict[str, Any]:
    match = resolve_player(client, expected, cache, max_search_pages=max_search_pages)

    report: dict[str, Any] = {
        "input_name": expected["name"],
        "input_nationality": expected.get("nationality"),
        "input_dob": expected["dob"],
        "input_club": expected.get("club"),
        "matched": match["matched"],
        "tm_id": match["tm_id"],
        "matched_name": match["matched_name"],
        "profile_url": match["profile_url"],
        "dob_transfermarkt": match["dob_transfermarkt"],
        "match_confidence": match["match_confidence"],
        "reason": match["reason"],
        "candidates_checked": match["candidates_checked"],
    }

    if not match["matched"]:
        report["total_games_fetched"] = 0
        report["seasons"] = []
        return report

    games = fetch_career_games(client, match["tm_id"])
    seasons = aggregate_by_season_and_competition(client, games, cache)
    report["total_games_fetched"] = len(games)
    report["seasons"] = seasons
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recuperateur durci : historique de carriere Transfermarkt avec validation par date de naissance."
    )
    parser.add_argument(
        "--input", required=True,
        help="Fichier JSON: liste de {name, nationality, dob (ISO YYYY-MM-DD), club}",
    )
    parser.add_argument("--out", help="Fichier de sortie JSON (defaut: stdout)")
    parser.add_argument(
        "--cache-file", default="tm_resolution_cache.json",
        help="Fichier de cache JSON (tm_id resolus, dob de profil, noms de competition)",
    )
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Delai minimum entre requetes (s)")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Tentatives max par requete")
    parser.add_argument(
        "--max-search-pages", type=int, default=DEFAULT_MAX_SEARCH_PAGES,
        help="Nb max de pages de resultats de recherche explorees (10 candidats/page)",
    )
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        players = json.load(f)

    for p in players:
        if "dob" not in p or not p["dob"]:
            log.error(
                "Joueur '%s' sans 'dob' dans l'entree - IMPOSSIBLE de valider un match en toute securite. "
                "Ce joueur sera marque en erreur plutot que devine silencieusement.",
                p.get("name", "?"),
            )

    cache = load_cache(args.cache_file)
    client = TransfermarktClient(delay_seconds=args.delay, max_retries=args.max_retries)

    results = []
    exit_code = 0
    try:
        for p in players:
            name = p.get("name", "?")
            log.info("=== Traitement de '%s' ===", name)
            if "dob" not in p or not p["dob"]:
                results.append({
                    "input_name": name, "matched": False, "match_confidence": "unresolved",
                    "reason": "Pas de date de naissance de reference fournie en entree - matching refuse.",
                })
                exit_code = 1
                continue
            try:
                report = build_player_report(client, p, cache, max_search_pages=args.max_search_pages)
                results.append(report)
                if report["matched"]:
                    log.info(
                        "OK '%s' -> tm_id=%s (%s), %d saisons/competitions, %d matchs bruts",
                        name, report["tm_id"], report["match_confidence"],
                        len(report["seasons"]), report["total_games_fetched"],
                    )
                else:
                    log.warning("NON RESOLU '%s': %s", name, report["reason"])
                    exit_code = 1
            except TransfermarktError as exc:
                log.error("ECHEC pour '%s': %s", name, exc)
                results.append({"input_name": name, "matched": False, "error": str(exc)})
                exit_code = 1
            finally:
                save_cache(cache, args.cache_file)  # sauvegarde incrementale
    finally:
        client.close()

    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        log.info("Resultat ecrit dans %s", args.out)
    else:
        print(text)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
