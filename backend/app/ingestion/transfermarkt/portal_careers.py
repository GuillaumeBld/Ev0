"""Import des carrières Transfermarkt depuis le portail CSV public d'un tiers.

Pourquoi : le portail « Football Career Data » (maloelpipo/FootballDashboard,
accord de son auteur le 13/11/2026) collecte chaque semaine, via le même
endpoint TM (`ceapi/performance-game`), la carrière des joueurs de 78
championnats et la publie en CSV. Comparé ligne à ligne à nos carrières
Ligue 1 : 99 % identiques (matchs joués, buts, passes, minutes). Notre
serveur, lui, est bridé par Transfermarkt (~1 400 joueurs/h, bloqué le
11/11) et ne rafraîchit jamais la saison en cours.

Rôle :
- joueur avec tm_id mais SANS carrière -> carrière complète importée ;
- joueur AVEC carrière -> seules les saisons récentes (saison TM courante et
  précédente) sont rafraîchies, sur les colonnes que le portail fournit.
Notre propre collecte (`fastpath_fill`, chemin lent) reste le secours pour
les joueurs absents du portail.

Fidélité au format maison (`aggregate_by_season_and_competition`) :
- une ligne par (saison, compétition), clubs additionnés ;
- seuls les matchs JOUÉS comptent (`matches_played`) ; une compétition sans
  match joué n'est pas écrite ;
- libellé de saison repris de notre base pour la même (compétition, saison)
  (« 25/26 » ou « 2026 » pour les championnats sur l'année civile), sinon
  déduit du format dominant de la compétition ;
- colonnes absentes du portail (titularisations, passes, duels, rouges...)
  -> NULL = inconnu, jamais 0 deviné.

Intégrité : chaque CSV est vérifié contre l'empreinte sha256 publiée dans le
`*_collection.json` du championnat ; un fichier qui ne correspond pas est
ignoré (jamais d'import partiel silencieux).
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.transfermarkt.resolve_clubs import current_tm_season
from app.models.player_career import PlayerCareerSeason
from app.scripts.import_career import _season_values, _upsert_season

logger = logging.getLogger(__name__)

PORTAL_INDEX_URL = "https://maloelpipo.github.io/FootballDashboard/"
PORTAL_DATA_URL = (
    "https://raw.githubusercontent.com/maloelpipo/FootballDashboard/main/"
    "artifacts/football-dashboard/live/data/tm_career"
)

_LEAGUE_RE = re.compile(r'href="data/([a-z0-9]+)_all\.zip"')
_SPLIT_LABEL_RE = re.compile(r"^\d{2}/\d{2}$")

# Colonnes rafraîchies sur une carrière existante : celles que le portail
# fournit. Les autres (passes, duels...) gardent la valeur de notre collecte.
REFRESH_COLUMNS = ("appearances", "goals", "assists", "minutes", "total_shots", "shots_on_target", "yellow_card")


@dataclass
class PortalImportReport:
    leagues_listed: int = 0
    leagues_read: int = 0
    leagues_failed: list[str] = field(default_factory=list)
    players_in_portal: int = 0
    players_known: int = 0
    careers_created: int = 0
    rows_created: int = 0
    players_refreshed: int = 0
    rows_refreshed: int = 0
    national_checked: int = 0


def list_league_codes(index_html: str) -> list[str]:
    """Codes championnat publiés par le portail (ordre stable, sans doublon)."""
    return list(dict.fromkeys(_LEAGUE_RE.findall(index_html)))


def verified_csv(content: bytes, expected_sha256: str | None) -> str | None:
    """Texte du CSV si son empreinte correspond à celle publiée, sinon None."""
    if not expected_sha256 or hashlib.sha256(content).hexdigest() != expected_sha256:
        return None
    return content.decode("utf-8")


def aggregate_career_rows(rows: Iterable[dict[str, str]]) -> dict[int, dict[tuple[int, str], dict[str, Any]]]:
    """{tm_id: {(saison TM, code compétition): totaux}} — clubs additionnés,
    matchs joués uniquement (même règle que notre agrégation maison)."""
    out: dict[int, dict[tuple[int, str], dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        try:
            tm_id = int(r["player_id"])
            season = int(r["season"])
            played = int(r["matches_played"] or 0)
        except (KeyError, TypeError, ValueError):
            continue
        comp = r.get("competition_id") or ""
        key = (season, comp)
        b = out[tm_id].get(key)
        if b is None:
            b = out[tm_id][key] = {
                "appearances": 0, "goals": 0, "assists": 0, "minutes": 0,
                "total_shots": 0, "shots_on_target": 0, "yellow_card": 0,
                "_national": [],
            }
        b["appearances"] += played
        b["goals"] += int(r.get("goals") or 0)
        b["assists"] += int(r.get("assists") or 0)
        b["minutes"] += int(r.get("minutes") or 0)
        b["total_shots"] += int(r.get("shots_total") or 0)
        b["shots_on_target"] += int(r.get("shots_on_goal") or 0)
        b["yellow_card"] += int(r.get("yellow_cards") or 0)
        if played:
            b["_national"].append(str(r.get("is_national_team")).lower() == "true")
    # Une compétition sans match joué n'existe pas dans notre format.
    for seasons in out.values():
        for key in [k for k, b in seasons.items() if b["appearances"] == 0]:
            del seasons[key]
    return {k: v for k, v in out.items() if v}


@dataclass
class SeasonLabels:
    """Libellés de saison et noms de compétition déjà connus de notre base."""

    exact: dict[tuple[str, int], str] = field(default_factory=dict)
    calendar: dict[str, bool] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)

    def label(self, comp: str, start: int) -> str:
        hit = self.exact.get((comp, start))
        if hit:
            return hit
        if self.calendar.get(comp):
            # Championnat sur l'année civile : saison TM 2025 = « 2026 ».
            return str(start + 1)
        return f"{start % 100:02d}/{(start + 1) % 100:02d}"


async def load_season_labels(session: AsyncSession) -> SeasonLabels:
    labels = SeasonLabels()
    rows = await session.execute(
        text(
            "SELECT competition_code, season_start_year, mode() WITHIN GROUP (ORDER BY season), "
            "mode() WITHIN GROUP (ORDER BY competition) "
            "FROM player_career_seasons WHERE season_start_year IS NOT NULL "
            "GROUP BY 1, 2"
        )
    )
    split_votes: dict[str, list[bool]] = defaultdict(list)
    for comp, start, season, name in rows:
        labels.exact[(comp, start)] = season
        split_votes[comp].append(bool(_SPLIT_LABEL_RE.match(season or "")))
        if name and comp and name != comp:
            labels.names.setdefault(comp, name)
    for comp, votes in split_votes.items():
        labels.calendar[comp] = sum(votes) < len(votes) / 2
    return labels


def _season_dict(comp: str, start: int, b: dict[str, Any], labels: SeasonLabels) -> dict[str, Any]:
    flags = b["_national"]
    return {
        "season": labels.label(comp, start),
        "season_start_year": start,
        "competition_code": comp,
        "competition": labels.names.get(comp) or comp or None,
        "appearances": b["appearances"],
        "goals": b["goals"],
        "assists": b["assists"],
        "minutes": b["minutes"],
        "total_shots": b["total_shots"],
        "shots_on_target": b["shots_on_target"],
        "yellow_card": b["yellow_card"],
        "is_national": bool(flags) and all(flags),
    }


async def _refresh_season(session: AsyncSession, values: dict[str, Any]) -> None:
    set_ = {k: values[k] for k in REFRESH_COLUMNS}
    set_["updated_at"] = func.now()
    stmt = pg_insert(PlayerCareerSeason).values(**values).on_conflict_do_update(
        index_elements=["player_api_id", "season", "competition_code"], set_=set_
    )
    await session.execute(stmt)


async def import_portal_league(
    session: AsyncSession,
    careers: dict[int, dict[tuple[int, str], dict[str, Any]]],
    *,
    tm_to_player: dict[int, int],
    with_career: set[int],
    labels: SeasonLabels,
    refresh_from_season: int,
    report: PortalImportReport,
) -> None:
    """Écrit les carrières d'un championnat. `with_career` est mis à jour au
    fil de l'eau (un joueur listé dans deux championnats n'est créé qu'une fois)."""
    report.players_in_portal += len(careers)
    national_fixes: list[dict[str, Any]] = []
    for tm_id, seasons in careers.items():
        player_api_id = tm_to_player.get(tm_id)
        if player_api_id is None:
            continue
        report.players_known += 1
        if player_api_id not in with_career:
            for (start, comp), b in seasons.items():
                await _upsert_season(session, _season_values(player_api_id, tm_id, _season_dict(comp, start, b, labels)))
                report.rows_created += 1
            with_career.add(player_api_id)
            report.careers_created += 1
            continue
        recent = [(k, b) for k, b in seasons.items() if k[0] >= refresh_from_season]
        for (start, comp), b in recent:
            await _refresh_season(session, _season_values(player_api_id, tm_id, _season_dict(comp, start, b, labels)))
            report.rows_refreshed += 1
        if recent:
            report.players_refreshed += 1
        for (start, comp), b in seasons.items():
            flags = b["_national"]
            national_fixes.append(
                {"pid": player_api_id, "start": start, "comp": comp, "nat": bool(flags) and all(flags)}
            )
    if national_fixes:
        # Étiquette Sélection/Club sur TOUTES les saisons des carrières
        # existantes : nos imports d'avant l'ajout de `is_national` l'ont
        # laissée à False (ex. qualifications Euro rangées en « Club »). Seule
        # cette colonne est touchée, et seulement si elle diffère.
        await session.execute(
            text(
                "UPDATE player_career_seasons SET is_national = :nat, updated_at = now() "
                "WHERE player_api_id = :pid AND season_start_year = :start "
                "AND competition_code = :comp AND is_national IS DISTINCT FROM :nat"
            ),
            national_fixes,
        )
        report.national_checked += len(national_fixes)
    await session.commit()


