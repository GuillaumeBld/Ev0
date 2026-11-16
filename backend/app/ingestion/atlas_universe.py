"""Univers Atlas complet : toutes les équipes, championnats, sélections et
joueurs dont on a les données, toutes sources confondues, sans doublon d'équipe.

Pourquoi : l'Atlas n'affichait que les clubs des compétitions suivies via
Bzzoiro. Or on dispose de bien plus : l'annuaire Bzzoiro complet (229 000
joueurs, leurs clubs et sélections) et le portail CSV Transfermarkt (78
championnats, ~1 400 clubs, ~59 000 joueurs avec carrière). Tout doit
apparaître, relié à son identifiant Bzzoiro quand il existe.

Une équipe = UNE ligne `canonical_teams`, qui porte ses identifiants de chaque
source (`bzz_team_id`, `transfermarkt_club_id`) :
- club Bzzoiro (identifiant de /api/events/ = `current_team.id` des joueurs)
  -> rattaché à la ligne qui porte déjà cet id, sinon à une ligne du même pays
  au même nom (clé `cle_club`), sinon créé ;
- club du portail (identifiant Transfermarkt) -> rattaché à la ligne qui porte
  déjà cet id TM, sinon au club Bzzoiro où jouent la majorité de ses joueurs
  déjà identifiés, sinon au même nom dans le même pays, sinon créé ;
- équipe connue d'une seule source hors Bzzoiro : identifiant négatif
  (`bzz_team_id = -tm_club_id`, joueurs `api_id = -tm_id`). Le signe négatif
  marque « hors Bzzoiro » : aucun job Bzzoiro ne les interroge.

Championnats : ceux du registre Bzzoiro (inchangés), ceux du portail inconnus
de Bzzoiro (ids `PORTAL_LEAGUE_BASE + n`), et pour chaque pays un « Autres
clubs » (ids `OTHER_LEAGUE_BASE + n`) qui accueille les clubs sans championnat
connu. Aucun n'entre dans `LEAGUE_REGISTRY` : les jobs Bzzoiro (événements,
segmentation stricte) ne les voient jamais.
"""
from __future__ import annotations

import csv
import io
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from typing import Any, Iterable

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.countries import COUNTRIES, country_iso
from app.ingestion.bzzoiro.league_registry import EXTERNAL_LEAGUE_BASE, LEAGUE_REGISTRY
from app.ingestion.bzzoiro.sync_players import build_player_values
from app.ingestion.transfermarkt.player_match import _names_compatible
from app.ingestion.transfermarkt.portal_careers import PORTAL_DATA_URL, PORTAL_INDEX_URL, list_league_codes
from app.ingestion.transfermarkt.text_utils import fold_accents
from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam
from app.models.leagues import League
from app.scripts.rebuild_team_registry import cle_club

logger = logging.getLogger(__name__)

PORTAL_LEAGUE_BASE = EXTERNAL_LEAGUE_BASE
OTHER_LEAGUE_BASE = 20000

# Code portail -> id du registre Bzzoiro quand c'est la même compétition.
PORTAL_TO_REGISTRY: dict[str, int] = {
    "gb1": 1, "es1": 3, "it1": 4, "l1": 5, "fr1": 6, "po1": 2, "nl1": 10, "tr1": 11,
    "be1": 14, "c1": 15, "sc1": 13, "gr1": 24, "pl1": 25, "ro1": 23, "no1": 54,
    "se1": 26, "dk1": 84, "ts1": 99, "bu1": 22, "fi1": 55, "gb2": 12, "es2": 38,
    "it2": 100, "l2": 94, "fr2": 89, "po2": 88, "it3a": 101, "it3b": 102, "it3c": 103,
    "bra1": 9, "sa1": 17, "mls1": 18, "mex1": 19, "jap1": 49, "rsk1": 50, "csl": 52,
    "mar1": 53, "col1": 80, "arg1": 85, "aus1": 104, "a1": 96, "slo1": 109,
    "gb3": 86, "fr3": 105, "bra2": 34, "clpd": 111, "ec1n": 112,
}

