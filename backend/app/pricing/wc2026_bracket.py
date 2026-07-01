"""WC2026 bracket simulation: ELO engine + group standings + Monte Carlo."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

WC_LEAGUE_API_ID = 27
N_SIM = 50_000
_K_GROUP   = 20.0   # Poule : rotation possible, enjeux variables
_K_KO      = 30.0   # KO décisif (score ≠) : équipes à 100%
_K_KO_TIE  = 10.0   # KO nul (score =) : prolongations/penaltys ≈ tirage au sort
_BASE_ELO  = 1500.0
_ELO_D     = 400.0  # Dénominateur ELO (calibré empiriquement sur cotes Pinnacle)

# WC2026 round_number scheme (from bzz_events)
_GROUP_ROUNDS = frozenset({1, 2, 3})
_ROUND_R32 = 6
_ROUND_R16 = 5
_ROUND_QF  = 27
_ROUND_SF  = 28
_ROUND_FINAL = 29

STAGES = ["r32", "r16", "qf", "sf", "finalist", "winner"]

# Résultats KO décidés aux prolongations/penaltys (score = dans bzz_events,
# vainqueur réel inconnu de la DB). Clé = frozenset({gagnant, perdant}).
# Mise à jour manuelle au fil du tournoi.
_MANUAL_KO_WINNERS: dict[frozenset, str] = {
    frozenset({"Morocco",     "Netherlands"}): "Morocco",    # R32 — penaltys
    frozenset({"Paraguay",    "Germany"}):     "Paraguay",   # R32 — penaltys
}


# ── ELO Engine ────────────────────────────────────────────────────────────────

def _elo_from_historical() -> dict[str, float]:
    """Initialise ELO ratings from historical international match results (2018–2026-06-10).

    Computed by scripts/compute_historical_elo.py from ~3700 international matches.
    Uses competition-weighted K factors: K=60 WC, K=50 continental, K=40 qualifiers, K=15 friendly.
    """
    from app.ingestion.wc2026.historical_elo import HISTORICAL_ELO
    return dict(HISTORICAL_ELO)


def _update_elo(
    elo: dict[str, float],
    team_a: str,
    team_b: str,
    score_a: int,
    score_b: int,
    is_ko: bool = False,
) -> None:
    """Update ELO ratings in-place after a match.

    K varie selon le contexte :
    - Poule (is_ko=False)         → K=20 (signal bruité, rotation possible)
    - KO décisif (score ≠)        → K=30 (équipes à 100%, résultat fiable)
    - KO nul (score =, is_ko=True) → K=10 (prolongations/penaltys ≈ aléatoire)
    """
    ea = 1.0 / (1.0 + 10.0 ** ((elo[team_b] - elo[team_a]) / _ELO_D))
    result_a = 1.0 if score_a > score_b else (0.5 if score_a == score_b else 0.0)
    if is_ko:
        k = _K_KO_TIE if score_a == score_b else _K_KO
    else:
        k = _K_GROUP
    delta = k * (result_a - ea)
    elo[team_a] += delta
    elo[team_b] -= delta


def _match_proba_group(elo_a: float, elo_b: float) -> tuple[float, float, float]:
    """Return (p_win_a, p_draw, p_win_b) for a group-stage match."""
    ea = 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / _ELO_D))
    p_draw = 0.28 * (1.0 - abs(2.0 * ea - 1.0))
    p_win_a = ea * (1.0 - p_draw)
    p_win_b = (1.0 - ea) * (1.0 - p_draw)
    return p_win_a, p_draw, p_win_b


def _match_proba_ko(elo_a: float, elo_b: float) -> float:
    """Return P(team_a advances) in a knockout match (no draw)."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / _ELO_D))


# ── Group Stage ────────────────────────────────────────────────────────────────

