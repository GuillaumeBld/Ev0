"""Top-Down Match-Centric pricing engine — Bzzoiro model.

Three-stage pipeline:
  Stage 1 — Team Match xG : MarketXgService (market-implied or Dixon-Coles)
  Stage 2 — Player Shares : form-blended top-down shares (npxg_share / xa_share)
  Stage 3 — Player Pricing: finishing_multiplier + creation_multiplier_v2 per player

Source: Bzzoiro (bzz_player_season_stats + bzz_players)
"""

from __future__ import annotations

import logging
import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import Integer, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import alert_bg
from app.pricing.assist import (
    ASSIST_GOAL_RATE,
    CLAMP_LAMBDA_MIN as CLAMP_LAMBDA_MIN_ASSIST,
    calculate_assist_lambda,
    calculate_supersub_prob_assist,
)
from app.pricing.goalscorer import (
    calculate_finishing_multiplier,
    calculate_supersub_prob,
)
from app.services.competitions import (
    SELECTION_COMPETITION_IDS,
    WC2026_COMPETITION_API_ID,
    is_international_fixture,
    is_wc2026_fixture,
    league_key_for,
)
from app.services.market_xg import MarketXgResult, MarketXgService

# Alertes « incidents » du calculateur déjà envoyées par ce processus. Le
# calcul tourne à chaque passage (60 s) : sans mémoire, la même équipe non
# reliée réalerterait ~4 fois par heure (send_alert ne filtre que 15 min).
_incidents_envoyes: set[str] = set()


def _alert_once(message: str) -> None:
    """Alerte incidents envoyée une seule fois par message et par processus."""
    if message in _incidents_envoyes:
        return
    _incidents_envoyes.add(message)
    alert_bg(message, "incidents")


# ── Constants ─────────────────────────────────────────────────────

HOME_ADVANTAGE = 1.22
SHRINKAGE_N = 30.0
PENS_PER_MATCH = 0.10
PEN_CONVERSION = 0.78
CLAMP_MULTIPLIER_MIN = 0.5
CLAMP_MULTIPLIER_MAX = 2.0
# Deprecated — kept for backward compat (imports, tests). Logic moved to 11-bucket POSITION_NPXG_PRIORS.
DF_SETPIECE_DISCOUNT: float = 0.55

FORM_WEIGHTS_BY_POSITION: dict[str, float] = {
    # 11 fine-grained buckets
    "CF_lone": 0.30, "CF_pair": 0.28, "SS": 0.25, "winger": 0.22,
    "AM": 0.20, "CM": 0.18, "DM": 0.12,
    "WB": 0.10, "FB": 0.08, "CB": 0.06, "GK": 0.00,
    # Legacy 3-bucket fallback (kept for backward compat)
    "FW": 0.25, "MF": 0.20, "DF": 0.10,
}
_FORM_WEIGHT_DEFAULT = 0.18

# 11-bucket npxG/90 priors (calibrated on Big5 + UCL 2024-25, ≥450 min)
POSITION_NPXG_PRIORS: dict[str, float] = {
    "CF_lone": 0.42, "CF_pair": 0.35, "SS": 0.22,
    "winger": 0.14, "AM": 0.10, "CM": 0.05, "DM": 0.03,
    "WB": 0.02, "FB": 0.01, "CB": 0.01, "GK": 0.00,
    # Legacy fallback
    "FW": 0.25, "MF": 0.08, "DF": 0.02,
}
POSITION_XA_PRIORS: dict[str, float] = {
    "CF_lone": 0.08, "CF_pair": 0.09, "SS": 0.14,
    "winger": 0.18, "AM": 0.18, "CM": 0.10, "DM": 0.05,
    "WB": 0.08, "FB": 0.06, "CB": 0.02, "GK": 0.00,
    # Legacy fallback
    "FW": 0.10, "MF": 0.15, "DF": 0.03,
}

# Etiquettes historiques des competitions de selections, derivees des identifiants
# Atlas (compatibilite). Le code compare `fixture.competition_api_id` via
# `is_international`, jamais ces etiquettes.
INTERNATIONAL_LEAGUES: frozenset[str] = frozenset(
    league_key_for(cid) for cid in SELECTION_COMPETITION_IDS
)

# CDM tournament stats blend (WC2026 — integrates in-tournament match-by-match data)
WC2026_LEAGUE_API_ID: int = WC2026_COMPETITION_API_ID
_TOURNAMENT_CLUB_WEIGHT: float = 0.40    # club season stats weight
_TOURNAMENT_CDM_WEIGHT: float = 0.60     # CDM in-tournament stats weight
_TOURNAMENT_GOALS_XG_ALPHA: float = 0.60 # goal-rate vs xG blend within CDM stats
_TOURNAMENT_MIN_MINUTES: int = 45        # min CDM minutes to apply top-down share blend
# Direct lambda blend: after top-down allocation, blend in CDM goals/90 as a direct signal.
# Bypasses the team-xG distribution ceiling that structurally limits individual lambdas.
_WC_DIRECT_LAMBDA_WEIGHT: float = 0.35  # weight on CDM direct lambda (vs top-down)
_WC_DIRECT_LAMBDA_MIN_MINUTES: int = 135  # min CDM minutes to apply direct blend (~1.5 matches)

# ── Matchup factor ─────────────────────────────────────────────────
# ── Position normalisation ────────────────────────────────────────

def _bzz_pos_to_raw(bzz_pos: str | None) -> str | None:
    """Map Bzzoiro single-char position codes to pricing engine format."""
    return {"G": "GK", "D": "DF", "M": "MF", "F": "FW"}.get(bzz_pos or "", bzz_pos)