# Championnats du portail absents du registre Bzzoiro : (nom, pays anglais, niveau).
PORTAL_ONLY_LEAGUES: dict[str, tuple[str, str, int]] = {
    "sfa1": ("Premier Division", "South Africa", 1),
    "alg1": ("Ligue Professionnelle 1", "Algeria", 1),
    "egy1": ("Premier League", "Egypt", 1),
    "cdn1": ("Canadian Premier League", "Canada", 1),
    "pr1a": ("Primera División", "Paraguay", 1),
    "uru1": ("Primera División", "Uruguay", 1),
    "uae1": ("UAE Pro League", "United Arab Emirates", 1),
    "alb1": ("Kategoria Superiore", "Albania", 1),
    "az1": ("Premier League", "Azerbaijan", 1),
    "bos1": ("Premijer Liga", "Bosnia and Herzegovina", 1),
    "zyp1": ("First Division", "Cyprus", 1),
    "kr1": ("SuperSport HNL", "Croatia", 1),
    "est1": ("Meistriliiga", "Estonia", 1),
    "ge1n": ("Erovnuli Liga", "Georgia", 1),
    "ung1": ("Nemzeti Bajnokság I", "Hungary", 1),
    "ir1": ("Premier Division", "Ireland", 1),
    "nir1": ("NIFL Premiership", "Northern Ireland", 1),
    "is1": ("Besta deild", "Iceland", 1),
    "isr1": ("Ligat Ha'Al", "Israel", 1),
    "let1": ("Virslīga", "Latvia", 1),
    "li1": ("A Lyga", "Lithuania", 1),
    "lux1": ("National Division", "Luxembourg", 1),
    "mal1": ("Premier League", "Malta", 1),
    "wal1": ("Cymru Premier", "Wales", 1),
    "ru1": ("Premier Liga", "Russia", 1),
    "ser1": ("Super Liga", "Serbia", 1),
    "sl1": ("Prva Liga", "Slovenia", 1),
    "ukr1": ("Premier Liga", "Ukraine", 1),
    "l3": ("3. Liga", "Germany", 3),
    "e3g1": ("Primera Federación — Groupe I", "Spain", 3),
    "e3g2": ("Primera Federación — Groupe II", "Spain", 3),
}

_POSITIONS = {"goalkeeper": "G", "defender": "D", "midfield": "M", "attack": "F"}


def portal_league_id(code: str) -> int:
    """Id stable d'un championnat du portail inconnu de Bzzoiro."""
    return PORTAL_LEAGUE_BASE + 1 + sorted(PORTAL_ONLY_LEAGUES).index(code)


def other_league_id(iso: str) -> int:
    """Id stable du « Autres clubs » d'un pays (ISO2)."""
    return OTHER_LEAGUE_BASE + (ord(iso[0]) - 65) * 26 + (ord(iso[1]) - 65)


def is_external_league(league_api_id: int | None) -> bool:
    """Championnat hors registre Bzzoiro (portail ou « Autres clubs »)."""
    return league_api_id is not None and league_api_id >= PORTAL_LEAGUE_BASE


@dataclass
class UniverseReport:
    bzz_players_upserted: int = 0
    clubs_created: int = 0
    clubs_linked_bzz: int = 0
    nations_created: int = 0
    portal_clubs_linked: Counter = field(default_factory=Counter)
    leagues_created: int = 0
    clubs_placed_other: int = 0
    portal_players_linked: int = 0
    portal_players_created: int = 0
    countries_unknown: Counter = field(default_factory=Counter)


# ---------------------------------------------------------------------------
# Annuaires (fonctions pures, testées)
# ---------------------------------------------------------------------------