def _build_groups(events: list[dict]) -> dict[str, list[str]]:
    """Build {group_id: [team1..4]} using Union-Find on round 1-3 matchups."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra > rb:
                ra, rb = rb, ra
            parent[rb] = ra

    for ev in events:
        if ev.get("round_number") in _GROUP_ROUNDS:
            home, away = ev["home_team"], ev["away_team"]
            if home not in parent:
                parent[home] = home
            if away not in parent:
                parent[away] = away
            union(home, away)

    buckets: dict[str, list[str]] = {}
    for team in list(parent):
        root = find(team)
        buckets.setdefault(root, []).append(team)
    return buckets


def _rank_group(standing: dict[str, dict]) -> list[str]:
    """Sort teams in a group: pts → gd → gf → elo (all descending)."""
    return sorted(
        standing,
        key=lambda t: (
            standing[t]["pts"],
            standing[t]["gd"],
            standing[t]["gf"],
            standing[t]["elo"],
        ),
        reverse=True,
    )


def _select_best_thirds(thirds: list[dict]) -> list[dict]:
    """Pick best 8 third-placed teams by pts → gd → gf."""
    return sorted(
        thirds,
        key=lambda t: (t["pts"], t["gd"], t["gf"]),
        reverse=True,
    )[:8]


def _find_match(events: list[dict], ta: str, tb: str) -> dict | None:
    """Find a played match between ta and tb (either order)."""
    for ev in events:
        if (ev["home_team"] == ta and ev["away_team"] == tb) or \
           (ev["home_team"] == tb and ev["away_team"] == ta):
            return ev
    return None


def _apply_result(
    standing: dict[str, dict], ta: str, tb: str, ga: int, gb: int
) -> None:
    """Apply a match result to the group standings in-place."""
    standing[ta]["gf"] += ga
    standing[ta]["gd"] += ga - gb
    standing[tb]["gf"] += gb
    standing[tb]["gd"] += gb - ga
    if ga > gb:
        standing[ta]["pts"] += 3
    elif ga == gb:
        standing[ta]["pts"] += 1
        standing[tb]["pts"] += 1
    else:
        standing[tb]["pts"] += 3


def _simulate_group(
    group_teams: list[str],
    events: list[dict],
    elo: dict[str, float],
    rng: np.random.Generator,
) -> dict[str, dict]:
    """Simulate a group: finished matches use real scores; unplayed are sampled.

    ELO is frozen at entry — it reflects all finished matches before this
    simulation call, updated once in compute_wc_advancement, not per-match here.
    """
    standing: dict[str, dict] = {
        t: {"pts": 0, "gd": 0, "gf": 0, "elo": elo.get(t, _BASE_ELO)}
        for t in group_teams
    }
    # Build O(1) lookup for this group's real results
    event_map: dict[frozenset, dict] = {
        frozenset({ev["home_team"], ev["away_team"]}): ev
        for ev in events
        if ev.get("round_number") in _GROUP_ROUNDS
    }

    for i, ta in enumerate(group_teams):
        for tb in group_teams[i + 1:]:
            match = event_map.get(frozenset({ta, tb}))
            if match and match["status"] == "finished":
                sh, sa = match["home_score"], match["away_score"]
                if match["home_team"] == ta:
                    ga, gb = sh, sa
                else:
                    ga, gb = sa, sh
            else:
                pw, pd, pl = _match_proba_group(
                    elo.get(ta, _BASE_ELO), elo.get(tb, _BASE_ELO)
                )
                roll = rng.random()
                if roll < pw:
                    ga, gb = 1, 0
                elif roll < pw + pd:
                    ga, gb = 0, 0
                else:
                    ga, gb = 0, 1

            _apply_result(standing, ta, tb, ga, gb)

    return standing


# ── Bracket Placement ──────────────────────────────────────────────────────────

def _build_bracket(
    groups: dict[str, list[str]],
    standings: dict[str, dict[str, dict]],
    thirds_teams: list[str],
) -> list[tuple[str, str]]:
    """Build 16 R32 matchup pairs (4 regions × 4 matches each).

    Groups are sorted by key and partitioned into regions of 3.
    Within each region (groups X, Y, Z) with 2 thirds T1, T2:
      Match 1: W_X vs R_Y   Match 2: W_Y vs T1
      Match 3: W_Z vs R_X   Match 4: R_Z vs T2
    """
    # Resolve group positions to team names
    pos: dict[str, str] = {}
    for gid, teams in groups.items():
        ranked = _rank_group(standings[gid])
        pos[f"W_{gid}"] = ranked[0]
        pos[f"R_{gid}"] = ranked[1]

    # Partition sorted group IDs into regions of 3
    sorted_gids = sorted(groups.keys())
    pairs: list[tuple[str, str]] = []
    third_idx = 0

    for i in range(0, len(sorted_gids) - 2, 3):
        x, y, z = sorted_gids[i], sorted_gids[i + 1], sorted_gids[i + 2]

        t1 = thirds_teams[third_idx]     if third_idx < len(thirds_teams) else pos.get(f"R_{y}", "?")
        t2 = thirds_teams[third_idx + 1] if third_idx + 1 < len(thirds_teams) else pos.get(f"R_{z}", "?")
        third_idx += 2

        pairs.append((pos[f"W_{x}"], pos[f"R_{y}"]))
        pairs.append((pos[f"W_{y}"], t1))
        pairs.append((pos[f"W_{z}"], pos[f"R_{x}"]))
        pairs.append((pos[f"R_{z}"], t2))

    return pairs  # 16 pairs for 12 groups


def _simulate_ko_round(
    pairs: list[tuple[str, str]],
    elo: dict[str, float],
    rng: np.random.Generator,
    locked: dict[frozenset, str] | None = None,
    market_probs: dict[frozenset, tuple[str, float]] | None = None,
) -> tuple[list[str], list[str]]:
    """Simulate one knockout round. Returns (winners, losers).

    locked : résultats KO déjà joués {frozenset({ta, tb}): winner}.
    market_probs : {frozenset({ta, tb}): (ref_team, P(ref_team qualifies))}.
      ref_team est l'équipe "home" de la fixture. Quand disponible, remplace ELO.
    """
    winners, losers = [], []
    for ta, tb in pairs:
        key = frozenset({ta, tb})
        if locked and key in locked:
            winner = locked[key]
            loser = tb if winner == ta else ta
        else:
            if market_probs and key in market_probs:
                ref_team, p_ref = market_probs[key]
                p_a = p_ref if ta == ref_team else 1.0 - p_ref
            else:
                p_a = _match_proba_ko(elo.get(ta, _BASE_ELO), elo.get(tb, _BASE_ELO))
            if rng.random() < p_a:
                winner, loser = ta, tb
            else:
                winner, loser = tb, ta
        winners.append(winner)
        losers.append(loser)
    return winners, losers


# ── Monte Carlo ────────────────────────────────────────────────────────────────

def simulate_bracket(
    elo: dict[str, float],
    groups: dict[str, list[str]],
    events: list[dict],
    n_sim: int = N_SIM,
    market_r32_probs: dict[frozenset, tuple[str, float]] | None = None,
) -> dict[str, dict[str, float]]:
    """Run Monte Carlo simulation of the WC2026 bracket.

    Returns {nation: {stage: probability}} for all nations in elo.
    Stages: r32, r16, qf, sf, finalist, winner.

    market_r32_probs : {frozenset({home, away}): (home_team, P(home qualifies))} depuis Pinnacle.
      Pour les matchs R32 avec cotes disponibles, remplace la probabilité ELO.
      Pour les rounds suivants (adversaires inconnus), ELO est toujours utilisé.
    """
    # Pré-calculer les résultats KO verrouillés :
    # 1) matchs décisifs (score ≠) depuis bzz_events
    # 2) matchs nuls en DB mais décidés aux penaltys (overrides manuels)
    locked_ko: dict[frozenset, str] = dict(_MANUAL_KO_WINNERS)
    for ev in events:
        if (
            ev.get("round_number") not in _GROUP_ROUNDS
            and ev.get("status") == "finished"
            and ev.get("home_score") is not None
            and ev["home_score"] != ev["away_score"]
        ):
            winner = ev["home_team"] if ev["home_score"] > ev["away_score"] else ev["away_team"]
            locked_ko[frozenset({ev["home_team"], ev["away_team"]})] = winner

    # Faits établis sur l'avancement réel en KO.
    # stage_ceiling[team] = dernier stade atteint (r32 = a qualifié des groupes,
    # r16 = a passé les 1/16, etc.). Les équipes éliminées sur score ≠ ont un
    # plafond clair ; les matchs à égalité (ET/penaltys) ne donnent pas de certitude.
    _KO_ROUND_TO_STAGE = {
        _ROUND_R32:   ("r32",       "r16"),
        _ROUND_R16:   ("r16",       "qf"),
        _ROUND_QF:    ("qf",        "sf"),
        _ROUND_SF:    ("sf",        "finalist"),
        _ROUND_FINAL: ("finalist",  "winner"),
    }
    # definite_max[team] = index du dernier stage garanti (tout ce qui suit → 0%)
    definite_eliminated: dict[str, int] = {}  # team → index dans STAGES après lequel tout est 0
    definite_min: dict[str, int] = {}         # team → index minimal garanti (tout ce qui précède → 100%)
    # Intégrer les overrides manuels (penaltys) dans les faits établis
    for pair, winner in _MANUAL_KO_WINNERS.items():
        teams = list(pair)
        loser = teams[1] if teams[0] == winner else teams[0]
        # On ne connaît pas le round exactement — on cherche dans events
        for ev in events:
            if frozenset({ev.get("home_team"), ev.get("away_team")}) == pair:
                rnd = ev.get("round_number")
                if rnd in _KO_ROUND_TO_STAGE:
                    loser_stage, winner_next = _KO_ROUND_TO_STAGE[rnd]
                    definite_eliminated[loser]  = STAGES.index(loser_stage)
                    definite_min[winner]        = max(definite_min.get(winner, -1), STAGES.index(winner_next))
                break
    for ev in events:
        rnd = ev.get("round_number")
        if rnd not in _KO_ROUND_TO_STAGE:
            continue
        if ev.get("status") != "finished" or ev.get("home_score") is None:
            continue
        if ev["home_score"] == ev["away_score"]:
            continue  # ET/pens — vainqueur inconnu, pas de certitude
        winner = ev["home_team"] if ev["home_score"] > ev["away_score"] else ev["away_team"]
        loser  = ev["away_team"] if winner == ev["home_team"] else ev["home_team"]
        loser_stage, winner_next = _KO_ROUND_TO_STAGE[rnd]
        loser_idx  = STAGES.index(loser_stage)
        winner_idx = STAGES.index(winner_next)
        # Le perdant a atteint loser_stage mais pas winner_next
        definite_eliminated[loser]  = loser_idx   # tout après loser_idx → 0%
        definite_min[winner]        = max(definite_min.get(winner, -1), winner_idx)

    rng = np.random.default_rng(42)
    counters: dict[str, dict[str, int]] = {
        n: {s: 0 for s in STAGES} for n in elo
    }

    for _ in range(n_sim):
        # 1. Simulate remaining group matches → standings
        standings: dict[str, dict[str, dict]] = {}
        for gid, teams in groups.items():
            standings[gid] = _simulate_group(teams, events, elo, rng)

        # 2. Collect thirds and pick best 8
        all_thirds: list[dict] = []
        for gid, teams in groups.items():
            ranked = _rank_group(standings[gid])
            if len(ranked) >= 3:
                third = ranked[2]
                st = standings[gid][third]
                all_thirds.append({
                    "team": third,
                    "pts": st["pts"],
                    "gd": st["gd"],
                    "gf": st["gf"],
                })
        best8 = _select_best_thirds(all_thirds)
        thirds_teams = sorted(t["team"] for t in best8)

        # 3. Mark r32 qualifiers
        for gid in groups:
            ranked = _rank_group(standings[gid])
            for t in ranked[:2]:
                if t in counters:
                    counters[t]["r32"] += 1
        for t in thirds_teams:
            if t in counters:
                counters[t]["r32"] += 1

        # 4. Build bracket and simulate knockouts (résultats réels verrouillés)
        pairs_r32 = _build_bracket(groups, standings, thirds_teams)
        winners_r32, _ = _simulate_ko_round(pairs_r32, elo, rng, locked_ko, market_r32_probs)
        for t in winners_r32:
            if t in counters:
                counters[t]["r16"] += 1

        pairs_r16 = list(zip(winners_r32[::2], winners_r32[1::2]))
        winners_r16, _ = _simulate_ko_round(pairs_r16, elo, rng, locked_ko)
        for t in winners_r16:
            if t in counters:
                counters[t]["qf"] += 1

        pairs_qf = list(zip(winners_r16[::2], winners_r16[1::2]))
        winners_qf, _ = _simulate_ko_round(pairs_qf, elo, rng, locked_ko)
        for t in winners_qf:
            if t in counters:
                counters[t]["sf"] += 1

        pairs_sf = list(zip(winners_qf[::2], winners_qf[1::2]))
        winners_sf, _ = _simulate_ko_round(pairs_sf, elo, rng, locked_ko)
        for t in winners_sf:
            if t in counters:
                counters[t]["finalist"] += 1

        if len(winners_sf) >= 2:
            pairs_final = [(winners_sf[0], winners_sf[1])]
            winners_final, _ = _simulate_ko_round(pairs_final, elo, rng, locked_ko)
            for t in winners_final:
                if t in counters:
                    counters[t]["winner"] += 1

    raw = {
        nation: {stage: count / n_sim for stage, count in stages.items()}
        for nation, stages in counters.items()
    }

    # Écrase les probabilités incompatibles avec les résultats KO réels :
    # - les équipes éliminées sur score ≠ ont 0% pour tout stade ultérieur
    # - les équipes qui ont gagné un match KO décisif ont 100% pour les stades
    #   qu'elles ont nécessairement atteints
    for nation, probs in raw.items():
        if nation in definite_eliminated:
            ceiling = definite_eliminated[nation]
            for i, stage in enumerate(STAGES):
                if i > ceiling:
                    probs[stage] = 0.0
        if nation in definite_min:
            floor_idx = definite_min[nation]
            for i, stage in enumerate(STAGES):
                if i <= floor_idx:
                    probs[stage] = 1.0

    return raw


def _e_games_from_probs(probs: dict[str, float]) -> float:
    """E[games] = 3 + p_r32 + p_r16 + p_qf + 2*p_sf + p_finalist — mirrors wc2026_tournament.py formula for calibration consistency."""
    return (
        3.0
        + probs["r32"]
        + probs["r16"]
        + probs["qf"]
        + 2.0 * probs["sf"]
        + probs["finalist"]
    )


# ── Public Entry Point ─────────────────────────────────────────────────────────

async def compute_wc_advancement(session: Any) -> list[dict]:
    """Compute WC2026 team advancement probabilities via ELO + Monte Carlo.

    Reads all WC events from bzz_events, applies ELO updates for finished
    matches, then runs simulate_bracket(). Returns a list of dicts ready for
    DB INSERT into wc2026_team_advancement.
    """
    from sqlalchemy import text

    rows = (await session.execute(text("""
        SELECT
            e.round_number,
            e.status,
            e.home_score,
            e.away_score,
            ht.name AS home_team,
            at.name AS away_team
        FROM bzz_events e
        JOIN bzz_teams ht ON ht.api_id = e.home_team_api_id
        JOIN bzz_teams at ON at.api_id = e.away_team_api_id
        WHERE e.league_api_id = :lid
        ORDER BY e.event_date ASC
    """), {"lid": WC_LEAGUE_API_ID})).mappings().all()

    events = [dict(r) for r in rows]

    # Build reverse map: bzz team name → canonical nation name
    from app.ingestion.wc2026.team_bm import WC2026_NATION_NAME_ALIASES
    from app.ingestion.wc2026.historical_elo import HISTORICAL_ELO
    bzz_to_canon: dict[str, str] = {
        bzz_name: canon
        for canon, bzz_name in WC2026_NATION_NAME_ALIASES.items()
    }
    for canon in HISTORICAL_ELO:
        bzz_to_canon.setdefault(canon, canon)

    norm_events = []
    for ev in events:
        home = bzz_to_canon.get(ev["home_team"])
        away = bzz_to_canon.get(ev["away_team"])
        if home and away:
            norm_events.append({**ev, "home_team": home, "away_team": away})

    # Initialise ELO and apply all finished matches
    elo = _elo_from_historical()
    for ev in norm_events:
        if ev["status"] == "finished" and ev["home_score"] is not None:
            is_ko = ev.get("round_number") not in _GROUP_ROUNDS
            _update_elo(elo, ev["home_team"], ev["away_team"],
                        ev["home_score"], ev["away_score"], is_ko=is_ko)

    groups = _build_groups(norm_events)
    if not groups:
        logger.warning("compute_wc_advancement: no group events found — returning empty")
        return []

    # Charger les probabilités marché Pinnacle pour les matchs R32 avec cotes disponibles.
    # Clé = frozenset({home_team, away_team}) en noms canoniques.
    # Valeur = (home_canonical, P(home qualifies)) — nécessaire pour retrouver la direction.
    market_r32_probs: dict[frozenset, tuple[str, float]] = {}
    pred_rows = (await session.execute(text("""
        SELECT home_team, away_team, pin_prob_home
        FROM wc2026_ko_predictions
        WHERE matchweek = :r32_mw
          AND pin_prob_home IS NOT NULL
          AND winner IS NULL
    """), {"r32_mw": _ROUND_R32})).mappings().all()
    for pr in pred_rows:
        h = bzz_to_canon.get(pr["home_team"], pr["home_team"])
        a = bzz_to_canon.get(pr["away_team"], pr["away_team"])
        market_r32_probs[frozenset({h, a})] = (h, float(pr["pin_prob_home"]))

    logger.info(
        "compute_wc_advancement: %d groups, %d nations, %d matchs R32 avec cotes Pinnacle, running %d simulations",
        len(groups), len(elo), len(market_r32_probs), N_SIM,
    )

    probs = simulate_bracket(elo, groups, norm_events, n_sim=N_SIM, market_r32_probs=market_r32_probs or None)

    now = datetime.now(timezone.utc)
    return [
        {
            "nation":      nation,
            "elo":         round(elo.get(nation, _BASE_ELO), 1),
            "p_r32":       round(p["r32"],      6),
            "p_r16":       round(p["r16"],      6),
            "p_qf":        round(p["qf"],       6),
            "p_sf":        round(p["sf"],       6),
            "p_finalist":  round(p["finalist"], 6),
            "p_winner":    round(p["winner"],   6),
            "e_games":     round(_e_games_from_probs(p), 4),
            "n_sim":       N_SIM,
            "computed_at": now,
        }
        for nation, p in probs.items()
    ]
