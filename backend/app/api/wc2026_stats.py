"""WC2026 player rankings — stats cumulées depuis bzz_player_match_stats."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

router = APIRouter(prefix="/wc2026/stats", tags=["wc2026"])

KO_MATCHWEEKS = (6, 5, 27, 28, 29, 50)

_PLACEHOLDER_RE = re.compile(
    r"winner|loser|tbd|tba|qf\d*|sf\d*|semi.?final|quarter.?final|match\s*\d+"
    r"|^\d[A-Z]$|^[A-Z]\d$|^[WL]\d+$|/",
    re.IGNORECASE,
)


def _is_placeholder(name: str) -> bool:
    return not name or bool(_PLACEHOLDER_RE.search(name))


# ── Models pour cotes KO ──────────────────────────────────────────────────────

class BookmakerLine(BaseModel):
    bookmaker: str
    home: float | None
    draw: float | None
    away: float | None


class KOMatchH2H(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    matchweek: int | None
    kickoff_utc: str
    status: str
    home_score: int | None
    away_score: int | None
    lines: list[BookmakerLine]
    best_home: float | None
    best_draw: float | None
    best_away: float | None


@router.get("/ko-h2h", response_model=list[KOMatchH2H])
async def get_ko_h2h(session: AsyncSession = Depends(get_db)) -> list[KOMatchH2H]:
    """Dernières cotes h2h (home/draw/away) pour tous les matchs KO du CDM 2026."""
    rows = (
        await session.execute(
            text("""
                WITH latest AS (
                    SELECT DISTINCT ON (mos.fixture_id, mos.bookmaker, mos.outcome)
                        mos.fixture_id,
                        mos.bookmaker,
                        mos.outcome,
                        mos.odds
                    FROM match_odds_snapshots mos
                    WHERE mos.market_type = 'h2h'
                    ORDER BY mos.fixture_id, mos.bookmaker, mos.outcome, mos.snapshot_utc DESC
                )
                SELECT
                    f.id          AS fixture_id,
                    f.home_team,
                    f.away_team,
                    f.matchweek,
                    f.kickoff_utc,
                    f.status,
                    f.home_score,
                    f.away_score,
                    l.bookmaker,
                    MAX(CASE WHEN l.outcome = 'home' THEN l.odds END) AS home_odds,
                    MAX(CASE WHEN l.outcome = 'draw' THEN l.odds END) AS draw_odds,
                    MAX(CASE WHEN l.outcome = 'away' THEN l.odds END) AS away_odds
                FROM fixtures f
                LEFT JOIN latest l ON l.fixture_id = f.id
                WHERE f.league = 'world_cup_2026'
                  AND f.matchweek = ANY(:mws)
                GROUP BY f.id, f.home_team, f.away_team, f.matchweek,
                         f.kickoff_utc, f.status, f.home_score, f.away_score, l.bookmaker
                ORDER BY f.kickoff_utc, f.id, l.bookmaker
            """),
            {"mws": list(KO_MATCHWEEKS)},
        )
    ).mappings().all()

    # Regrouper par fixture
    from collections import OrderedDict
    fixtures_map: dict[int, dict] = OrderedDict()
    for r in rows:
        fid = r["fixture_id"]
        if fid not in fixtures_map:
            fixtures_map[fid] = {
                "fixture_id": fid,
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "matchweek": r["matchweek"],
                "kickoff_utc": str(r["kickoff_utc"]),
                "status": r["status"],
                "home_score": r["home_score"],
                "away_score": r["away_score"],
                "lines": [],
            }
        h = float(r["home_odds"]) if r["home_odds"] is not None else None
        d = float(r["draw_odds"]) if r["draw_odds"] is not None else None
        a = float(r["away_odds"]) if r["away_odds"] is not None else None
        if h is not None or d is not None or a is not None:
            fixtures_map[fid]["lines"].append(BookmakerLine(bookmaker=r["bookmaker"], home=h, draw=d, away=a))

    result: list[KOMatchH2H] = []
    for fdata in fixtures_map.values():
        lines = fdata["lines"]
        homes = [ln.home for ln in lines if ln.home is not None]
        draws = [ln.draw for ln in lines if ln.draw is not None]
        aways = [ln.away for ln in lines if ln.away is not None]
        result.append(KOMatchH2H(
            **{k: v for k, v in fdata.items() if k != "lines"},
            lines=lines,
            best_home=max(homes) if homes else None,
            best_draw=max(draws) if draws else None,
            best_away=max(aways) if aways else None,
        ))
    return result

WC_LEAGUE_ID = 27

# xG attendus pour l'ensemble du tournoi, par bookmaker (source externe).
# Clés = noms de nations tels qu'ils apparaissent dans wc2026_squad_players.
BM_XG: dict[str, float] = {
    "Espagne":            13.03,
    "Brésil":             12.33,
    "Allemagne":          11.78,
    "Angleterre":         11.74,
    "France":             10.90,
    "Argentine":          10.83,
    "Portugal":           10.23,
    "Belgique":            9.56,
    "Suisse":              8.27,
    "Pays-Bas":            7.97,
    "Colombie":            7.69,
    "Norvège":             7.15,
    "Mexique":             7.08,
    "Équateur":            6.63,
    "Uruguay":             6.51,
    "Canada":              6.23,
    "États-Unis":          6.20,
    "Croatie":             6.06,
    "Maroc":               5.98,
    "Côte d'Ivoire":       5.85,
    "Autriche":            5.78,
    "Turquie":             5.73,
    "Japon":               5.38,
    "Sénégal":             5.31,
    "Égypte":              4.96,
    "Écosse":              4.64,
    "Corée du Sud":        4.48,
    "République Tchèque":  4.29,
    "Suède":               4.21,
    "Bosnie-Herzégovine":  4.15,
    "Algérie":             4.08,
    "Paraguay":            3.95,
    "Iran":                3.80,
    "Ghana":               3.22,
    "Australie":           3.19,
    "RD Congo":            2.94,
    "Panama":              2.94,
    "Nouvelle-Zélande":    2.74,
    "Afrique du Sud":      2.64,
    "Ouzbékistan":         2.62,
    "Tunisie":             2.56,
    "Cap-Vert":            2.51,
    "Arabie Saoudite":     2.35,
    "Curaçao":             2.17,
    "Haïti":               2.08,
    "Jordanie":            2.05,
    "Qatar":               1.99,
    "Irak":                1.53,
}


# Mapping nom Bzzoiro (anglais) → nom nation français (wc2026_squad_players).
# Fallback utilisé quand le nom du joueur ne matche pas directement dans le squad.
BZZ_TEAM_TO_NATION: dict[str, str] = {
    "Algeria":             "Algérie",
    "Argentina":           "Argentine",
    "Australia":           "Australie",
    "Austria":             "Autriche",
    "Belgium":             "Belgique",
    "Bosnia & Herzegovina": "Bosnie-Herzégovine",
    "Brazil":              "Brésil",
    "Cabo Verde":          "Cap-Vert",
    "Canada":              "Canada",
    "Colombia":            "Colombie",
    "Croatia":             "Croatie",
    "Curaçao":             "Curaçao",
    "Czechia":             "République Tchèque",
    "Côte d'Ivoire":       "Côte d'Ivoire",
    "DR Congo":            "RD Congo",
    "Ecuador":             "Équateur",
    "Egypt":               "Égypte",
    "England":             "Angleterre",
    "France":              "France",
    "Germany":             "Allemagne",
    "Ghana":               "Ghana",
    "Haiti":               "Haïti",
    "Iran":                "Iran",
    "Iraq":                "Irak",
    "Japan":               "Japon",
    "Jordan":              "Jordanie",
    "Mexico":              "Mexique",
    "Morocco":             "Maroc",
    "Netherlands":         "Pays-Bas",
    "New Zealand":         "Nouvelle-Zélande",
    "Norway":              "Norvège",
    "Panama":              "Panama",
    "Paraguay":            "Paraguay",
    "Portugal":            "Portugal",
    "Qatar":               "Qatar",
    "Saudi Arabia":        "Arabie Saoudite",
    "Scotland":            "Écosse",
    "Senegal":             "Sénégal",
    "South Africa":        "Afrique du Sud",
    "South Korea":         "Corée du Sud",
    "Spain":               "Espagne",
    "Sweden":              "Suède",
    "Switzerland":         "Suisse",
    "Tunisia":             "Tunisie",
    "Türkiye":             "Turquie",
    "USA":                 "États-Unis",
    "Uruguay":             "Uruguay",
    "Uzbekistan":          "Ouzbékistan",
}


def _norm(name: str) -> str:
    # Hyphens treated as spaces so "In-Beom" and "In Beom" normalize identically.
    n = unicodedata.normalize("NFKD", name.lower().strip().replace("-", " "))
    return "".join(c for c in n if not unicodedata.combining(c))


class PlayerRanking(BaseModel):
    player_name: str
    nation: str | None
    flag_emoji: str | None
    position: str | None        # GK / DEF / MID / FWD (depuis squad)
    matches: int
    minutes: int
    goals: int
    assists: int
    xg: float
    xa: float
    shots: int
    shots_on_target: int
    finishing_delta: float      # goals - xg
    creation_delta: float       # assists - xa
    xg_per_90: float | None
    xa_per_90: float | None
    xg_tournoi: float | None    # xG BM attendus pour tout le tournoi (équipe)
    xg_left: float | None       # xg_tournoi - xG cumulé équipe en tournoi
    xa_left: float | None       # (xg_tournoi × 0.7) - xA cumulé équipe en tournoi
    team_xg_share: float | None # % du xG tournoi généré par ce joueur


@router.get("/rankings", response_model=list[PlayerRanking])
async def get_rankings(
    session: AsyncSession = Depends(get_db),
) -> list[PlayerRanking]:
    """Classement cumulatif des joueurs CDM 2026 depuis les stats Bzzoiro."""

    # ── 1. Aggregation depuis bzz_player_match_stats ──────────────────────────
    # DISTINCT ON (name, event) : Bzzoiro peut renvoyer des player_id différents
    # pour le même joueur entre deux syncs (ID historique vs ID CDM spécifique).
    # canonical_team : certains joueurs ont deux player_api_id dans bzz_players,
    # l'un avec national_team_api_id renseigné, l'autre sans. Le CTE canonical_team
    # résout la nation non-nulle en priorité (NULLS LAST) pour éviter les doublons
    # dans le GROUP BY final.
    rows: list[dict[str, Any]] = (
        await session.execute(
            text("""
                WITH deduped AS (
                    SELECT DISTINCT ON (p.name, ps.event_api_id)
                        p.name       AS player_name,
                        p.position   AS bzz_pos,
                        bt.name      AS national_team_name,
                        ps.minutes_played,
                        ps.goals,
                        ps.goal_assist,
                        ps.expected_goals,
                        ps.expected_assists,
                        ps.total_shots,
                        ps.shots_on_target
                    FROM bzz_player_match_stats ps
                    JOIN bzz_players p ON p.api_id = ps.player_api_id
                    JOIN bzz_events  e ON e.api_id = ps.event_api_id
                    LEFT JOIN bzz_teams bt ON bt.api_id = p.national_team_api_id
                    WHERE e.league_api_id = :lid
                      AND COALESCE(ps.minutes_played, 1) > 0
                    ORDER BY p.name, ps.event_api_id, bt.name NULLS LAST, ps.player_api_id DESC
                ),
                canonical_team AS (
                    SELECT DISTINCT ON (player_name)
                        player_name,
                        national_team_name
                    FROM deduped
                    ORDER BY player_name, national_team_name NULLS LAST
                )
                SELECT
                    d.player_name                            AS bzz_name,
                    MAX(d.bzz_pos)                           AS bzz_pos,
                    ct.national_team_name,
                    COUNT(*)                                 AS matches,
                    COALESCE(SUM(d.minutes_played), 0)       AS minutes,
                    COALESCE(SUM(d.goals), 0)                AS goals,
                    COALESCE(SUM(d.goal_assist), 0)          AS assists,
                    COALESCE(SUM(d.expected_goals), 0.0)     AS xg,
                    COALESCE(SUM(d.expected_assists), 0.0)   AS xa,
                    COALESCE(SUM(d.total_shots), 0)          AS shots,
                    COALESCE(SUM(d.shots_on_target), 0)      AS shots_on_target
                FROM deduped d
                JOIN canonical_team ct ON ct.player_name = d.player_name
                GROUP BY d.player_name, ct.national_team_name
                ORDER BY xg DESC
            """),
            {"lid": WC_LEAGUE_ID},
        )
    ).mappings().all()

    # ── 2. Chargement du squad WC2026 pour enrichissement (flag + position) ───
    squad_rows = (
        await session.execute(
            text("SELECT player_name, nation, flag_emoji, position FROM wc2026_squad_players")
        )
    ).mappings().all()

    # Index 1 : nom normalisé → liste d'entrées (pour le fallback nation)
    squad_by_norm: dict[str, list[dict]] = {}
    # Index 2 : (nation_fr, nom_normalisé) → entrée (pour flag/position sans risque inter-nation)
    squad_by_nation_norm: dict[tuple[str, str], dict] = {}
    for r in squad_rows:
        key = _norm(r["player_name"])
        squad_by_norm.setdefault(key, []).append(dict(r))
        squad_by_nation_norm[(r["nation"], key)] = dict(r)

    def _token_match(bzz_name: str) -> list[dict]:
        """Token-subset fallback : une entrée squad matche si tous ses tokens sont dans
        le nom Bzzoiro ou vice-versa. Gère les prénoms intermédiaires, réordonnements
        et traits d'union (déjà normalisés en espaces par _norm).
        """
        tokens_bzz = set(_norm(bzz_name).split())
        if not tokens_bzz:
            return []
        hits: list[dict] = []
        for norm_key, entries in squad_by_norm.items():
            tokens_sq = set(norm_key.split())
            if tokens_sq and (tokens_sq <= tokens_bzz or tokens_bzz <= tokens_sq):
                hits.extend(entries)
        return hits

    def _enrich_from_squad(bzz_name: str, nation_fr: str | None) -> dict | None:
        """Retourne l'entrée squad pour enrichissement flag/position.

        1. Lookup exact dans la nation connue (O(1), sans risque inter-nation).
        2. Fallback token-subset dans la nation connue (si 1 seul résultat).
        3. Sans nation : exact non-ambigu, puis token-subset non-ambigu.
        """
        norm = _norm(bzz_name)
        if nation_fr:
            entry = squad_by_nation_norm.get((nation_fr, norm))
            if entry:
                return entry
            fuzzy = [e for e in _token_match(bzz_name) if e["nation"] == nation_fr]
            return fuzzy[0] if len(fuzzy) == 1 else None
        hits = squad_by_norm.get(norm, [])
        if len(hits) == 1:
            return hits[0]
        fuzzy = _token_match(bzz_name)
        return fuzzy[0] if len(fuzzy) == 1 else None

    # ── 3. Première passe — construction intermédiaire ────────────────────────
    intermediate: list[dict] = []
    for r in rows:
        bzz_name: str = r["bzz_name"]
        national_team_name: str | None = r["national_team_name"]

        # Nation depuis la DB (fiable) — évite tout matching textuel flou
        nation = BZZ_TEAM_TO_NATION.get(national_team_name) if national_team_name else None

        # Fallback pour les joueurs sans national_team_api_id :
        # 1) exact non-ambigu, 2) token-subset non-ambigu (une seule nation possible)
        if nation is None:
            exact_hits = squad_by_norm.get(_norm(bzz_name), [])
            if len(exact_hits) == 1:
                nation = exact_hits[0]["nation"]
            else:
                fuzzy_hits = _token_match(bzz_name)
                nations = {e["nation"] for e in fuzzy_hits}
                if len(nations) == 1:
                    nation = next(iter(nations))

        # Enrichissement flag + position dans la nation connue uniquement
        squad_entry = _enrich_from_squad(bzz_name, nation)
        if squad_entry:
            flag_emoji = squad_entry["flag_emoji"]
            position   = squad_entry["position"]
        else:
            flag_emoji = None
            position   = _bzz_pos_to_squad(r["bzz_pos"])

        intermediate.append({
            "player_name": bzz_name,
            "nation": nation,
            "flag_emoji": flag_emoji,
            "position": position,
            "matches": int(r["matches"]),
            "minutes": int(r["minutes"]),
            "goals": int(r["goals"]),
            "assists": int(r["assists"]),
            "xg": round(float(r["xg"]), 2),
            "xa": round(float(r["xa"]), 2),
            "shots": int(r["shots"]),
            "shots_on_target": int(r["shots_on_target"]),
            "bzz_pos": r["bzz_pos"],
        })

    # ── 4. xG et xA cumulés par équipe en tournoi ─────────────────────────────
    team_xg: dict[str, float] = {}
    team_xa: dict[str, float] = {}
    for p in intermediate:
        n = p["nation"]
        if n:
            team_xg[n] = team_xg.get(n, 0.0) + p["xg"]
            team_xa[n] = team_xa.get(n, 0.0) + p["xa"]

    # ── 5. Résultats finaux ───────────────────────────────────────────────────
    result: list[PlayerRanking] = []
    for p in intermediate:
        xg      = p["xg"]
        xa      = p["xa"]
        minutes = p["minutes"]
        nation  = p["nation"]

        xg_per_90 = round(xg / minutes * 90, 2) if minutes >= 10 else None
        xa_per_90 = round(xa / minutes * 90, 2) if minutes >= 10 else None

        bm         = BM_XG.get(nation) if nation else None
        txg        = team_xg.get(nation, 0.0) if nation else 0.0
        txa        = team_xa.get(nation, 0.0) if nation else 0.0
        xg_left    = round(bm - txg, 2)         if bm is not None else None
        xa_left    = round(bm * 0.7 - txa, 2)   if bm is not None else None
        team_xg_share = round(xg / txg * 100, 1) if txg > 0 and xg > 0 else None

        result.append(PlayerRanking(
            player_name=p["player_name"],
            nation=nation,
            flag_emoji=p["flag_emoji"],
            position=p["position"],
            matches=p["matches"],
            minutes=minutes,
            goals=p["goals"],
            assists=p["assists"],
            xg=xg,
            xa=xa,
            shots=p["shots"],
            shots_on_target=p["shots_on_target"],
            finishing_delta=round(p["goals"] - xg, 2),
            creation_delta=round(p["assists"] - xa, 2),
            xg_per_90=xg_per_90,
            xa_per_90=xa_per_90,
            xg_tournoi=bm,
            xg_left=xg_left,
            xa_left=xa_left,
            team_xg_share=team_xg_share,
        ))

    return result


def _bzz_pos_to_squad(pos: str | None) -> str | None:
    """Convertit la position Bzzoiro (G/D/M/F) vers le format squad (GK/DEF/MID/FWD)."""
    return {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}.get(pos or "", None)


# ── Backtest KO ────────────────────────────────────────────────────────────────

_FR_BOOKS = frozenset({"betclic", "unibet", "pmu"})
_ELO_D = 400.0


def _vig_removed_home(h: float, d: float, a: float) -> float:
    """Probabilité implicite home vig-removed, draw splitté 50/50."""
    rh, rd, ra = 1 / h, 1 / d, 1 / a
    total = rh + rd + ra
    return rh / total + (rd / total) / 2


def _elo_prob(elo_a: float, elo_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / _ELO_D))


class KOPredictionOut(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    matchweek: int | None
    kickoff_utc: str | None
    # Prédictions
    elo_home: float | None
    elo_away: float | None
    elo_prob_home: float | None
    pin_prob_home: float | None
    pin_odds_home: float | None
    pin_odds_draw: float | None
    pin_odds_away: float | None
    fr_odds_home: float | None
    fr_odds_away: float | None
    # Résultat
    home_score: int | None
    away_score: int | None
    winner: str | None
    snapshot_utc: str | None


async def _snapshot_ko_predictions(session: AsyncSession) -> int:
    """Snapshot ELO + cotes pour tous les matchs KO scheduled sans prédiction enregistrée.

    Appelé par le worker avant chaque match KO. Retourne le nombre de lignes créées.
    """
    from datetime import datetime, timezone
    from app.models.wc2026_predictions import WC2026KOPrediction

    # Fixtures KO scheduled sans snapshot
    rows = (await session.execute(text("""
        SELECT f.id, f.home_team, f.away_team, f.matchweek, f.kickoff_utc
        FROM fixtures f
        WHERE f.league = 'world_cup_2026'
          AND f.matchweek = ANY(:mws)
          AND f.status = 'scheduled'
          AND NOT EXISTS (
              SELECT 1 FROM wc2026_ko_predictions p WHERE p.fixture_id = f.id
          )
        ORDER BY f.kickoff_utc
    """), {"mws": list(KO_MATCHWEEKS)})).mappings().all()

    if not rows:
        return 0

    # ELO courant depuis wc2026_team_advancement
    elo_rows = (await session.execute(text(
        "SELECT nation, elo FROM wc2026_team_advancement"
    ))).mappings().all()
    elo_map: dict[str, float] = {r["nation"]: r["elo"] for r in elo_rows}

    # Dernières cotes Pinnacle + FR par fixture
    odds_rows = (await session.execute(text("""
        WITH latest AS (
            SELECT DISTINCT ON (fixture_id, bookmaker, outcome)
                fixture_id, bookmaker, outcome, odds
            FROM match_odds_snapshots
            WHERE market_type = 'h2h'
            ORDER BY fixture_id, bookmaker, outcome, snapshot_utc DESC
        )
        SELECT fixture_id, bookmaker,
            MAX(CASE WHEN outcome='home' THEN odds END) h,
            MAX(CASE WHEN outcome='draw' THEN odds END) d,
            MAX(CASE WHEN outcome='away' THEN odds END) a
        FROM latest
        WHERE fixture_id = ANY(:fids)
        GROUP BY fixture_id, bookmaker
    """), {"fids": [r["id"] for r in rows]})).mappings().all()

    # Organiser par fixture
    from collections import defaultdict
    odds_by_fixture: dict[int, list[dict]] = defaultdict(list)
    for o in odds_rows:
        odds_by_fixture[o["fixture_id"]].append(dict(o))

    now = datetime.now(timezone.utc)
    created = 0
    for f in rows:
        fid = f["id"]
        home = f["home_team"]
        away = f["away_team"]

        # Ne pas snapshoter avant de connaître les deux équipes
        if _is_placeholder(home) or _is_placeholder(away):
            continue

        elo_h = elo_map.get(home)
        elo_a = elo_map.get(away)
        elo_prob = _elo_prob(elo_h, elo_a) if elo_h and elo_a else None

        pin = next((o for o in odds_by_fixture[fid] if o["bookmaker"] == "pinnacle"), None)
        pin_h = float(pin["h"]) if pin and pin["h"] else None
        pin_d = float(pin["d"]) if pin and pin["d"] else None
        pin_a = float(pin["a"]) if pin and pin["a"] else None
        pin_prob = _vig_removed_home(pin_h, pin_d, pin_a) if pin_h and pin_d and pin_a else None

        fr = [o for o in odds_by_fixture[fid] if o["bookmaker"] in _FR_BOOKS]
        fr_homes = [float(o["h"]) for o in fr if o["h"]]
        fr_aways = [float(o["a"]) for o in fr if o["a"]]
        fr_h = max(fr_homes) if fr_homes else None
        fr_a = max(fr_aways) if fr_aways else None

        session.add(WC2026KOPrediction(
            fixture_id=fid,
            home_team=home,
            away_team=away,
            matchweek=f["matchweek"],
            kickoff_utc=f["kickoff_utc"],
            elo_home=elo_h,
            elo_away=elo_a,
            elo_prob_home=elo_prob,
            pin_odds_home=pin_h,
            pin_odds_draw=pin_d,
            pin_odds_away=pin_a,
            pin_prob_home=pin_prob,
            fr_odds_home=fr_h,
            fr_odds_away=fr_a,
            snapshot_utc=now,
        ))
        created += 1

    await session.commit()
    return created


async def _cleanup_placeholder_predictions(session: AsyncSession) -> int:
    """Supprime les prédictions dont les noms d'équipes sont des placeholders (W83, W89...).

    Appelé dans la settlement pipeline après sync_fixtures_from_bzz. Après suppression,
    _snapshot_ko_predictions re-créera les snapshots avec les vrais noms.
    Retourne le nombre de lignes supprimées.
    """
    result = await session.execute(text("""
        DELETE FROM wc2026_ko_predictions
        WHERE home_team ~ '^[WL][0-9]'
           OR away_team ~ '^[WL][0-9]'
           OR home_team ~ '^[0-9][A-Z]$'
           OR away_team ~ '^[0-9][A-Z]$'
           OR home_team ~ '^[A-Z][0-9]$'
           OR away_team ~ '^[A-Z][0-9]$'
    """))
    deleted = result.rowcount
    if deleted:
        await session.commit()
    return deleted


async def _update_ko_results(session: AsyncSession) -> int:
    """Met à jour le winner pour les prédictions dont le match est terminé."""
    from datetime import datetime, timezone
    from app.models.wc2026_predictions import WC2026KOPrediction
    from sqlalchemy import select

    pending = (await session.execute(
        select(WC2026KOPrediction).where(WC2026KOPrediction.winner.is_(None))
    )).scalars().all()

    if not pending:
        return 0

    fids = [p.fixture_id for p in pending]
    results = (await session.execute(text("""
        SELECT id, home_score, away_score, status
        FROM fixtures
        WHERE id = ANY(:fids) AND status = 'finished'
    """), {"fids": fids})).mappings().all()
    results_map = {r["id"]: r for r in results}

    now = datetime.now(timezone.utc)
    updated = 0
    for pred in pending:
        r = results_map.get(pred.fixture_id)
        if not r or r["home_score"] is None:
            continue
        if r["home_score"] > r["away_score"]:
            pred.winner = "home"
        elif r["away_score"] > r["home_score"]:
            pred.winner = "away"
        else:
            pred.winner = "draw"
        pred.home_score = r["home_score"]
        pred.away_score = r["away_score"]
        pred.result_recorded_utc = now
        updated += 1

    if updated:
        await session.commit()
    return updated


@router.post("/ko-predictions/snapshot")
async def snapshot_ko_predictions(session: AsyncSession = Depends(get_db)):
    """Snapshot ELO + cotes pre-match pour tous les KO scheduled non encore enregistrés."""
    created = await _snapshot_ko_predictions(session)
    updated = await _update_ko_results(session)
    return {"created": created, "results_updated": updated}


@router.get("/ko-predictions", response_model=list[KOPredictionOut])
async def get_ko_predictions(session: AsyncSession = Depends(get_db)):
    """Retourne toutes les prédictions KO (avec résultats quand disponibles) pour backtest."""
    rows = (await session.execute(text("""
        SELECT
            fixture_id, home_team, away_team, matchweek,
            kickoff_utc::text, elo_home, elo_away, elo_prob_home,
            pin_prob_home, pin_odds_home, pin_odds_draw, pin_odds_away,
            fr_odds_home, fr_odds_away,
            home_score, away_score, winner, snapshot_utc::text
        FROM wc2026_ko_predictions
        ORDER BY kickoff_utc
    """))).mappings().all()
    return [KOPredictionOut(**dict(r)) for r in rows]