def build_directories(
    bzz_rows: Iterable[dict[str, Any]],
) -> tuple[dict[int, tuple[str, str | None]], dict[int, tuple[str, str | None]]]:
    """({club_id: (nom, pays)}, {sélection_id: (nom, pays)}) depuis les fiches
    joueurs Bzzoiro. Nom et pays retenus = les plus fréquents. Un id vu comme
    sélection n'est jamais un club."""
    clubs: dict[int, Counter] = defaultdict(Counter)
    nations: dict[int, Counter] = defaultdict(Counter)
    for row in bzz_rows:
        team = row.get("current_team") or {}
        if team.get("id"):
            clubs[team["id"]][(team.get("name") or "", team.get("country"))] += 1
        nat = row.get("national_team") or {}
        if nat.get("id"):
            nations[nat["id"]][(nat.get("name") or "", nat.get("country") or nat.get("name"))] += 1
    nation_dir = {tid: c.most_common(1)[0][0] for tid, c in nations.items()}
    club_dir = {tid: c.most_common(1)[0][0] for tid, c in clubs.items() if tid not in nation_dir}
    return club_dir, nation_dir


def vote_bzz_team(
    tm_player_ids: Iterable[int],
    tm_to_player: dict[int, int],
    player_team: dict[int, int | None],
    *,
    min_players: int = 3,
    min_share: float = 0.5,
) -> int | None:
    """Club Bzzoiro où joue la majorité des joueurs (déjà identifiés) d'un
    club Transfermarkt. None si trop peu de joueurs connus ou pas de majorité."""
    votes: Counter = Counter()
    known = 0
    for tm_id in tm_player_ids:
        pid = tm_to_player.get(tm_id)
        if pid is None:
            continue
        known += 1
        team = player_team.get(pid)
        if team:
            votes[team] += 1
    if known < min_players or not votes:
        return None
    team, n = votes.most_common(1)[0]
    return team if n >= min_players and n / known >= min_share else None


def age_at(dob: date | None, today: date) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class _Teams:
    """Index des lignes canoniques par identifiant et par (pays, clé de nom)."""

    def __init__(self, rows: list[CanonicalTeam]) -> None:
        self.rows = rows
        self.by_bzz = {r.bzz_team_id: r for r in rows if r.bzz_team_id is not None}
        self.by_tm = {r.transfermarkt_club_id: r for r in rows if r.transfermarkt_club_id is not None}
        self.names = {r.name_fr for r in rows}
        self.by_key: dict[tuple[str | None, str], list[CanonicalTeam]] = defaultdict(list)
        for r in rows:
            self._index(r)

    def _index(self, r: CanonicalTeam) -> None:
        iso = country_iso(r.country)
        for label in {r.name_fr, r.name_en, *(r.aliases or [])}:
            key = cle_club(label)
            if key and r not in self.by_key[(iso, key)]:
                self.by_key[(iso, key)].append(r)

    def find_by_name(self, name: str, country: str | None, *, free: str) -> CanonicalTeam | None:
        """Ligne unique du même pays au même nom, dont l'identifiant `free`
        ('bzz' ou 'tm') n'est pas déjà pris par une autre source."""
        cands = self.by_key.get((country_iso(country), cle_club(name)), [])
        if free == "bzz":
            cands = [c for c in cands if c.bzz_team_id is None or c.bzz_team_id < 0]
        else:
            cands = [c for c in cands if c.transfermarkt_club_id is None]
        return cands[0] if len(cands) == 1 else None

    def unique_name(self, name: str, country: str | None, suffix_id: int) -> str:
        label = name
        if label in self.names:
            label = f"{name} ({country or '?'})"
        if label in self.names:
            label = f"{name} #{suffix_id}"
        self.names.add(label)
        return label

    def add(self, session: AsyncSession, **kw: Any) -> CanonicalTeam:
        row = CanonicalTeam(**kw)
        session.add(row)
        self.rows.append(row)
        if row.bzz_team_id is not None:
            self.by_bzz[row.bzz_team_id] = row
        if row.transfermarkt_club_id is not None:
            self.by_tm[row.transfermarkt_club_id] = row
        self._index(row)
        return row