def _name_key(name: str | None) -> str:
    """Accent-insensitive lowercase key for player deduplication.

    Strips diacritics so 'José' and 'Jose' collide to the same key.
    """
    normalized = unicodedata.normalize("NFD", (name or "").strip().lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


_NORM_POS_11_BUCKET: dict[str, str] = {
    "CF_LONE": "CF_lone", "CF_PAIR": "CF_pair", "SS": "SS", "WINGER": "winger",
    "AM": "AM", "CM": "CM", "DM": "DM", "WB": "WB", "FB": "FB", "CB": "CB", "GK": "GK",
}


def _norm_pos(raw: str | None) -> str | None:
    """Map Bzzoiro position codes to 11-bucket system.

    Buckets: CF_lone, CF_pair, SS, winger, AM, CM, DM, WB, FB, CB, GK
    Falls back to legacy FW/MF/DF/GK when sub-position info is absent.
    """
    if not raw:
        return None
    p = raw.strip().upper()
    # Explicit 11-bucket pass-through (case-insensitive → canonical casing)
    if p in _NORM_POS_11_BUCKET:
        return _NORM_POS_11_BUCKET[p]
    # GK
    if "GK" in p or p == "G":
        return "GK"
    # Defensive line
    if p in ("CB", "SW"):
        return "CB"
    if p in ("LB", "RB"):
        return "FB"
    if p in ("LWB", "RWB"):
        return "WB"
    # Defensive mid
    if p in ("DM", "CDM", "CM/DM"):
        return "DM"
    # Central mid
    if p in ("CM", "MF", "M"):
        return "CM"
    # Attacking mid / second striker
    if p in ("AM", "CAM"):
        return "AM"
    # Wingers
    if p in ("LW", "RW", "LM", "RM", "W"):
        return "winger"
    # Forwards — default to CF_lone when no pairing info
    if p in ("FW", "CF", "ST", "F"):
        return "CF_lone"
    # Broad legacy fallback (Bzzoiro single-char already mapped by _bzz_pos_to_raw)
    if p == "DF":
        return "CB"
    if p == "AM":
        return "AM"
    return None


# ── Dataclasses ───────────────────────────────────────────────────

@dataclass
class TeamStats:
    team: str
    attack_xg_per_match: float
    defense_xga_per_match: float
    finishing: float


@dataclass
class PlayerShare:
    player_id: int
    player_name: str
    team: str
    position: str | None
    npxg_share: float
    xa_share: float
    expected_minutes: float
    matches_played: int
    is_pen_taker: bool = False
    # Part du xG d'equipe reservee au penalty (0 si aucun tireur parmi les
    # postes). Le xG du marche compte deja les penalties : on les retire du
    # jeu ouvert au lieu de les ajouter par-dessus.
    reserve_penalty: float = 0.0
    npxg_per_90: float = 0.0
    xa_per_90: float = 0.0
    xgchain_per_90: float = 0.0
    conversion_rate: float = 1.0
    sot_per_90: float = 0.0
    tap_per_90: float = 0.0
    bcc_per_90: float = 0.0
    accurate_crosses_per_90: float = 0.0
    through_balls_per_90: float = 0.0
    shot_accuracy: float = 0.0
    xg_per_shot: float = 0.0
    avg_rating: float = 0.0          # brut (0-10)
    key_pass_per_90: float = 0.0
    accurate_cross_per_90: float = 0.0
    cross_accuracy: float = 0.0
    npxg_total: float = 0.0
    goals_total: int = 0
    xa_total: float = 0.0
    assists_total: int = 0
    form_xg_5: float | None = None
    form_assists_5: float | None = None
    finishing_delta: float = 0.0
    # Aucun match mesure en xG : rythme tire des vrais buts, et pas de donnees
    # de tir pour juger la finition -> multiplicateur neutre (1.0).
    rythme_sans_xg: bool = False
    has_bzz_stats: bool = False  # True when loaded from bzz_player_season_stats
    # Part du rythme qui vient du joueur lui-meme, le reste venant de l'a priori
    # de son poste. 0 = la cote est celle du poste, 1 = elle est entierement
    # mesuree. Sert a afficher d'ou vient une cote.
    poids_mesure_goal: float = 1.0
    poids_mesure_assist: float = 1.0
    # Saison dont viennent les stats (toutes competitions fusionnees) ;
    # None pour un joueur de l'effectif sans aucune stat.
    stats_season: str | None = None


@dataclass
class PlayerAllocation:
    player_id: int
    player_name: str
    team: str
    position: str | None
    expected_minutes: float
    is_pen_taker: bool
    npxg_share: float
    xa_share: float
    quality_multiplier: float
    lambda_open_play: float
    lambda_penalty: float
    lambda_total: float
    prob_goal: float
    fair_odds_goal: float
    creation_multiplier: float
    lambda_assist: float
    prob_assist: float
    fair_odds_assist: float
    matches_played: int = 0
    has_form_goal: bool = False
    has_form_assist: bool = False
    # Y a-t-il de la matiere pour coter ce joueur ? Faux quand sa part du xG
    # d'equipe est nulle : aucune statistique exploitable. Dans ce cas les
    # probabilites et les cotes valent 0 — une absence, pas un prix.
    has_signal_goal: bool = True
    has_signal_assist: bool = True
    # Part du rythme venant du joueur, le reste de l'a priori de son poste.
    # Permet d'afficher si une cote est mesuree, melangee, ou issue du poste.
    poids_mesure_goal: float = 1.0
    poids_mesure_assist: float = 1.0
    # Saison dont viennent les stats du joueur (None = aucune stat).
    stats_season: str | None = None
    # Supersub fields
    p_goal_supersub: float = 0.0
    fair_odds_goal_supersub: float = 99.0
    p_assist_supersub: float = 0.0
    fair_odds_assist_supersub: float = 99.0
    p_sub: float = 0.35
    avg_sub_time: float = 65.0
    sub_premium_goal: float = 0.0
    sub_premium_assist: float = 0.0


@dataclass
class MatchPricingResult:
    fixture_id: int
    home_team: str
    away_team: str
    home_match_xg: float
    away_match_xg: float
    xg_source: str = "market_implied"
    home_players: list[PlayerAllocation] = field(default_factory=list)
    away_players: list[PlayerAllocation] = field(default_factory=list)
    # Optional redistributed pricing when caller supplies a starter list
    home_lineup_players: list[PlayerAllocation] | None = None
    away_lineup_players: list[PlayerAllocation] | None = None
    # Signalements de la compo (titulaire inconnu, trop peu de titulaires)
    home_lineup_warnings: list[str] = field(default_factory=list)
    away_lineup_warnings: list[str] = field(default_factory=list)
    last_scraped_at: datetime | None = None




def estimate_team_match_xg(
    attack_xg: float,
    opponent_xga: float,
    league_avg_xg: float,
    league_avg_xga: float,
    is_home: bool,
) -> float:
    """Dixon-Coles style match xG estimate (unchanged)."""
    attack_ratio = attack_xg / league_avg_xg if league_avg_xg > 0 else 1.0
    def_ratio = opponent_xga / league_avg_xga if (league_avg_xga > 0 and opponent_xga > 0) else 1.0
    home_factor = HOME_ADVANTAGE if is_home else 1.0
    return max(0.3, min(4.0, attack_ratio * def_ratio * league_avg_xg * home_factor))


# ── Guard-rail: assess_inputs ────────────────────────────────────

@dataclass
class InputWarning:
    code: str
    player_id: int | None
    player_name: str | None
    detail: str


def assess_inputs(players: list[dict[str, Any]], team: str) -> list[InputWarning]:
    """Validate player data before pricing. Returns warnings; never raises.

    Checks:
    - matches_played=1 with minutes_played>180 (data corruption)
    - npxg_per_90 > 1.5 (implausible; likely ingestion error)
    - no players found (empty squad)
    - avg_minutes_per_match > 90 (impossible per-match average)
    """
    warnings: list[InputWarning] = []
    if not players:
        warnings.append(InputWarning("NO_PLAYERS", None, None, f"team={team}: aucun joueur — équipe non reliée à l'Atlas ou effectif vide"))
        return warnings
    for p in players:
        pid = p.get("player_id")
        pname = p.get("player_name", "?")
        matches = p.get("matches_played") or 0
        mins = p.get("minutes_played") or 0
        avg_mins = p.get("avg_minutes_per_match") or 0.0
        npxg = p.get("npxg_per_90") or p.get("xg_per_90") or 0.0

        if matches == 1 and mins > 180:
            warnings.append(InputWarning(
                "CORRUPT_MATCHES_PLAYED", pid, pname,
                f"matches_played=1 but minutes_played={mins} (probable aggregation error)"
            ))
        if npxg > 1.5:
            warnings.append(InputWarning(
                "IMPLAUSIBLE_NPXG", pid, pname,
                f"npxg_per_90={npxg:.3f} > 1.5 — likely ingestion error"
            ))
        if avg_mins > 90.0:
            warnings.append(InputWarning(
                "IMPOSSIBLE_AVG_MINS", pid, pname,
                f"avg_minutes_per_match={avg_mins:.1f} > 90"
            ))
    return warnings


# ── Stage 2: Player shares ────────────────────────────────────────

# ── Rythme de base par poste (regression vers l'a priori) ─────────
#
# Un rythme par 90 calcule sur quelques minutes ne veut rien dire, et la cote
# supersub l'amplifie en divisant par (minutes_attendues / 90) : pour un joueur
# a 1,6 minute, cela multiplie par 56. Constate en production le 06/11/2026 --
# Federico Gatti, defenseur, 1,6 minute jouee, cote juste a 1.04, soit 96 % de
# chances de marquer. 1 206 recommandations actives reposaient sur moins de
# 20 minutes attendues.
#
# On ramene donc le rythme mesure vers celui de son poste, mais UNIQUEMENT
# tant que le joueur n'a pas atteint le seuil de minutes qui rend son propre
# rythme exploitable :
#
#     poids = min(1, minutes / SEUIL_MINUTES)
#     rythme = poids * mesure + (1 - poids) * a_priori_du_poste
#
# Au-dela du seuil le poids vaut 1 : le rythme du joueur est pris tel quel,
# intact. Un joueur etabli n'est JAMAIS tire vers son poste -- c'est ce qui
# distingue cette regression d'une penalite de poste, que le depot a
# explicitement rejetee par le passe (voir test_defender_calibration.py).
#
# Sous le seuil, le poids monte lineairement de 0 a 1, donc aucune marche a la
# frontiere : a 449 minutes le joueur pese 99,8 %, a 450 il pese 100 %.
#
#     0 minute     ->   0 % joueur, 100 % poste   (cote de base pure)
#   225 minutes    ->  50 % / 50 %
#   450 minutes    -> 100 % joueur                (et au-dela, inchange)
#
# Effet sur les cas reels du 06/11/2026 : Federico Gatti, defenseur, 1,6
# minute jouee, passe d'une cote juste de 1.04 -- soit 96 % de chances de
# marquer -- a la cote de son poste. Son rythme surbooste pese 0,36 %.
#
# L'a priori est un CENTRE DE GRAVITE sous le seuil, jamais un plancher : un
# attaquant reellement faible s'en eloigne vers le bas des qu'il joue. Un
# plancher produirait de la fausse value sur tous les mauvais attaquants.
#
# Valeurs mesurees le 06/11/2026 sur bzz_player_season_stats, saison en cours,
# joueurs a 450 minutes ou plus, cinq grands championnats et C1 (3 431
# joueurs). Granularite F/M/D : c'est ce que la mesure couvre, les 11 buckets
# fins sont rabattus sur leur famille faute de mesure par bucket.
#
# SEUIL_MINUTES et les a priori restent a calibrer au score de Brier -- voir
# docs/superpowers/specs/2026-11-06-cote-par-defaut-a-priori-poste-design.md
SEUIL_MINUTES: float = 450.0

# Joueur sans aucun match ou le xG est mesure : son rythme vient de ses vrais
# buts / passes, rappeles vers son repli (carriere, sinon poste) comme
# 15 matchs d'a priori. Calibre le 27/11/2026 hors echantillon (2 929 joueurs,
# saison derniere -> saison en cours) : K=15 bat 0/3/5/10/20/30 et fait meme
# mieux que le xG brut (erreur 0,112 contre 0,118).
RAPPEL_SANS_XG_MATCHS: float = 15.0

POSITION_PRIOR_XG: dict[str, float] = {"FW": 0.347, "MF": 0.132, "DF": 0.056, "GK": 0.035}
POSITION_PRIOR_XA: dict[str, float] = {"FW": 0.115, "MF": 0.126, "DF": 0.059, "GK": 0.006}

# Poste inconnu : moyenne mesuree sur les joueurs sans position renseignee.
_PRIOR_XG_FALLBACK: float = 0.122
_PRIOR_XA_FALLBACK: float = 0.056

# Les 11 buckets fins vers les trois familles mesurees.
_PRIOR_FAMILY: dict[str, str] = {
    "CF_lone": "FW", "CF_pair": "FW", "SS": "FW", "winger": "FW", "FW": "FW",
    "AM": "MF", "CM": "MF", "DM": "MF", "MF": "MF",
    "WB": "DF", "FB": "DF", "CB": "DF", "DF": "DF",
    "GK": "GK",
}


def position_prior(position: str | None, priors: dict[str, float], fallback: float) -> float:
    """A priori de rythme pour ce poste, ou la moyenne generale si inconnu."""
    famille = _PRIOR_FAMILY.get(position or "")
    if famille is None:
        return fallback
    return priors.get(famille, fallback)


def regress_to_position(
    rate: float, minutes: float, position: str | None,
    priors: dict[str, float], fallback: float,
    carriere: float | None = None,
) -> tuple[float, float]:
    """(rythme ramene vers son repli, poids du joueur entre 0 et 1).

    Cascade a trois niveaux :

      1. minutes >= SEUIL_MINUTES  -> le rythme mesure, INTACT (poids 1)
      2. sous le seuil, carriere connue -> repli sur la carriere
      3. sous le seuil, rien        -> repli sur l'a priori du poste

    Le niveau 2 compte : 2 406 joueurs cotes sur 3 467 sous le seuil ont une
    carriere importee (mesure du 06/11/2026). Les envoyer directement sur la
    moyenne de leur poste jetterait ce qu'on sait d'eux -- un attaquant
    transfere avec deux matchs au compteur recevrait "attaquant moyen" au lieu
    de son propre historique.

    Le poids est rendu pour que l'appelant puisse dire d'ou vient la cote.
    """
    if minutes >= SEUIL_MINUTES:
        return rate, 1.0
    repli = carriere if carriere is not None else position_prior(position, priors, fallback)
    if minutes <= 0:
        return repli, 0.0
    poids = minutes / SEUIL_MINUTES
    return poids * rate + (1.0 - poids) * repli, poids


def _rappel_sans_xg(
    taux_reel: float, minutes: float, position: str | None,
    priors: dict[str, float], fallback: float, carriere: float | None,
    part_non_mesuree: float = 1.0,
) -> tuple[float, float]:
    """Rythme d'un joueur sans aucun match mesure en xG : ses vrais buts (ou
    passes) par 90, rappeles vers sa carriere (sinon son poste) comme
    RAPPEL_SANS_XG_MATCHS matchs d'a priori. Evite les deux exces : 0 but en
    6 matchs ne vaut pas « jamais buteur », 2 buts en 3 matchs pas « 0,67/90 »."""
    repli = carriere if carriere is not None else position_prior(position, priors, fallback)
    n90 = max(minutes, 0.0) / 90.0
    # Le rappel ne porte que sur la part de la saison jouee sans xG (Lukic :
    # 91 % non mesure -> ~14 matchs de rappel ; saison toute mesuree -> 0).
    k = RAPPEL_SANS_XG_MATCHS * max(0.0, min(1.0, part_non_mesuree))
    if n90 + k <= 0:
        return repli, 0.0
    poids = n90 / (n90 + k)
    return poids * taux_reel + (1.0 - poids) * repli, poids


async def _enrichir_rythme_carriere(db: AsyncSession, players: list[dict[str, Any]]) -> None:
    """Pose `carriere_xg_per_90` / `carriere_xa_per_90` sur chaque dict joueur.

    Modifie les dicts sur place. Un joueur sans carriere exploitable reste
    sans ces cles : la cascade retombe alors sur l'a priori de son poste.

    Toute defaillance est avalee : une carriere absente degrade la precision,
    elle ne doit pas empecher de coter un match.
    """
    ids = [p["player_id"] for p in players if p.get("player_id")]
    if not ids:
        return
    try:
        from app.pricing.career_blend import blended_rhythms_for

        rythmes = await blended_rhythms_for(db, ids)
    except Exception as exc:
        logger.warning(
            "Rythme carriere indisponible, repli sur l'a priori de poste — %s: %s",
            type(exc).__name__, exc,
        )
        return

    for p in players:
        r = rythmes.get(p.get("player_id"))
        if r is None:
            continue
        p["carriere_xg_per_90"] = r.goal_rate_per_90
        p["carriere_xa_per_90"] = r.assist_rate_per_90


def onze_probable(players: list[dict[str, Any]]) -> set[int]:
    """Les 11 postes d'un effectif sans compo connue : le gardien et les 10
    joueurs de champ les plus souvent titulaires (a egalite : le plus de temps
    de jeu, puis le plus de matchs).

    Sert uniquement a choisir QUI occupe les postes, jamais a reduire une part.
    """
    def cle(p: dict[str, Any]) -> tuple:
        return (
            p.get("starts") or 0,
            p.get("minutes_played") or 0,
            p.get("matches_played") or 0,
            -(p.get("player_id") or 0),
        )

    gardiens = sorted((p for p in players if p.get("position") == "GK"), key=cle, reverse=True)
    champ = sorted((p for p in players if p.get("position") != "GK"), key=cle, reverse=True)
    onze = gardiens[:1] + champ[: 11 - min(1, len(gardiens))]
    return {p["player_id"] for p in onze}


def _goal_stats(share: PlayerShare) -> dict[str, Any]:
    return {
        "shot_accuracy": share.shot_accuracy,
        "xg_per_shot": share.xg_per_shot,
        "avg_rating": share.avg_rating,        # brut (0-10)
        "matches_played": share.matches_played,
        "npxg_total": share.npxg_total,
        "goals": share.goals_total,
    }


def _assist_stats(share: PlayerShare) -> dict[str, Any]:
    return {
        "xa_per_90": share.xa_per_90,
        "key_pass_per_90": share.key_pass_per_90,
        "accurate_cross_per_90": share.accurate_cross_per_90,
        "cross_accuracy": share.cross_accuracy,
        "matches_played": share.matches_played,
        "xa_total": share.xa_total,
        "assists": share.assists_total,
    }


def _multiplicateur_but(share: PlayerShare) -> float:
    # Sans match mesure, ni tirs ni xG par tir : une donnee absente n'est pas
    # une mauvaise finition (sinon plancher x0,5 -> defenseurs cotes 150).
    if share.rythme_sans_xg:
        return 1.0
    return calculate_finishing_multiplier(_goal_stats(share), share.position)


def compute_player_shares(
    players: list[dict[str, Any]],
    team: str,
    lambda_team: float,
    xi_ids: set[int] | list[int] | None = None,
) -> list[PlayerShare]:
    """Parts de chaque joueur, raisonnees PAR POSTE SUR 90 MINUTES.

    Le pari de reference est « avec remplacant » : il couvre le poste pendant
    tout le match. Le poids d'un joueur est donc son rythme par 90 minutes
    (melange saison / forme, ramene vers carriere ou poste si l'echantillon
    est mince) — jamais multiplie par ses minutes habituelles.

    Les parts sont normalisees sur les 11 postes (`xi_ids` : la compo ; par
    defaut le onze probable) de sorte que, multiplicateurs de finition et de
    creation compris, les 11 postes totalisent EXACTEMENT le xG de l'equipe
    et le budget de passes decisives. Un joueur hors des 11 recoit la part
    qu'il aurait s'il occupait un poste (meme denominateur).

    Correction du 26/11/2026 : l'ancienne regle reduisait la part par les
    minutes puis regonflait chaque joueur a 90 minutes pour la cote avec
    remplacant — 1,66 but distribue pour 1,40 attendu (Chelsea) et jusqu'a
    +78 % de passes decisives.
    """
    # Pass 1 — compute blended weights per player
    entries = []
    for p in players:
        matches = p.get("matches_played", 0) or 0
        mins = p.get("minutes_played", 0) or 0
        avg_mins = p.get("avg_minutes_per_match") or ((mins / matches) if matches > 0 else 75.0)
        exp_mins = min(90.0, max(1.0, avg_mins))

        # Goal weight — blend season xg_per_90 + form_xg_5 (position-specific form weight)
        pos = p.get("position")
        form_w = FORM_WEIGHTS_BY_POSITION.get(pos or "", _FORM_WEIGHT_DEFAULT)
        xg_per_90 = p.get("npxg_per_90") or p.get("xg_per_90") or 0.0
        form_xg = p.get("form_xg_5")
        if form_xg is not None and exp_mins > 0:
            form_rate = form_xg / (5.0 * exp_mins / 90.0)
            blended_xg = (1 - form_w) * xg_per_90 + form_w * form_rate
        else:
            blended_xg = xg_per_90
        # Regression vers le poste APRES le melange de forme : form_rate divise
        # aussi par les minutes attendues, donc il souffre de la meme
        # amplification. Une seule intervention couvre les deux.
        # Part de la saison jouee dans des matchs sans xG mesure : son rythme y
        # vient des vrais buts / passes (voir _player_dict_from_merged).
        part_nm = p.get("part_non_mesuree")
        if part_nm is None:
            part_nm = 1.0 if p.get("rythme_sans_xg") else 0.0
        sans_xg = part_nm > 0.0
        mins_xg = p.get("minutes_xg") if p.get("minutes_xg") is not None else mins
        if sans_xg:
            blended_xg, poids_xg = _rappel_sans_xg(
                xg_per_90, mins, pos, POSITION_PRIOR_XG, _PRIOR_XG_FALLBACK, p.get("carriere_xg_per_90"), part_nm)
        else:
            blended_xg, poids_xg = regress_to_position(
                blended_xg, mins_xg, pos, POSITION_PRIOR_XG, _PRIOR_XG_FALLBACK,
                carriere=p.get("carriere_xg_per_90"),
            )
        goal_weight = blended_xg

        # Assist weight — blend season xa_per_90 + form_assists_5
        xa_per_90 = p.get("xa_per_90") or 0.0
        form_xa = p.get("form_assists_5")
        if form_xa is not None and exp_mins > 0:
            form_xa_rate = form_xa / (5.0 * exp_mins / 90.0)
            blended_xa = (1 - form_w) * xa_per_90 + form_w * form_xa_rate
        else:
            blended_xa = xa_per_90
        if sans_xg:
            blended_xa, poids_xa = _rappel_sans_xg(
                xa_per_90, mins, pos, POSITION_PRIOR_XA, _PRIOR_XA_FALLBACK, p.get("carriere_xa_per_90"), part_nm)
        else:
            blended_xa, poids_xa = regress_to_position(
                blended_xa, mins_xg, pos, POSITION_PRIOR_XA, _PRIOR_XA_FALLBACK,
                carriere=p.get("carriere_xa_per_90"),
            )
        # Regle du 27/11/2026 (Alpha ET Beta) : le rythme passeur est celui de
        # la carriere — saison en cours comprise et la plus lourde, chaque
        # saison melant xA et passes reellement donnees (career_blend). Teste
        # hors echantillon : il bat le xA de la saison seule, surtout pour les
        # avant-centres que le xA sous-estimait de moitie. Sans carriere
        # exploitable, repli sur la saison ramenee vers l'a priori du poste.
        carriere_xa = p.get("carriere_xa_per_90")
        if carriere_xa is not None:
            blended_xa, poids_xa = carriere_xa, 1.0
        assist_weight = blended_xa

        entries.append((p, exp_mins, goal_weight, assist_weight, poids_xg, poids_xa))

    # Pass 2 — PlayerShare objects (parts brutes = poids, normalisees ensuite)
    shares = []
    for p, exp_mins, goal_weight, assist_weight, poids_xg, poids_xa in entries:
        pos = p.get("position")
        matches = p.get("matches_played", 0) or 0
        npxg_total = p.get("npxg_total") or p.get("npxg") or 0.0
        goals_total = p.get("goals_total") or p.get("goals") or 0

        shares.append(PlayerShare(
            player_id=p["player_id"],
            player_name=p["player_name"],
            team=team,
            position=pos,
            npxg_share=goal_weight,
            xa_share=assist_weight,
            expected_minutes=exp_mins,
            matches_played=matches,
            npxg_per_90=p.get("npxg_per_90") or p.get("xg_per_90") or 0.0,
            xa_per_90=p.get("xa_per_90") or 0.0,
            xgchain_per_90=p.get("xgchain_per_90") or 0.0,
            conversion_rate=1.0,  # computed in allocate_player
            sot_per_90=p.get("shots_on_target_per_90") or 0.0,
            tap_per_90=p.get("touches_attack_pen_area_per_90") or 0.0,
            bcc_per_90=p.get("bcc_per_90") or 0.0,
            accurate_crosses_per_90=p.get("accurate_crosses_per_90") or 0.0,
            through_balls_per_90=p.get("through_balls_per_90") or 0.0,
            shot_accuracy=p.get("shot_accuracy") or 0.0,
            xg_per_shot=p.get("xg_per_shot") or 0.0,
            avg_rating=p.get("avg_rating") or 0.0,      # brut (0-10)
            cross_accuracy=p.get("cross_accuracy") or 0.0,
            npxg_total=npxg_total,
            goals_total=goals_total,
            xa_total=p.get("xa_total") or 0.0,
            assists_total=p.get("assists_total") or 0,
            key_pass_per_90=p.get("key_pass_per_90") or 0.0,
            accurate_cross_per_90=p.get("accurate_cross_per_90") or 0.0,
            form_xg_5=p.get("form_xg_5"),
            form_assists_5=p.get("form_assists_5"),
            finishing_delta=p.get("finishing_delta") or 0.0,
            rythme_sans_xg=bool(p.get("rythme_sans_xg")),
            has_bzz_stats=p.get("has_bzz_stats", False),
            poids_mesure_goal=round(poids_xg, 4),
            poids_mesure_assist=round(poids_xa, 4),
            stats_season=p.get("stats_season"),
        ))

    # Pass 3 — normalisation sur les 11 postes, multiplicateurs compris
    postes = {int(i) for i in xi_ids} if xi_ids is not None else onze_probable(players)
    xi = [s for s in shares if s.player_id in postes]
    poids_but = {s.player_id: s.npxg_share * _multiplicateur_but(s) for s in shares}
    poids_passe = {s.player_id: s.xa_share for s in shares}
    denom_but = sum(poids_but[s.player_id] for s in xi)
    denom_passe = sum(poids_passe[s.player_id] for s in xi)
    # Un joueur hors des 11 est cote comme s'il PRENAIT LA PLACE d'un titulaire
    # (celui de son poste qui pese le moins) : il entre dans le denominateur et
    # le remplace s'en retire, donc les 11 postes totalisent toujours le xG de
    # l'equipe. Correction du 27/11/2026 : diviser par les 11 SANS lui donnait a
    # un remplacant efficace plus que toute l'equipe (Cano 1,15 buteur, Etta
    # Eyong 1,31, recos a +200 % d'edge).
    def remplace(s: PlayerShare) -> PlayerShare | None:
        if not xi:
            return None
        meme_poste = [t for t in xi if t.position == s.position]
        meme_famille = [t for t in xi if _famille(t.position) == _famille(s.position)]
        candidats = meme_poste or meme_famille or [t for t in xi if t.position != "GK"] or xi
        return min(candidats, key=lambda t: poids_but[t.player_id])
    for s in shares:
        d_but, d_passe = denom_but, denom_passe
        if s.player_id not in postes:
            r = remplace(s)
            if r is not None:
                d_but += poids_but[s.player_id] - poids_but[r.player_id]
                d_passe += poids_passe[s.player_id] - poids_passe[r.player_id]
        s.npxg_share = s.npxg_share / d_but if d_but > 0 else 0.0
        s.xa_share = s.xa_share / d_passe if d_passe > 0 else 0.0
    return shares


def _famille(position: str | None) -> str:
    """Famille de poste (GK / DEF / MIL / ATT) pour choisir qui un remplacant remplace."""
    if position == "GK":
        return "GK"
    if position in {"CB", "FB", "WB", "DF"}:
        return "DEF"
    if position in {"CF_lone", "CF_pair", "SS", "FW", "winger"}:
        return "ATT"
    return "MIL"


def marquer_tireur(shares: list[PlayerShare], pen_id: int | None, postes: set[int] | None = None) -> None:
    """Designe le tireur de penalty et reserve sa part dans le xG d'equipe.

    La reserve n'est prise que si le tireur occupe un des postes (`postes`,
    tous les joueurs fournis si None) : sinon personne sur le terrain ne tire.
    """
    ids = postes if postes is not None else {s.player_id for s in shares}
    reserve = PEN_CONVERSION * PENS_PER_MATCH if pen_id is not None and pen_id in ids else 0.0
    for s in shares:
        s.is_pen_taker = s.player_id == pen_id
        s.reserve_penalty = reserve


def detect_penalty_taker(players: list[dict[str, Any]]) -> int | None:
    """Auto-detect penalty taker: highest xG − npxG (= penalty xG).

    Returns None when no player has npxG data (e.g. international matches where
    Bzzoiro doesn't provide xG/npxG split) to avoid spurious detection.
    Requires pen_xg > 0.05 to filter noise from data rounding errors.
    """
    has_any_npxg = any((p.get("npxg") or 0.0) > 0.0 for p in players)
    if not has_any_npxg:
        return None

    best_id: int | None = None
    best_pen_xg = 0.05  # minimum threshold — avoids noise from rounding
    for p in players:
        pen_xg = (p.get("xg", 0.0) or 0.0) - (p.get("npxg", 0.0) or 0.0)
        if pen_xg > best_pen_xg:
            best_pen_xg = pen_xg
            best_id = p["player_id"]
    return best_id


# ── Stage 3: Player allocation with Model C ───────────────────────

def allocate_player(
    share: PlayerShare,
    team_match_xg: float,
    is_pen_taker: bool,
    budget_assists: float,
    league_avg_goalscorer: dict[str, float] | None = None,
    league_avg_assist: dict[str, float] | None = None,
    p_sub: float = 0.35,
    avg_sub_time: float = 65.0,
) -> PlayerAllocation:
    """Lambdas de Poisson du POSTE sur 90 minutes (cote avec remplacant),
    puis du joueur seul sur ses minutes habituelles (cote sans remplacant).

    Les parts viennent de compute_player_shares, deja normalisees pour que
    les 11 postes totalisent le xG d'equipe (penalty reserve compris) et le
    budget de passes decisives.
    """
    mins_ratio = share.expected_minutes / 90.0

    # ── Goalscorer ────────────────────────────────────────────────
    finishing_mult = _multiplicateur_but(share)
    xg_jeu = max(team_match_xg - share.reserve_penalty, 0.0)
    lambda_open_play = share.npxg_share * xg_jeu * finishing_mult
    lambda_penalty = PEN_CONVERSION * PENS_PER_MATCH if is_pen_taker else 0.0

    # Part nulle du xG d'equipe = aucune statistique exploitable. Le plancher
    # qui suit (0.001) protege d'une division par zero ; il ne doit JAMAIS
    # servir de prix. Normalise a 90 minutes il produisait une cote d'allure
    # serieuse sortie de nulle part : 11.62 buteur, 362 recommandations sur
    # les 30 jours au 06/11/2026 — toutes avec lambda au plancher.
    has_signal_goal = share.npxg_share > 0.0 or is_pen_taker

    # Lambda du poste sur 90 minutes (brut, avant plancher), puis celui du
    # joueur seul sur ses minutes. Le plancher (0.001) protege d'une division
    # par zero ; il n'est jamais mis a l'echelle.
    lambda_poste_brut = lambda_open_play + lambda_penalty
    lambda_total_brut = lambda_poste_brut * mins_ratio
    # Meme regle que le passeur (27/11/2026) : sous le plancher (0.001), pas
    # de cote du tout -- jamais 1000.50 servi comme un prix.
    has_signal_goal_90 = has_signal_goal and lambda_poste_brut >= 0.001
    has_signal_goal = has_signal_goal_90 and lambda_total_brut >= 0.001
    lambda_total = max(0.001, min(lambda_total_brut, 3.0))
    if has_signal_goal:
        prob_goal = 1 - math.exp(-lambda_total)
        fair_odds_goal = round(1 / prob_goal, 2) if prob_goal > 0 else 9999.0
    else:
        # Zero, pas 9999 : les garde-fous avals ecartent une probabilite nulle
        # ou une cote vide, donc ni recommandation ni alerte sur ce joueur.
        prob_goal = 0.0
        fair_odds_goal = 0.0

    # ── Assist ────────────────────────────────────────────────────
    # Plus de multiplicateurs de creation ni de conversion (27/11/2026) : ils
    # recomptaient le xA deja present dans la part et, plafonnes a x1.50 /
    # x1.40 ou planches a x0.70 / x0.75, exageraient les ecarts (Olise 0.89
    # passe/match annoncee pour 0.38 reelle, Isidor 0.01 pour 0.18).
    creation_mult = 1.0
    xa_conversion = 1.0
    # Poste sur 90 minutes (brut), puis joueur seul sur ses minutes ; le
    # plancher passeur (0.01) n'est jamais mis a l'echelle.
    lambda_assist_poste_brut = share.xa_share * budget_assists * creation_mult * xa_conversion
    lambda_assist = calculate_assist_lambda(
        share.xa_share * mins_ratio, budget_assists, creation_mult, xa_conversion,
    )

    # Le plancher passeur (CLAMP_LAMBDA_MIN_ASSIST) protege d'une division par
    # zero ; il ne doit JAMAIS devenir un prix (cote 100.50 servie a des
    # avant-centres que les books proposaient entre 4 et 7). Sous le plancher :
    # pas de cote, donc ni recommandation ni alerte.
    has_signal_assist_90 = share.xa_share > 0.0 and lambda_assist_poste_brut >= CLAMP_LAMBDA_MIN_ASSIST
    has_signal_assist = has_signal_assist_90 and lambda_assist_poste_brut * mins_ratio >= CLAMP_LAMBDA_MIN_ASSIST

    if has_signal_assist:
        prob_assist = 1 - math.exp(-lambda_assist)
        fair_odds_assist = round(1 / prob_assist, 2) if prob_assist > 0 else 9999.0
    else:
        prob_assist = 0.0
        fair_odds_assist = 0.0

    # ── Avec remplacant (le poste sur 90 minutes) ────────────────
    # C'est la cote de reference : le pari couvre le poste tout le match.
    # Elle vient directement de la part du poste, sans aucune division par
    # les minutes (l'ancienne division gonflait chaque joueur a 90 minutes
    # apres avoir reduit sa part : le total depassait le xG d'equipe).
    lambda_total_90 = max(0.001, min(lambda_poste_brut, 3.0))
    lambda_assist_90 = max(CLAMP_LAMBDA_MIN_ASSIST, min(lambda_assist_poste_brut, 2.0))

    if has_signal_goal_90:
        p_goal_ss = 1.0 - math.exp(-lambda_total_90)
        fair_odds_goal_ss = round(1.0 / p_goal_ss, 2) if p_goal_ss > 0 else 99.0
    else:
        p_goal_ss = 0.0
        fair_odds_goal_ss = 0.0

    if has_signal_assist_90:
        p_assist_ss = 1.0 - math.exp(-lambda_assist_90)
        fair_odds_assist_ss = round(1.0 / p_assist_ss, 2) if p_assist_ss > 0 else 99.0
    else:
        p_assist_ss = 0.0
        fair_odds_assist_ss = 0.0

    sub_premium_goal   = round(p_goal_ss   - prob_goal,   4)
    sub_premium_assist = round(p_assist_ss - prob_assist, 4)

    return PlayerAllocation(
        player_id=share.player_id,
        player_name=share.player_name,
        team=share.team,
        position=share.position,
        expected_minutes=round(share.expected_minutes, 1),
        is_pen_taker=is_pen_taker,
        npxg_share=round(share.npxg_share, 4),
        xa_share=round(share.xa_share, 4),
        quality_multiplier=round(finishing_mult, 4),
        lambda_open_play=round(lambda_open_play, 4),
        lambda_penalty=round(lambda_penalty, 4),
        lambda_total=round(lambda_total, 4),
        prob_goal=round(prob_goal, 4),
        fair_odds_goal=fair_odds_goal,
        creation_multiplier=round(creation_mult, 4),
        lambda_assist=round(lambda_assist, 4),
        prob_assist=round(prob_assist, 4),
        fair_odds_assist=fair_odds_assist,
        matches_played=share.matches_played,
        has_form_goal=share.form_xg_5 is not None,
        has_form_assist=share.form_assists_5 is not None,
        has_signal_goal=has_signal_goal,
        has_signal_assist=has_signal_assist,
        poids_mesure_goal=share.poids_mesure_goal,
        poids_mesure_assist=share.poids_mesure_assist,
        stats_season=share.stats_season,
        p_goal_supersub=round(p_goal_ss, 4),
        fair_odds_goal_supersub=fair_odds_goal_ss,
        p_assist_supersub=round(p_assist_ss, 4),
        fair_odds_assist_supersub=fair_odds_assist_ss,
        p_sub=round(p_sub, 4),
        avg_sub_time=round(avg_sub_time, 1),
        sub_premium_goal=sub_premium_goal,
        sub_premium_assist=sub_premium_assist,
    )


# ── DB helpers ────────────────────────────────────────────────────

# Ratios stockes par l'agregation (aggregate.py) comme MOYENNE des ratios par
# match, pas comme ratio des totaux. Les multiplicateurs finition / creation et
# leurs references par poste sont regles sur cette definition : la fusion
# multi-competitions doit la conserver (methode Alpha inchangee).
_RATIOS_MOYENNE_PAR_MATCH: tuple[str, ...] = ("shot_accuracy", "xg_per_shot", "cross_accuracy")


def _stored_ratio_weighted(rows: list[Any], attr: str) -> float | None:
    """Moyenne des valeurs STOCKEES de `attr`, ponderee par matches_played.

    Une seule ligne renseignee -> sa valeur exacte. Lignes a None ignorees ;
    tout a None -> None (le dict applique alors le repli 0.0 d'aujourd'hui).
    """
    pairs = [(getattr(r, attr), getattr(r, "matches_played", None) or 0)
             for r in rows if getattr(r, attr, None) is not None]
    if not pairs:
        return None
    if len(pairs) == 1:
        return pairs[0][0]
    total_w = sum(w for _, w in pairs)
    if total_w == 0:
        return sum(v for v, _ in pairs) / len(pairs)
    return sum(v * w for v, w in pairs) / total_w


def _player_dict_from_merged(
    player: Any, merged: dict[str, Any], season: str, rows: list[Any],
) -> dict[str, Any]:
    """Dict joueur du calculateur depuis la fusion de TOUTES ses competitions d'une saison.

    `rows` = les lignes bzz_player_season_stats fusionnees : shot_accuracy,
    xg_per_shot et cross_accuracy en viennent (moyenne ponderee des valeurs
    stockees, meme definition qu'avant), pas des ratios de totaux de `merged`.

    `merged` = sortie de `merge_season_stats` (meme source que la partie Joueurs).
    Les cles sont exactement celles lues par compute_player_shares /
    allocate_player / _blend_tournament_stats / beta_pricing, plus
    `stats_season`. Une stat absente de toutes les lignes reste 0 / None
    comme avant : jamais de valeur inventee.
    """
    def _num(key: str, default: float | int = 0.0) -> Any:
        v = merged.get(key)
        return v if v else default

    name = player.name
    xg_total = _num("expected_goals")
    goals_total = _num("goals", 0)
    xa_total = _num("expected_assists")
    xg_per_90 = _num("xg_per_90")
    xa_per_90 = _num("xa_per_90")
    # Aucun match ou Bzzoiro mesure le xG (championnat non couvert, petite
    # selection) : on prend ses vrais buts / passes par 90 plutot qu'un faux 0,
    # rappeles vers le poste dans compute_player_shares (RAPPEL_SANS_XG_MATCHS).
    minutes = merged.get("minutes_played") or 0
    minutes_xg = merged.get("minutes_xg")
    m_xg = minutes if minutes_xg is None else minutes_xg
    rythme_sans_xg = minutes > 0 and (merged.get("xg_per_90") is None or m_xg <= 0)
    part_non_mesuree = (minutes - m_xg) / minutes if minutes > 0 and m_xg < minutes else 0.0
    if part_non_mesuree > 0:
        # Production du joueur sur TOUTE la saison : xG/xA la ou ils sont
        # mesures, vrais buts/passes ailleurs (Lukic : 1 match mesure, 8 buts
        # dans les autres -> ne plus l'evaluer sur ce seul match).
        buts_hors = max(0, (merged.get("goals") or 0) - (merged.get("goals_xg") or 0))
        passes_hors = max(0, (merged.get("goal_assist") or 0) - (merged.get("assists_xg") or 0))
        xg_per_90 = 90.0 * ((merged.get("expected_goals") or 0.0) + buts_hors) / minutes
        xa_per_90 = 90.0 * ((merged.get("expected_assists") or 0.0) + passes_hors) / minutes
    accurate_cross_per_90 = _num("accurate_cross_per_90")
    form_xg_5 = merged.get("form_xg_5")
    ratios = {k: _stored_ratio_weighted(rows, k) or 0.0 for k in _RATIOS_MOYENNE_PAR_MATCH}
    return {
        "player_id": player.api_id,
        "player_name": name,
        "name": name,
        "position": _norm_pos(_bzz_pos_to_raw(player.position)),
        "matches_played": _num("matches_played", 0),
        "minutes_played": _num("minutes_played", 0),
        "starts": _num("starts", 0),
        # Shared numeric fields (mapped to existing compute_player_shares keys)
        "goals": goals_total,
        "xg": xg_total,
        "npxg": xg_total,  # no penalty split in bzz — use total xG
        "xa": xa_total,
        "npxg_per_90": xg_per_90,
        "xa_per_90": xa_per_90,
        "xgchain_per_90": 0.0,  # not available in bzz
        # Bzzoiro-specific quality/creation fields
        "xg_per_90": xg_per_90,
        "shot_accuracy": ratios["shot_accuracy"],
        "xg_per_shot": ratios["xg_per_shot"],
        "avg_rating": _num("avg_rating"),          # brut (0-10), pondere par matchs
        "cross_accuracy": ratios["cross_accuracy"],
        "xa_total": xa_total,
        "assists_total": _num("goal_assist", 0),
        "form_assists_5": merged.get("form_assists_5"),  # peut être None
        "npxg_total": xg_total,
        "goals_total": goals_total,
        "key_pass_per_90": _num("key_pass_per_90"),
        "accurate_cross_per_90": accurate_cross_per_90,
        "form_xg_5": form_xg_5 if form_xg_5 else None,
        "finishing_delta": goals_total - xg_total,
        # Sofascore fields — not available from bzz; default to 0
        "shots_on_target_per_90": _num("shots_on_target_per_90"),
        "touches_attack_pen_area_per_90": 0.0,
        "bcc_per_90": 0.0,
        "accurate_crosses_per_90": accurate_cross_per_90,
        "through_balls_per_90": 0.0,
        # Flag to indicate bzz-sourced stats (triggers new multiplier formulas)
        "has_bzz_stats": True,
        "stats_season": season,
        # Temps de jeu des matchs ou le xG est mesure : taille d'echantillon du
        # xG/xA (un rythme mesure sur 1 match de coupe n'est pas fiable).
        "minutes_xg": minutes if minutes_xg is None else minutes_xg,
        "rythme_sans_xg": rythme_sans_xg,
        "part_non_mesuree": part_non_mesuree,
    }


def _roster_only_dict(player: Any, position: str | None) -> dict[str, Any]:
    """Joueur de l'effectif sans aucune ligne de stats : zeros, les a priori de poste prennent le relais."""
    return {
        "player_id": player.api_id,
        "player_name": player.name,
        "name": player.name,
        "position": position,
        "matches_played": 0,
        "minutes_played": 0,
        "goals": 0,
        "xg": 0.0,
        "npxg": 0.0,
        "xa": 0.0,
        "npxg_per_90": 0.0,
        "xa_per_90": 0.0,
        "xgchain_per_90": 0.0,
        "xg_per_90": 0.0,
        "shot_accuracy": 0.0,
        "xg_per_shot": 0.0,
        "avg_rating": 0.0,
        "cross_accuracy": 0.0,
        "xa_total": 0.0,
        "assists_total": 0,
        "npxg_total": 0.0,
        "goals_total": 0,
        "key_pass_per_90": 0.0,
        "accurate_cross_per_90": 0.0,
        "form_xg_5": None,
        "form_assists_5": None,
        "finishing_delta": 0.0,
        "shots_on_target_per_90": 0.0,
        "touches_attack_pen_area_per_90": 0.0,
        "bcc_per_90": 0.0,
        "accurate_crosses_per_90": 0.0,
        "through_balls_per_90": 0.0,
        "has_bzz_stats": False,
        "stats_season": None,
    }


async def _load_latest_season_merged(
    db: AsyncSession,
    where: Any,
    join_player_in_subquery: bool = True,
) -> list[tuple[Any, dict[str, Any], str, list[Any]]]:
    """Derniere saison jouee de chaque joueur, TOUTES competitions fusionnees.

    Methode Alpha inchangee (rythme de la derniere saison jouee) ; seule la
    donnee change : championnat + coupes d'Europe + coupes nationales, et
    les lignes sous deux clubs d'un joueur transfere en cours de saison,
    sont additionnees par `merge_season_stats` (meme fusion que la partie
    Joueurs) au lieu de ne garder que la competition la plus jouee.

    Retourne [(BzzPlayer, merged, season, lignes)] trie par player_api_id.
    """
    from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat
    from app.services.atlas_source import merge_season_stats

    latest_q = select(
        BzzPlayerSeasonStat.player_api_id,
        func.max(BzzPlayerSeasonStat.season).label("max_season"),
    )
    if join_player_in_subquery:
        latest_q = latest_q.join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
    latest_subq = (
        latest_q.where(where)
        .group_by(BzzPlayerSeasonStat.player_api_id)
        .subquery()
    )

    stats_q = (
        select(BzzPlayerSeasonStat, BzzPlayer)
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .join(
            latest_subq,
            (BzzPlayerSeasonStat.player_api_id == latest_subq.c.player_api_id)
            & (BzzPlayerSeasonStat.season == latest_subq.c.max_season),
        )
        .where(where)
        .order_by(BzzPlayerSeasonStat.player_api_id, BzzPlayerSeasonStat.league_api_id)
    )

    res = await db.execute(stats_q)
    grouped: dict[int, tuple[Any, list[Any]]] = {}
    for row in res.all():
        stat, player = row[0], row[1]
        grouped.setdefault(player.api_id, (player, []))[1].append(stat)

    out: list[tuple[Any, dict[str, Any], str, list[Any]]] = []
    for player, stats in grouped.values():
        season = stats[0].season
        merged = merge_season_stats(stats, season)
        if merged is not None:
            out.append((player, merged, season, stats))
    return out


def _append_merged_players(
    merged_players: list[tuple[Any, dict[str, Any], str, list[Any]]],
    players: list[dict[str, Any]],
    seen_ids: set[int],
    seen_internal_ids: set[int],
    seen_names: set[str],
) -> None:
    """Ajoute les joueurs fusionnes (gardiens exclus), dedoublonnes par id / internal_id / nom."""
    for player, merged, season, stat_rows in merged_players:
        name_key = _name_key(player.name)
        internal_id = player.internal_id
        if player.api_id in seen_ids:
            continue
        if internal_id is not None and internal_id in seen_internal_ids:
            continue
        if name_key in seen_names:
            continue
        seen_ids.add(player.api_id)
        if internal_id is not None:
            seen_internal_ids.add(internal_id)
        seen_names.add(name_key)

        d = _player_dict_from_merged(player, merged, season, stat_rows)
        if d["position"] == "GK":
            continue
        players.append(d)


def _append_roster_fallback(
    roster: list[Any],
    players: list[dict[str, Any]],
    seen_ids: set[int],
    seen_internal_ids: set[int],
    seen_names: set[str],
) -> None:
    """Joueurs de l'effectif sans stats : zeros, a priori de poste (comportement inchange)."""
    for player in roster:
        if player.api_id in seen_ids:
            continue
        internal_id = player.internal_id
        if internal_id is not None and internal_id in seen_internal_ids:
            continue
        name_key = _name_key(player.name)
        if name_key in seen_names:
            continue

        position = _norm_pos(_bzz_pos_to_raw(player.position))
        if position == "GK":
            continue

        seen_ids.add(player.api_id)
        if internal_id is not None:
            seen_internal_ids.add(internal_id)
        seen_names.add(name_key)
        players.append(_roster_only_dict(player, position))


async def _load_team_players(
    db: AsyncSession,
    team: str,
    league: str | None = None,
    bzz_team_id: int | None = None,
) -> list[dict[str, Any]]:
    """Load player season stats from bzz_player_season_stats for a team.

    Effectif = `club_squad_filter(bzz_team_id)` (source unique Atlas).
    Sans bzz_team_id : liste vide + alerte, jamais de rapprochement par nom.
    Stats = derniere saison jouee, toutes competitions fusionnees.
    """
    from app.models.bzzoiro import BzzPlayer
    from app.services.atlas_source import club_squad_filter

    if bzz_team_id is None:
        logger.warning("_load_team_players: équipe non reliée à l'Atlas: %r", team)
        _alert_once(f"Calculateur : équipe non reliée à l'Atlas — {team}")
        return []

    team_id_filter = club_squad_filter(bzz_team_id)
    merged_players = await _load_latest_season_merged(db, team_id_filter)

    players: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_internal_ids: set[int] = set()
    seen_names: set[str] = set()
    _append_merged_players(merged_players, players, seen_ids, seen_internal_ids, seen_names)

    # ── Fallback: add roster players with no season stats ─────────
    all_roster_result = await db.execute(select(BzzPlayer).where(team_id_filter))
    _append_roster_fallback(
        all_roster_result.scalars().all(), players, seen_ids, seen_internal_ids, seen_names,
    )
    return players


async def _load_national_team_players(
    db: AsyncSession,
    national_team_api_id: int,
) -> list[dict[str, Any]]:
    """Load player season stats for a national team.

    Filters bzz_players by national_team_api_id (direct integer match —
    no fuzzy name resolution needed). Return structure is identical to
    _load_team_players so all downstream callers work unchanged.
    Stats = derniere saison jouee, toutes competitions fusionnees.
    """
    from app.models.bzzoiro import BzzPlayer

    nat_filter = BzzPlayer.national_team_api_id == national_team_api_id
    merged_players = await _load_latest_season_merged(db, nat_filter)

    players: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_internal_ids: set[int] = set()
    seen_names: set[str] = set()
    _append_merged_players(merged_players, players, seen_ids, seen_internal_ids, seen_names)

    # ── Fallback: add roster players with no season stats ─────────
    all_roster_result = await db.execute(select(BzzPlayer).where(nat_filter))
    _append_roster_fallback(
        all_roster_result.scalars().all(), players, seen_ids, seen_internal_ids, seen_names,
    )
    return players


async def _load_players_by_api_ids(
    db: AsyncSession,
    api_ids: list[int],
) -> list[dict[str, Any]]:
    """Load player data for specific BZZ player API IDs (no national_team_api_id filter).

    Used to supplement the pool with players who are in the BZZ event lineup but
    whose national_team_api_id is missing or incorrect in bzz_players.
    Stats = derniere saison jouee, toutes competitions fusionnees.
    """
    from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat

    if not api_ids:
        return []

    merged_players = await _load_latest_season_merged(
        db, BzzPlayerSeasonStat.player_api_id.in_(api_ids), join_player_in_subquery=False,
    )
    seen_ids: set[int] = set()
    players: list[dict[str, Any]] = []
    for player, merged, season, stat_rows in merged_players:
        if player.api_id in seen_ids:
            continue
        seen_ids.add(player.api_id)
        d = _player_dict_from_merged(player, merged, season, stat_rows)
        if d["position"] == "GK":
            continue
        players.append(d)

    # Fallback for players without season stats
    no_stat_ids = [i for i in api_ids if i not in seen_ids]
    if no_stat_ids:
        roster_res = await db.execute(
            select(BzzPlayer).where(BzzPlayer.api_id.in_(no_stat_ids))
        )
        for player in roster_res.scalars().all():
            if player.api_id in seen_ids:
                continue
            position = _norm_pos(_bzz_pos_to_raw(player.position))
            if position == "GK":
                continue
            seen_ids.add(player.api_id)
            players.append(_roster_only_dict(player, position))

    return players


# ── Tournament (CDM) stats blend ──────────────────────────────────

async def _get_tournament_player_ids(
    db: AsyncSession,
    player_api_ids: list[int],
    league_api_id: int,
) -> set[int]:
    """Return IDs of players who appeared in any match of the tournament (>0 min)."""
    from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat

    q = (
        select(BzzPlayerMatchStat.player_api_id)
        .join(BzzEvent, BzzEvent.api_id == BzzPlayerMatchStat.event_api_id)
        .where(
            BzzEvent.league_api_id == league_api_id,
            BzzPlayerMatchStat.player_api_id.in_(player_api_ids),
            BzzPlayerMatchStat.minutes_played > 0,
        )
        .distinct()
    )
    rows = (await db.execute(q)).all()
    return {row[0] for row in rows}


async def _load_tournament_match_stats(
    db: AsyncSession,
    player_api_ids: list[int],
    league_api_id: int,
) -> dict[int, dict[str, Any]]:
    """Aggregate in-tournament match-by-match stats for a list of players.

    Returns {player_api_id: aggregated stats dict} for players with at least
    _TOURNAMENT_MIN_MINUTES total CDM minutes.
    """
    from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat
    from app.pricing.sub_constants import T_SUB_DEFAULT

    if not player_api_ids:
        return {}

    agg = (
        select(
            BzzPlayerMatchStat.player_api_id,
            func.sum(BzzPlayerMatchStat.minutes_played).label("total_minutes"),
            func.sum(BzzPlayerMatchStat.goals).label("total_goals"),
            func.sum(BzzPlayerMatchStat.expected_goals).label("total_xg"),
            func.sum(BzzPlayerMatchStat.goal_assist).label("total_assists"),
            func.sum(BzzPlayerMatchStat.expected_assists).label("total_xa"),
            func.sum(BzzPlayerMatchStat.total_shots).label("total_shots"),
            func.avg(BzzPlayerMatchStat.rating).label("avg_rating"),
            func.avg(BzzPlayerMatchStat.shot_accuracy).label("avg_shot_accuracy"),
            func.avg(BzzPlayerMatchStat.xg_per_shot).label("avg_xg_per_shot"),
            func.count().label("matches_played"),
            # Sub stats from CDM games: games where 0 < minutes < 85 (mirrors sub_stats.py)
            func.sum(
                func.cast(
                    (BzzPlayerMatchStat.minutes_played > 0) & (BzzPlayerMatchStat.minutes_played < 85),
                    Integer,
                )
            ).label("sub_games"),
            func.avg(
                case(
                    (
                        (BzzPlayerMatchStat.minutes_played > 0) & (BzzPlayerMatchStat.minutes_played < 85),
                        BzzPlayerMatchStat.minutes_played,
                    ),
                    else_=None,
                )
            ).label("avg_sub_minutes"),
        )
        .join(BzzEvent, BzzEvent.api_id == BzzPlayerMatchStat.event_api_id)
        .where(
            BzzEvent.league_api_id == league_api_id,
            BzzPlayerMatchStat.player_api_id.in_(player_api_ids),
        )
        .group_by(BzzPlayerMatchStat.player_api_id)
    )

    rows = (await db.execute(agg)).all()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        total_minutes = row.total_minutes or 0
        if total_minutes < _TOURNAMENT_MIN_MINUTES:
            continue
        matches_played = row.matches_played or 1
        minutes_per_90 = total_minutes / 90.0
        total_goals = row.total_goals or 0
        total_xg = row.total_xg or 0.0
        total_assists = row.total_assists or 0
        total_xa = row.total_xa or 0.0
        total_shots = row.total_shots or 0
        goals_per_shot = min(total_goals / total_shots, 0.50) if total_shots > 0 else 0.0
        # CDM-derived sub stats: p_sub = fraction of games with <85 min, avg_sub_time = avg of those
        sub_games = row.sub_games or 0
        p_sub_cdm = sub_games / matches_played if matches_played > 0 else 0.35
        avg_sub_time_cdm = float(row.avg_sub_minutes) if row.avg_sub_minutes is not None else T_SUB_DEFAULT
        out[row.player_api_id] = {
            "total_minutes": total_minutes,
            "matches_played": matches_played,
            "cdm_goals_per_90": total_goals / minutes_per_90,
            "cdm_xg_per_90": total_xg / minutes_per_90,
            "cdm_assists_per_90": total_assists / minutes_per_90,
            "cdm_xa_per_90": total_xa / minutes_per_90,
            "total_goals": total_goals,
            "total_xg": total_xg,
            "total_assists": total_assists,
            "total_xa": total_xa,
            "total_shots": total_shots,
            "goals_per_shot": goals_per_shot,
            "avg_rating": row.avg_rating or 0.0,
            "avg_shot_accuracy": row.avg_shot_accuracy or 0.0,
            "avg_xg_per_shot": row.avg_xg_per_shot or 0.0,
            "p_sub_cdm": p_sub_cdm,
            "avg_sub_time_cdm": avg_sub_time_cdm,
        }
    return out


def _blend_tournament_stats(
    players: list[dict[str, Any]],
    tournament_stats: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Blend club season stats with CDM in-tournament stats (in-place).

    Blend formula per player with CDM data:
      effective_cdm_xg = ALPHA * cdm_goals/90 + (1-ALPHA) * cdm_xg/90
      blended_xg/90   = CLUB_W * club_xg/90 + CDM_W * effective_cdm_xg
    Same pattern for assists. Also blends avg_rating and shot quality.
    """
    for p in players:
        ts = tournament_stats.get(p.get("player_id", -1))
        if ts is None:
            continue

        a = _TOURNAMENT_GOALS_XG_ALPHA
        effective_cdm_xg = a * ts["cdm_goals_per_90"] + (1 - a) * ts["cdm_xg_per_90"]
        effective_cdm_xa = a * ts["cdm_assists_per_90"] + (1 - a) * ts["cdm_xa_per_90"]

        club_xg = p.get("npxg_per_90") or p.get("xg_per_90") or 0.0
        club_xa = p.get("xa_per_90") or 0.0

        blended_xg = _TOURNAMENT_CLUB_WEIGHT * club_xg + _TOURNAMENT_CDM_WEIGHT * effective_cdm_xg
        blended_xa = _TOURNAMENT_CLUB_WEIGHT * club_xa + _TOURNAMENT_CDM_WEIGHT * effective_cdm_xa

        p["npxg_per_90"] = blended_xg
        p["xg_per_90"] = blended_xg
        p["xa_per_90"] = blended_xa

        # Update totals so conversion_rate in allocate_player reflects CDM reality
        p["npxg_total"] = ts["total_xg"]
        p["goals_total"] = ts["total_goals"]
        p["xa_total"] = ts["total_xa"]
        p["assists_total"] = ts["total_assists"]
        p["finishing_delta"] = ts["total_goals"] - ts["total_xg"]

        # Blend rating (skip if either side is zero)
        cdm_r = ts.get("avg_rating") or 0.0
        club_r = p.get("avg_rating") or 0.0
        if cdm_r > 0:
            p["avg_rating"] = (
                _TOURNAMENT_CLUB_WEIGHT * club_r + _TOURNAMENT_CDM_WEIGHT * cdm_r
                if club_r > 0 else cdm_r
            )

        # Shot quality blend — prefer goals/shot (actual finishing) over xg_per_shot (model)
        cdm_sa = ts.get("avg_shot_accuracy") or 0.0
        if cdm_sa > 0:
            p["shot_accuracy"] = (
                _TOURNAMENT_CLUB_WEIGHT * (p.get("shot_accuracy") or 0.0)
                + _TOURNAMENT_CDM_WEIGHT * cdm_sa
            )
        cdm_gps = ts.get("goals_per_shot") or 0.0
        club_xps = p.get("xg_per_shot") or 0.0
        if cdm_gps > 0:
            p["xg_per_shot"] = _TOURNAMENT_CLUB_WEIGHT * club_xps + _TOURNAMENT_CDM_WEIGHT * cdm_gps
        elif ts.get("avg_xg_per_shot") or 0.0 > 0:
            p["xg_per_shot"] = (
                _TOURNAMENT_CLUB_WEIGHT * club_xps
                + _TOURNAMENT_CDM_WEIGHT * (ts["avg_xg_per_shot"] or 0.0)
            )

        # Replace club form signal with CDM form (goals/assists in tournament)
        # form_xg_5 = total CDM goals; form_rate = total_goals / (5 * exp_mins/90) ≈ cdm_goals/90
        p["form_xg_5"] = float(ts["total_goals"])
        p["form_assists_5"] = float(ts["total_assists"])

        logger.debug(
            "_blend_tournament_stats [%s]: club_xg/90=%.3f effective_cdm_xg/90=%.3f → blended=%.3f",
            p.get("player_name", "?"), club_xg, effective_cdm_xg, blended_xg,
        )
    return players


# ── Lineup pricing (compo) ───────────────────────────────────────
#
# Les titulaires sont désignés par IDENTIFIANT (bzz_players.api_id), jamais
# par nom : source unique.
#
# Le xG du match est réparti ENTIÈREMENT entre les titulaires. Sur
# l'effectif complet, compute_player_shares divise par max(Σ poids, λ_équipe)
# — garde-fou pensé pour un vivier incomplet. Appliqué tel quel à une compo,
# il la rendait sans effet dès que la somme des poids des titulaires restait
# sous le xG du match : même dénominateur (λ_équipe) que pour l'effectif,
# donc mêmes parts, mêmes cotes, et une partie du xG attribuée à personne.
# Constaté le 23/11 sur Man City – Napoli : Haaland 2,13 → 2,13 côté City
# (xG élevé) alors que Napoli bougeait. Onze joueurs sur le terrain
# produisent tout le xG de l'équipe : leurs parts somment à 1.

MIN_LINEUP_STARTERS = 5


def select_lineup_starters(
    players: list[dict[str, Any]],
    starter_ids: set[int] | list[int],
    team: str,
) -> tuple[list[dict[str, Any]], list[InputWarning]]:
    """Titulaires du vivier désignés par identifiant, et signalements.

    Un identifiant absent du vivier est ignoré, signalé (InputWarning) et
    remonté en alerte ; il ne fait jamais échouer le calcul.
    """
    wanted = {int(i) for i in starter_ids}
    starters = [p for p in players if p.get("player_id") in wanted]
    found = {p["player_id"] for p in starters}
    warnings: list[InputWarning] = []
    for pid in sorted(wanted - found):
        warnings.append(InputWarning(
            "UNKNOWN_STARTER_ID", pid, None,
            f"team={team}: titulaire {pid} absent de l'effectif — ignoré",
        ))
    if len(starters) < MIN_LINEUP_STARTERS:
        warnings.append(InputWarning(
            "TOO_FEW_STARTERS", None, None,
            f"team={team}: {len(starters)} titulaire(s) reconnu(s) sur {len(wanted)} "
            f"— compo non appliquée (minimum {MIN_LINEUP_STARTERS})",
        ))
    if warnings:
        for w in warnings:
            logger.warning("compo [%s]: %s — %s", team, w.code, w.detail)
        _alert_once(
            f"Calculateur, compo {team} : " + " ; ".join(w.detail for w in warnings)
        )
    return starters, warnings


def lineup_pen_taker(starters: list[dict[str, Any]], override: int | None) -> int | None:
    """Tireur de penalty de la compo : le forçage s'il est titulaire, sinon détecté."""
    ids = {p["player_id"] for p in starters}
    if override is not None and override in ids:
        return override
    return detect_penalty_taker(starters)


def lineup_shares(
    starters: list[dict[str, Any]], team: str, match_xg: float,
) -> list[PlayerShare]:
    """Parts des titulaires : les postes de la compo se partagent exactement
    le xG de l'équipe et le budget de passes décisives."""
    return compute_player_shares(
        starters, team, lambda_team=match_xg, xi_ids={p["player_id"] for p in starters},
    )


def compute_lineup_allocation(
    players: list[dict[str, Any]],
    starter_ids: set[int] | list[int],
    team: str,
    team_xg: float,
    pen_taker_id: int | None = None,
    sub_by_id: dict[int, tuple[float, float]] | None = None,
    warnings_out: list[InputWarning] | None = None,
) -> list[PlayerAllocation]:
    """Cotes de la compo : le xG de l'équipe réparti entre les seuls titulaires.

    Méthode Alpha inchangée (rythmes, minutes attendues, multiplicateurs) ;
    seule la compo restreint qui se partage le xG. `sub_by_id` reporte
    p_sub / avg_sub_time calculés pour l'effectif.

    Rend [] si moins de MIN_LINEUP_STARTERS titulaires sont reconnus (signalé).
    Les signalements sont aussi ajoutés à `warnings_out` quand il est fourni.
    """
    starters, warnings = select_lineup_starters(players, starter_ids, team)
    if warnings_out is not None:
        warnings_out.extend(warnings)
    if len(starters) < MIN_LINEUP_STARTERS:
        return []

    pen_id = lineup_pen_taker(starters, pen_taker_id)
    budget_assists = team_xg * ASSIST_GOAL_RATE
    allocs = []
    shares = lineup_shares(starters, team, team_xg)
    marquer_tireur(shares, pen_id)
    for s in shares:
        p_sub, avg_sub_time = (sub_by_id or {}).get(s.player_id, (0.35, 65.0))
        allocs.append(allocate_player(
            s, team_xg, s.is_pen_taker, budget_assists,
            p_sub=p_sub, avg_sub_time=avg_sub_time,
        ))
    return sorted(allocs, key=lambda a: a.prob_goal, reverse=True)


# ── Orchestration ─────────────────────────────────────────────────

async def load_match_pricing(
    db: AsyncSession,
    fixture: Any,
    home_xg_override: float | None = None,
    away_xg_override: float | None = None,
    home_pen_taker_override: int | None = None,
    away_pen_taker_override: int | None = None,
    home_starter_ids: list[int] | None = None,
    away_starter_ids: list[int] | None = None,
) -> MatchPricingResult | None:
    """Full Top-Down pricing pipeline for one fixture (Model C).

    home_starter_ids / away_starter_ids: optional starters, by identifier
    (bzz_players.api_id). When provided, the response also contains
    home_lineup_players / away_lineup_players with the team xG split among
    those starters only.
    The main home_players / away_players always contains the full squad.
    """
    home_team = fixture.home_team
    away_team = fixture.away_team
    market_result: MarketXgResult | None = None

    if home_xg_override is not None and away_xg_override is not None:
        home_match_xg = home_xg_override
        away_match_xg = away_xg_override
        xg_source = "override"
    else:
        _svc = MarketXgService()
        market_result = await _svc.compute(fixture.id, db)
        if market_result is None:
            return None
        xg_source = market_result.xg_source

        home_match_xg = home_xg_override if home_xg_override is not None else market_result.xg_home
        away_match_xg = away_xg_override if away_xg_override is not None else market_result.xg_away

        if home_xg_override is not None or away_xg_override is not None:
            xg_source = "override"

    home_bzz_team_id = getattr(fixture, "home_bzz_team_id", None)
    away_bzz_team_id = getattr(fixture, "away_bzz_team_id", None)

    t_stats: dict[int, dict[str, Any]] = {}
    home_players_for_lineup: list[dict[str, Any]] | None = None
    away_players_for_lineup: list[dict[str, Any]] | None = None

    if is_international_fixture(fixture):
        from app.models.bzzoiro import BzzEvent
        bzz_api_id_str = (fixture.external_id or "").removeprefix("bzz_")
        bzz_event = None
        if bzz_api_id_str.isdigit():
            ev_res = await db.execute(
                select(BzzEvent).where(BzzEvent.api_id == int(bzz_api_id_str))
            )
            bzz_event = ev_res.scalar_one_or_none()

        if bzz_event is not None and bzz_event.home_team_api_id and bzz_event.away_team_api_id:
            home_players_db = await _load_national_team_players(
                db, national_team_api_id=bzz_event.home_team_api_id
            )
            away_players_db = await _load_national_team_players(
                db, national_team_api_id=bzz_event.away_team_api_id
            )
            if is_wc2026_fixture(fixture):
                all_ids = [p["player_id"] for p in home_players_db + away_players_db]
                # Filtrer au groupe effectif : seuls les joueurs ayant joué au tournoi.
                squad_ids = await _get_tournament_player_ids(db, all_ids, WC2026_LEAGUE_API_ID)
                t_stats = await _load_tournament_match_stats(db, list(squad_ids), WC2026_LEAGUE_API_ID)
                if t_stats:
                    logger.info(
                        "load_match_pricing: CDM stats blend — %d / %d joueurs du groupe",
                        len(t_stats), len(squad_ids),
                    )
                    home_players_db = _blend_tournament_stats(home_players_db, t_stats)
                    away_players_db = _blend_tournament_stats(away_players_db, t_stats)
                # Conserver la liste complète pour l'allocation lineup (inclut les
                # joueurs à leur premier match du tournoi ce soir, absents de squad_ids).
                home_players_for_lineup = home_players_db[:]
                away_players_for_lineup = away_players_db[:]
                # Filtrer l'affichage au groupe ayant déjà joué.
                home_players_db = [p for p in home_players_db if p["player_id"] in squad_ids]
                away_players_db = [p for p in away_players_db if p["player_id"] in squad_ids]

                # Compléter les pools lineup avec les joueurs présents dans le JSONB BZZ
                # mais absents du pool (national_team_api_id manquant ou incorrect).
                # On extrait directement les player_api_id du JSONB pour chaque côté.
                if bzz_event.lineups:
                    pool_home_ids = {p["player_id"] for p in home_players_for_lineup}
                    pool_away_ids = {p["player_id"] for p in away_players_for_lineup}
                    bzz_home_ids: set[int] = set()
                    bzz_away_ids: set[int] = set()
                    for section in ("players", "substitutes"):
                        for p in (bzz_event.lineups.get("home") or {}).get(section) or []:
                            if p.get("id"):
                                bzz_home_ids.add(int(p["id"]))
                        for p in (bzz_event.lineups.get("away") or {}).get(section) or []:
                            if p.get("id"):
                                bzz_away_ids.add(int(p["id"]))
                    missing_home = list(bzz_home_ids - pool_home_ids)
                    missing_away = list(bzz_away_ids - pool_away_ids)
                    if missing_home:
                        extra = await _load_players_by_api_ids(db, missing_home)
                        if extra:
                            logger.info(
                                "load_match_pricing: %d joueurs %s hors pool ajoutés depuis JSONB BZZ: %s",
                                len(extra), home_team, [p["player_name"] for p in extra],
                            )
                            home_players_for_lineup.extend(extra)
                    if missing_away:
                        extra = await _load_players_by_api_ids(db, missing_away)
                        if extra:
                            logger.info(
                                "load_match_pricing: %d joueurs %s hors pool ajoutés depuis JSONB BZZ: %s",
                                len(extra), away_team, [p["player_name"] for p in extra],
                            )
                            away_players_for_lineup.extend(extra)
        else:
            logger.warning(
                "load_match_pricing: international fixture %s (league=%r, external_id=%r) "
                "— BzzEvent not found or missing team IDs, falling back to _load_team_players",
                getattr(fixture, "id", "?"), fixture.league, fixture.external_id,
            )
            home_players_db = await _load_team_players(db, home_team, bzz_team_id=home_bzz_team_id)
            away_players_db = await _load_team_players(db, away_team, bzz_team_id=away_bzz_team_id)
    else:
        home_players_db = await _load_team_players(db, home_team, bzz_team_id=home_bzz_team_id)
        away_players_db = await _load_team_players(db, away_team, bzz_team_id=away_bzz_team_id)

    # For non-WC2026, lineup allocation uses the same pool as display.
    if home_players_for_lineup is None:
        home_players_for_lineup = home_players_db
    if away_players_for_lineup is None:
        away_players_for_lineup = away_players_db

    if not home_players_db:
        logger.warning(
            "load_match_pricing: 0 players found for home team %r (fixture %s) — "
            "check the club's bzz_team_id link and its bzz_players roster",
            home_team, getattr(fixture, "id", "?"),
        )
    if not away_players_db:
        logger.warning(
            "load_match_pricing: 0 players found for away team %r (fixture %s) — "
            "check the club's bzz_team_id link and its bzz_players roster",
            away_team, getattr(fixture, "id", "?"),
        )

    # Niveau 2 de la cascade : la carriere, pour les joueurs qui n'ont pas
    # encore assez joue cette saison. Sans elle, un joueur transfere avec deux
    # matchs au compteur recevrait la moyenne generique de son poste alors
    # qu'on connait son historique -- mesure du 06/11/2026 : 2 406 joueurs
    # cotes sur 3 467 sous le seuil ont une carriere importee.
    #
    # Une seule paire de requetes pour les deux equipes : la boucle de
    # scraping tourne toutes les 60 secondes, un appel par joueur y serait
    # trop couteux.
    await _enrichir_rythme_carriere(db, home_players_db + away_players_db)

    home_shares = compute_player_shares(home_players_db, home_team, lambda_team=home_match_xg)
    away_shares = compute_player_shares(away_players_db, away_team, lambda_team=away_match_xg)

    for w in assess_inputs(home_players_db, home_team):
        logger.warning("assess_inputs [%s]: %s — %s", home_team, w.code, w.detail)
    for w in assess_inputs(away_players_db, away_team):
        logger.warning("assess_inputs [%s]: %s — %s", away_team, w.code, w.detail)

    home_pen_id = home_pen_taker_override or detect_penalty_taker(home_players_db)
    away_pen_id = away_pen_taker_override or detect_penalty_taker(away_players_db)
    marquer_tireur(home_shares, home_pen_id, onze_probable(home_players_db))
    marquer_tireur(away_shares, away_pen_id, onze_probable(away_players_db))

    budget_assists_home = home_match_xg * ASSIST_GOAL_RATE
    budget_assists_away = away_match_xg * ASSIST_GOAL_RATE

    from app.services.sub_stats import get_player_sub_stats

    _home_allocs: list[PlayerAllocation] = []
    for s in home_shares:
        sub = await get_player_sub_stats(
            player_name=s.player_name,
            position=s.position or "MF",
            db=db,
            n_matches=20,
        )
        _home_allocs.append(
            allocate_player(
                s, home_match_xg, s.is_pen_taker, budget_assists_home,
                p_sub=sub.p_sub, avg_sub_time=sub.avg_sub_time,
            )
        )
    home_allocs = sorted(_home_allocs, key=lambda a: a.prob_goal, reverse=True)

    _away_allocs: list[PlayerAllocation] = []
    for s in away_shares:
        sub = await get_player_sub_stats(
            player_name=s.player_name,
            position=s.position or "MF",
            db=db,
            n_matches=20,
        )
        _away_allocs.append(
            allocate_player(
                s, away_match_xg, s.is_pen_taker, budget_assists_away,
                p_sub=sub.p_sub, avg_sub_time=sub.avg_sub_time,
            )
        )
    away_allocs = sorted(_away_allocs, key=lambda a: a.prob_goal, reverse=True)

    # ── WC2026: direct CDM lambda blend ──────────────────────────────
    # For players with ≥ _WC_DIRECT_LAMBDA_MIN_MINUTES in the tournament, blend the
    # top-down lambda with a direct CDM signal (goals/90 × mins_ratio).
    # This bypasses the team-xG ceiling that structurally limits top-down lambdas.
    if is_wc2026_fixture(fixture) and t_stats:
        for alloc in home_allocs + away_allocs:
            ts_d = t_stats.get(alloc.player_id)
            if ts_d is None:
                continue
            if ts_d.get("total_minutes", 0) < _WC_DIRECT_LAMBDA_MIN_MINUTES:
                continue
            cdm_goals_per_90 = ts_d.get("cdm_goals_per_90", 0.0) or 0.0
            cdm_xg_per_90 = ts_d.get("cdm_xg_per_90", 0.0) or 0.0
            # Signal CDM = max(goals/90, xG/90) quand les deux sont dispo.
            # Pour les joueurs à fort xG mais peu de buts marqués (e.g. Lautaro),
            # xG/90 reflète mieux la qualité que goals/90.
            if cdm_goals_per_90 > 0:
                cdm_signal = max(cdm_goals_per_90, cdm_xg_per_90)
                w = _WC_DIRECT_LAMBDA_WEIGHT
            elif cdm_xg_per_90 > 0:
                cdm_signal = cdm_xg_per_90
                w = _WC_DIRECT_LAMBDA_WEIGHT * 0.5  # halved weight — xG less certain
            else:
                continue
            mins_ratio = alloc.expected_minutes / 90.0
            lambda_cdm_direct = cdm_signal * mins_ratio
            lambda_blended = (1 - w) * alloc.lambda_total + w * lambda_cdm_direct
            lambda_blended = max(0.001, min(lambda_blended, 3.0))
            prob_goal = 1 - math.exp(-lambda_blended)
            alloc.lambda_total = lambda_blended
            alloc.prob_goal = prob_goal
            alloc.fair_odds_goal = round(1 / prob_goal, 2) if prob_goal > 0 else 9999.0
            # Recompute supersub using CDM sub stats — not Understat (club) data.
            # For WC players like Messi who enters late for Argentina, club data
            # gives wrong avg_sub_time and p_sub.
            p_sub_cdm = ts_d.get("p_sub_cdm", alloc.p_sub)
            avg_sub_time_cdm = ts_d.get("avg_sub_time_cdm", alloc.avg_sub_time)
            alloc.p_sub = round(p_sub_cdm, 4)
            alloc.avg_sub_time = round(avg_sub_time_cdm, 1)
            # C.But+Sub = cote à 90 min (qualité pure), cohérent avec allocate_player.
            lambda_blended_90 = lambda_blended / mins_ratio if mins_ratio > 0 else lambda_blended
            p_goal_ss = 1.0 - math.exp(-lambda_blended_90)
            alloc.p_goal_supersub = round(p_goal_ss, 4)
            alloc.fair_odds_goal_supersub = round(1.0 / p_goal_ss, 2) if p_goal_ss > 0 else 99.0

    home_lineup_warnings: list[InputWarning] = []
    away_lineup_warnings: list[InputWarning] = []
    home_lineup = (
        compute_lineup_allocation(
            home_players_for_lineup, set(home_starter_ids), home_team, home_match_xg,
            pen_taker_id=home_pen_taker_override,
            sub_by_id={a.player_id: (a.p_sub, a.avg_sub_time) for a in home_allocs},
            warnings_out=home_lineup_warnings,
        )
        if home_starter_ids else None
    )
    away_lineup = (
        compute_lineup_allocation(
            away_players_for_lineup, set(away_starter_ids), away_team, away_match_xg,
            pen_taker_id=away_pen_taker_override,
            sub_by_id={a.player_id: (a.p_sub, a.avg_sub_time) for a in away_allocs},
            warnings_out=away_lineup_warnings,
        )
        if away_starter_ids else None
    )

    return MatchPricingResult(
        fixture_id=fixture.id,
        home_team=home_team,
        away_team=away_team,
        home_match_xg=round(home_match_xg, 3),
        away_match_xg=round(away_match_xg, 3),
        xg_source=xg_source,
        home_players=home_allocs,
        away_players=away_allocs,
        home_lineup_players=home_lineup or None,
        away_lineup_players=away_lineup or None,
        home_lineup_warnings=[w.detail for w in home_lineup_warnings],
        away_lineup_warnings=[w.detail for w in away_lineup_warnings],
        last_scraped_at=market_result.last_snapshot_at if market_result is not None else None,
    )