async def import_portal_careers(
    session: AsyncSession,
    http: httpx.Client,
    today: date,
    *,
    codes: list[str] | None = None,
) -> PortalImportReport:
    report = PortalImportReport()
    if codes is None:
        codes = list_league_codes(http.get(PORTAL_INDEX_URL).raise_for_status().text)
    report.leagues_listed = len(codes)

    tm_to_player = {
        tm: pid
        for pid, tm in (await session.execute(text("SELECT player_api_id, tm_id FROM player_tm_ids"))).all()
    }
    with_career = set(
        (await session.execute(select(PlayerCareerSeason.player_api_id).distinct())).scalars().all()
    )
    labels = await load_season_labels(session)
    refresh_from = current_tm_season(today) - 1

    for code in codes:
        try:
            meta = http.get(f"{PORTAL_DATA_URL}/{code}_collection.json").raise_for_status().json()
            sha = ((meta.get("last_success") or {}).get("sha256") or {}).get("career")
            raw = http.get(f"{PORTAL_DATA_URL}/{code}_career.csv").raise_for_status().content
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Portail carrières : %s illisible (%s).", code, exc)
            report.leagues_failed.append(code)
            continue
        content = verified_csv(raw, sha)
        if content is None:
            logger.warning("Portail carrières : %s ignoré (empreinte sha256 différente de celle publiée).", code)
            report.leagues_failed.append(code)
            continue
        careers = aggregate_career_rows(csv.DictReader(io.StringIO(content)))
        # Relu à chaque championnat : notre propre collecte tourne peut-être en
        # parallèle ; une carrière qu'elle vient de créer ne doit pas être
        # réécrite en entier (on perdrait ses colonnes passes/duels).
        with_career |= set(
            (await session.execute(select(PlayerCareerSeason.player_api_id).distinct())).scalars().all()
        )
        await import_portal_league(
            session, careers, tm_to_player=tm_to_player, with_career=with_career,
            labels=labels, refresh_from_season=refresh_from, report=report,
        )
        report.leagues_read += 1
        logger.info(
            "Portail carrières : %s lu (%d joueurs) — cumul %d carrières créées, %d joueurs rafraîchis.",
            code, len(careers), report.careers_created, report.players_refreshed,
        )
    return report