async def upsert_bzz_players(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Écrit toutes les fiches Bzzoiro (même règle que la synchro par club)."""
    now = datetime.now(UTC)
    values = [v for v in (build_player_values(r, now) for r in rows) if v is not None]
    # Dédoublonne par api_id (une fiche peut apparaître sur deux pages).
    values = list({v["api_id"]: v for v in values}.values())
    for i in range(0, len(values), 1000):
        chunk = values[i:i + 1000]
        ins = pg_insert(BzzPlayer).values(chunk)
        cols = [k for k in chunk[0] if k not in ("api_id", "internal_id")]
        # Une valeur absente de la source ne doit jamais effacer la valeur connue.
        table = BzzPlayer.__table__.c
        update_set = {k: func.coalesce(ins.excluded[k], table[k]) for k in cols}
        update_set["internal_id"] = func.coalesce(BzzPlayer.__table__.c.internal_id, ins.excluded.internal_id)
        await session.execute(ins.on_conflict_do_update(index_elements=["api_id"], set_=update_set))
        await session.commit()
    return len(values)


async def build_universe(
    session: AsyncSession,
    *,
    bzz_rows: list[dict[str, Any]],
    portal: dict[str, list[dict[str, str]]],
    season: str,
    today: date,
) -> UniverseReport:
    report = UniverseReport()

    # 1. Toutes les fiches joueurs Bzzoiro.
    if bzz_rows:
        report.bzz_players_upserted = await upsert_bzz_players(session, bzz_rows)
    club_dir, nation_dir = build_directories(bzz_rows)

    teams = _Teams(list((await session.execute(select(CanonicalTeam))).scalars().all()))

    # 2. Clubs et sélections Bzzoiro : une ligne par identifiant, sans doublon.
    for directory, is_nation in ((nation_dir, True), (club_dir, False)):
        for team_id, (name, country) in directory.items():
            if team_id in teams.by_bzz:
                row = teams.by_bzz[team_id]
                if row.country is None and country:
                    row.country = country
                continue
            row = teams.find_by_name(name, country, free="bzz")
            if row is not None:
                old = row.bzz_team_id
                teams.by_bzz.pop(old, None)
                row.bzz_team_id = team_id
                teams.by_bzz[team_id] = row
                if old is not None and old < 0:
                    await session.execute(
                        update(BzzPlayer).where(BzzPlayer.current_team_api_id == old)
                        .values(current_team_api_id=team_id)
                    )
                report.clubs_linked_bzz += 1
                continue
            teams.add(
                session, name_fr=teams.unique_name(name, country, team_id), name_en=name,
                bzz_team_id=team_id, country=country,
            )
            if is_nation:
                report.nations_created += 1
            else:
                report.clubs_created += 1
    await session.commit()
    nation_ids = set(nation_dir)

    # 3. Clubs du portail : identifiant TM, vote des joueurs, nom, ou création.
    tm_to_player = dict((await session.execute(text("SELECT tm_id, player_api_id FROM player_tm_ids"))).all())
    player_team = dict((await session.execute(select(BzzPlayer.api_id, BzzPlayer.current_team_api_id))).all())
    portal_club_rows: dict[int, CanonicalTeam] = {}
    leagues_needed: dict[int, tuple[str, str, int, str]] = {}
    for code, rows in portal.items():
        if code in PORTAL_TO_REGISTRY:
            league_id = None  # championnat du registre : géré par la segmentation Bzzoiro
        elif code in PORTAL_ONLY_LEAGUES:
            name, country, level = PORTAL_ONLY_LEAGUES[code]
            league_id = portal_league_id(code)
            leagues_needed[league_id] = (name, country, level, "portal")
        else:
            logger.warning("Univers Atlas : championnat du portail inconnu %r ignoré.", code)
            continue
        country = (
            PORTAL_ONLY_LEAGUES[code][1] if code in PORTAL_ONLY_LEAGUES
            else LEAGUE_REGISTRY[PORTAL_TO_REGISTRY[code]].country
        )
        by_club: dict[int, list[dict[str, str]]] = defaultdict(list)
        for r in rows:
            try:
                by_club[int(r["current_team_id"])].append(r)
            except (KeyError, ValueError):
                continue
        for tm_club_id, members in by_club.items():
            club_name = Counter(m["current_team_name"] for m in members).most_common(1)[0][0]
            row = teams.by_tm.get(tm_club_id)
            how = "tm_id"
            if row is None:
                team_id = vote_bzz_team(
                    (int(m["player_id"]) for m in members), tm_to_player, player_team
                )
                if team_id is not None and team_id in teams.by_bzz and teams.by_bzz[team_id].transfermarkt_club_id is None:
                    row, how = teams.by_bzz[team_id], "joueurs"
            if row is None:
                row = teams.find_by_name(club_name, country, free="tm")
                how = "nom"
            if row is None:
                row = teams.add(
                    session, name_fr=teams.unique_name(club_name, country, -tm_club_id),
                    name_en=club_name, bzz_team_id=-tm_club_id,
                    transfermarkt_club_id=tm_club_id, country=country,
                )
                how = "créé"
            elif row.transfermarkt_club_id is None and tm_club_id not in teams.by_tm:
                row.transfermarkt_club_id = tm_club_id
                teams.by_tm[tm_club_id] = row
            report.portal_clubs_linked[how] += 1
            portal_club_rows[tm_club_id] = row
            if league_id is not None and (row.league_api_id is None or is_external_league(row.league_api_id)):
                row.league_api_id = league_id
                row.season = season
                if country and row.country is None:
                    row.country = country
    await session.commit()

    # 4. « Autres clubs » : tout club sans championnat va sous son pays.
    for row in teams.rows:
        if row.league_api_id is not None or row.bzz_team_id in nation_ids:
            continue
        iso = country_iso(row.country)
        if iso is None:
            report.countries_unknown[row.country or "?"] += 1
            continue
        league_id = other_league_id(iso)
        leagues_needed.setdefault(league_id, ("Autres clubs", COUNTRIES[iso][0], 9, "other"))
        row.league_api_id = league_id
        row.season = season
        report.clubs_placed_other += 1
    await session.commit()

    # 5. Lignes `leagues` des championnats hors registre.
    existing = set((await session.execute(select(League.id).where(League.id >= PORTAL_LEAGUE_BASE))).scalars().all())
    for league_id, (name, country, level, kind) in sorted(leagues_needed.items()):
        if league_id in existing:
            continue
        session.add(League(
            id=league_id, name=name, country=country,
            tier=str(level), kind="domestic_1" if kind == "portal" and level == 1 else (
                "domestic_2" if kind == "portal" else "other"),
            format="round_robin" if kind == "portal" else "groups",
            expected_count=None, rank=league_id,
        ))
        report.leagues_created += 1
    await session.commit()

    # 6. Joueurs du portail : identifiant TM posé sur le joueur Bzzoiro
    #    correspondant, sinon joueur créé (hors Bzzoiro).
    tm_known = set(tm_to_player)
    by_team: dict[int, list[BzzPlayer]] = defaultdict(list)
    by_name: dict[str, list[BzzPlayer]] = defaultdict(list)
    for p in (await session.execute(select(BzzPlayer))).scalars().all():
        team = p.loan_team_api_id or p.current_team_api_id
        if team:
            by_team[team].append(p)
        by_name[fold_accents(p.name or "")].append(p)
    taken = set((await session.execute(text("SELECT player_api_id FROM player_tm_ids"))).scalars().all())

    new_links: list[dict[str, Any]] = []
    new_players: list[dict[str, Any]] = []
    moved_players: list[dict[str, Any]] = []
    for code, rows in portal.items():
        for r in rows:
            try:
                tm_id = int(r["player_id"])
                tm_club_id = int(r["current_team_id"])
            except (KeyError, ValueError):
                continue
            row = portal_club_rows.get(tm_club_id)
            if tm_id in tm_known:
                # Joueur hors Bzzoiro déjà créé : il suit son club (transferts).
                pid = tm_to_player.get(tm_id)
                if pid is not None and pid < 0 and row is not None:
                    moved_players.append({"pid": pid, "team": row.bzz_team_id})
                continue
            name = r.get("player_name") or ""
            age = int(r["current_age"]) if (r.get("current_age") or "").isdigit() else None
            match = None
            if row is not None and row.bzz_team_id and row.bzz_team_id > 0:
                cands = [
                    p for p in by_team.get(row.bzz_team_id, [])
                    if p.api_id not in taken and _names_compatible(name, p.name or "")
                    and (age is None or p.date_of_birth is None or abs(age_at(p.date_of_birth, today) - age) <= 1)
                ]
                match = cands[0] if len(cands) == 1 else None
            if match is None:
                cands = [
                    p for p in by_name.get(fold_accents(name), [])
                    if p.api_id not in taken and age is not None and p.date_of_birth is not None
                    and abs(age_at(p.date_of_birth, today) - age) <= 1
                    and country_iso(p.nationality) == country_iso(r.get("nationality"))
                ]
                match = cands[0] if len(cands) == 1 else None
            if match is not None:
                new_links.append({"player_api_id": match.api_id, "tm_id": tm_id, "source": "portal"})
                taken.add(match.api_id)
                tm_known.add(tm_id)
                report.portal_players_linked += 1
                continue
            team_id = row.bzz_team_id if row is not None else None
            new_players.append({
                "api_id": -tm_id, "internal_id": -tm_id, "name": name,
                "nationality": r.get("nationality") or None,
                "position": _POSITIONS.get((r.get("current_position") or "").lower()),
                "current_team_api_id": team_id,
                "jersey_number": int(r["shirt_number"]) if (r.get("shirt_number") or "").isdigit() else None,
            })
            new_links.append({"player_api_id": -tm_id, "tm_id": tm_id, "source": "portal"})
            tm_known.add(tm_id)
            report.portal_players_created += 1

    for i in range(0, len(new_players), 1000):
        chunk = new_players[i:i + 1000]
        ins = pg_insert(BzzPlayer).values(chunk)
        await session.execute(ins.on_conflict_do_update(
            index_elements=["api_id"],
            set_={k: ins.excluded[k] for k in ("name", "nationality", "position", "current_team_api_id", "jersey_number")},
        ))
    if moved_players:
        await session.execute(text(
            "UPDATE bzz_players SET current_team_api_id = :team "
            "WHERE api_id = :pid AND current_team_api_id IS DISTINCT FROM :team"
        ), moved_players)
    for i in range(0, len(new_links), 1000):
        chunk = new_links[i:i + 1000]
        await session.execute(text(
            "INSERT INTO player_tm_ids (player_api_id, tm_id, source) "
            "VALUES (:player_api_id, :tm_id, :source) ON CONFLICT (player_api_id) DO NOTHING"
        ), chunk)
    await session.commit()
    return report


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def fetch_portal_rosters(http: httpx.Client) -> dict[str, list[dict[str, str]]]:
    """{code: lignes *_summary.csv} pour chaque championnat du portail."""
    codes = list_league_codes(http.get(PORTAL_INDEX_URL).raise_for_status().text)
    out: dict[str, list[dict[str, str]]] = {}
    for code in codes:
        try:
            raw = http.get(f"{PORTAL_DATA_URL}/{code}_summary.csv").raise_for_status().text
        except httpx.HTTPError as exc:
            logger.warning("Univers Atlas : effectif portail %s illisible (%s).", code, exc)
            continue
        out[code] = list(csv.DictReader(io.StringIO(raw)))
    return out


# Champs lus par `build_player_values` et `build_directories` : on ne garde
# que ceux-là (230 000 fiches en mémoire).
_BZZ_FIELDS = (
    "id", "api_id", "name", "short_name", "date_of_birth", "nationality", "position",
    "height", "jersey_number", "market_value", "current_team", "national_team",
)


async def fetch_all_bzz_players(client: Any) -> list[dict[str, Any]]:
    """Toutes les fiches /api/players/ (pagination complète, ~2 300 pages)."""
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        data = await client.get_page("/api/players/", {"page": page})
        rows.extend({k: r.get(k) for k in _BZZ_FIELDS} for r in data.get("results") or [])
        if not data.get("next"):
            return rows
        page += 1
