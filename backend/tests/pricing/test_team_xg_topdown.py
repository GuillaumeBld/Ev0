"""Tests for top-down share calculation and new allocate_player."""
import inspect
import math
from types import SimpleNamespace

import pytest

from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat
from app.pricing import team_xg
from app.pricing.assist import ASSIST_GOAL_RATE
from app.pricing.team_xg import PlayerAllocation, allocate_player, compute_player_shares
from app.services.atlas_source import merge_season_stats

SAMPLE_FW = {
    "player_id": 1, "player_name": "Striker A", "position": "FW",
    "matches_played": 15, "minutes_played": 1200,
    "npxg_per_90": 0.35, "xa_per_90": 0.08,
    "xg_per_90": 0.35, "xgchain_per_90": 0.0,
    "shot_accuracy": 0.42, "xg_per_shot": 0.12, "avg_rating": 6.9,
    "key_pass_per_90": 0.30, "accurate_cross_per_90": 0.15, "cross_accuracy": 0.30,
    "npxg_total": 4.0, "goals_total": 5, "xa_total": 1.0, "assists_total": 2,
    "form_xg_5": None, "form_assists_5": None,
    "has_bzz_stats": True,
}

SAMPLE_MF = {
    "player_id": 2, "player_name": "Mid B", "position": "MF",
    "matches_played": 15, "minutes_played": 1200,
    "npxg_per_90": 0.10, "xa_per_90": 0.12,
    "xg_per_90": 0.10, "xgchain_per_90": 0.0,
    "shot_accuracy": 0.35, "xg_per_shot": 0.09, "avg_rating": 6.8,
    "key_pass_per_90": 0.55, "accurate_cross_per_90": 0.20, "cross_accuracy": 0.30,
    "npxg_total": 1.5, "goals_total": 1, "xa_total": 2.0, "assists_total": 3,
    "form_xg_5": None, "form_assists_5": None,
    "has_bzz_stats": True,
}


def _onze(*vedettes):
    """Les vedettes + des coequipiers etablis pour remplir 11 postes."""
    out = list(vedettes)
    for i in range(11 - len(out)):
        out.append({**SAMPLE_MF, "player_id": 100 + i, "player_name": f"Coequipier {i}",
                    "npxg_per_90": 0.06, "xg_per_90": 0.06, "xa_per_90": 0.07})
    return out


def _lambda_poste(alloc):
    return -math.log(1.0 - alloc.p_goal_supersub)


class TestComputePlayerSharesTopDown:
    def test_les_onze_postes_totalisent_exactement_le_xg_equipe(self):
        """Regle du 26/11/2026 : avec remplacant, les 11 postes se partagent
        EXACTEMENT le xG de l'equipe et le budget de passes decisives."""
        onze = _onze(SAMPLE_FW, SAMPLE_MF)
        shares = compute_player_shares(onze, "Team", lambda_team=1.5)
        budget = 1.5 * ASSIST_GOAL_RATE
        allocs = [allocate_player(sh, 1.5, False, budget) for sh in shares]
        assert sum(_lambda_poste(a) for a in allocs) == pytest.approx(1.5, rel=0.01)
        assert sum(-math.log(1.0 - a.p_assist_supersub) for a in allocs) == pytest.approx(budget, rel=0.01)

    def test_les_minutes_ne_reduisent_pas_la_part_du_poste(self):
        """Le pari couvre le poste 90 minutes : un titulaire qui sort a l'heure
        de jeu garde la meme part que s'il jouait tout le match."""
        complet = compute_player_shares(_onze(SAMPLE_FW, SAMPLE_MF), "Team", lambda_team=1.5)
        partiel_fw = {**SAMPLE_FW, "minutes_played": 15 * 55}
        partiel = compute_player_shares(_onze(partiel_fw, SAMPLE_MF), "Team", lambda_team=1.5)
        assert complet[0].npxg_share == pytest.approx(partiel[0].npxg_share, rel=1e-6)

    def test_fw_has_higher_goal_share_than_mf(self):
        shares = compute_player_shares([SAMPLE_FW, SAMPLE_MF], "Team", lambda_team=1.5)
        fw = next(s for s in shares if s.position == "FW")
        mf = next(s for s in shares if s.position == "MF")
        assert fw.npxg_share > mf.npxg_share

    def test_form_blending_increases_share(self):
        fw_with_form = {**SAMPLE_FW, "form_xg_5": 3.0}  # hot form: 3 goals in 5 games
        shares_base = compute_player_shares([SAMPLE_FW, SAMPLE_MF], "Team", lambda_team=1.5)
        shares_form = compute_player_shares([fw_with_form, SAMPLE_MF], "Team", lambda_team=1.5)
        fw_base = next(s for s in shares_base if s.position == "FW")
        fw_form = next(s for s in shares_form if s.position == "FW")
        assert fw_form.npxg_share > fw_base.npxg_share

    def test_aucun_xg_perdu_quand_les_rythmes_sont_faibles(self):
        """Avant le 26/11, une equipe aux rythmes faibles ne recevait qu'une
        partie du xG du match (denominateur = xG du match). Desormais tout le
        xG est reparti entre les postes."""
        faible = [{**p, "npxg_per_90": 0.02, "xg_per_90": 0.02} for p in _onze(SAMPLE_FW)]
        shares = compute_player_shares(faible, "Team", lambda_team=2.0)
        allocs = [allocate_player(sh, 2.0, False, 2.0 * ASSIST_GOAL_RATE) for sh in shares]
        assert sum(_lambda_poste(a) for a in allocs) == pytest.approx(2.0, rel=0.01)


class TestAllocatePlayerTopDown:
    def _get_share(self, player=SAMPLE_FW):
        shares = compute_player_shares(_onze(player, SAMPLE_MF), "Team", lambda_team=1.5)
        return shares[0]

    def test_average_fw_fair_odds_goal_reasonable(self):
        share = self._get_share(SAMPLE_FW)
        alloc = allocate_player(share, team_match_xg=1.5, is_pen_taker=False, budget_assists=1.5 * ASSIST_GOAL_RATE)
        # Should be between 2 and 10 for an average FW with 75 min expected
        assert 2.0 <= alloc.fair_odds_goal <= 10.0

    def test_pen_taker_has_lower_fair_odds(self):
        share = self._get_share(SAMPLE_FW)
        alloc_no_pen = allocate_player(share, 1.5, False, 1.5 * ASSIST_GOAL_RATE)
        alloc_pen = allocate_player(share, 1.5, True, 1.5 * ASSIST_GOAL_RATE)
        assert alloc_pen.fair_odds_goal < alloc_no_pen.fair_odds_goal

    def test_has_form_flags(self):
        share = self._get_share(SAMPLE_FW)
        alloc = allocate_player(share, 1.5, False, 1.5 * ASSIST_GOAL_RATE)
        assert alloc.has_form_goal is False   # form_xg_5 was None
        assert alloc.has_form_assist is False  # form_assists_5 was None

    def test_form_data_sets_flag(self):
        fw_form = {**SAMPLE_FW, "form_xg_5": 2.0, "form_assists_5": 1.0}
        shares = compute_player_shares(_onze(fw_form), "Team", lambda_team=1.5)
        alloc = allocate_player(shares[0], 1.5, False, 1.5 * ASSIST_GOAL_RATE)
        assert alloc.has_form_goal is True
        assert alloc.has_form_assist is True

    def test_matches_played_propagated(self):
        share = self._get_share(SAMPLE_FW)
        alloc = allocate_player(share, 1.5, False, 1.5 * ASSIST_GOAL_RATE)
        assert alloc.matches_played == 15


# ── Tâche 6 : stats du calculateur = toutes les compétitions de la saison ──


_STAT_COLS = [c.key for c in BzzPlayerSeasonStat.__table__.columns]


def _stat(**kw):
    """Ligne bzz_player_season_stats : toutes les colonnes du vrai modele, None par defaut."""
    base = dict.fromkeys(_STAT_COLS)
    base.update(kw)
    return SimpleNamespace(**base)


def _joueur(api_id=7, position="F", name="Buteur Test"):
    return BzzPlayer(api_id=api_id, name=name, position=position)


def test_player_dict_from_merged_toutes_competitions():
    rows = [
        _stat(player_api_id=7, league_api_id=1, season="2026-2027", matches_played=10,
              minutes_played=900, goals=3, expected_goals=2.5, expected_assists=1.0,
              goal_assist=2, form_xg_5=1.1),
        _stat(player_api_id=7, league_api_id=2, season="2026-2027", matches_played=4,
              minutes_played=300, goals=1, expected_goals=0.7, expected_assists=0.2,
              goal_assist=0, form_xg_5=0.4),
    ]
    merged = merge_season_stats(rows, "2026-2027")
    d = team_xg._player_dict_from_merged(_joueur(), merged, "2026-2027", rows)
    assert d["minutes_played"] == 1200
    assert d["matches_played"] == 14
    assert d["npxg_per_90"] == pytest.approx(3.2 / 1200 * 90)
    assert d["xg_per_90"] == pytest.approx(3.2 / 1200 * 90)
    assert d["xa_per_90"] == pytest.approx(1.2 / 1200 * 90)
    assert d["goals_total"] == 4
    assert d["npxg_total"] == pytest.approx(3.2)
    assert d["assists_total"] == 2
    assert d["finishing_delta"] == pytest.approx(4 - 3.2)
    assert d["form_xg_5"] == 1.1  # forme = championnat le plus joue
    assert d["stats_season"] == "2026-2027"
    assert d["has_bzz_stats"] is True
    assert d["position"] == team_xg._norm_pos(team_xg._bzz_pos_to_raw("F"))
    # Jamais de valeur inventee : stat absente de toutes les lignes = 0 comme avant
    assert d["shot_accuracy"] == 0.0
    assert d["avg_rating"] == 0.0
    assert d["form_assists_5"] is None


def test_player_dict_from_merged_memes_cles_que_le_repli_effectif():
    rows = [_stat(season="2026-2027", matches_played=1, minutes_played=90)]
    merged = merge_season_stats(rows, "2026-2027")
    d = team_xg._player_dict_from_merged(_joueur(), merged, "2026-2027", rows)
    for key in SAMPLE_FW:
        assert key in d, key


def test_ratios_une_ligne_valeur_stockee_meme_si_les_totaux_disent_autre_chose():
    # aggregate.py stocke ces ratios comme moyenne des ratios par match ; les
    # multiplicateurs finition/creation sont regles dessus : on les garde tels quels.
    rows = [_stat(season="2026-2027", matches_played=10, minutes_played=900,
                  total_shots=20, shots_on_target=5, expected_goals=4.0,
                  total_cross=10, accurate_cross=9,
                  shot_accuracy=0.41, xg_per_shot=0.13, cross_accuracy=0.27)]
    merged = merge_season_stats(rows, "2026-2027")
    assert merged["shot_accuracy"] != pytest.approx(0.41)  # totaux en desaccord
    d = team_xg._player_dict_from_merged(_joueur(), merged, "2026-2027", rows)
    assert d["shot_accuracy"] == 0.41
    assert d["xg_per_shot"] == 0.13
    assert d["cross_accuracy"] == 0.27


def test_ratios_deux_lignes_moyenne_ponderee_par_matchs():
    rows = [
        _stat(season="2026-2027", league_api_id=1, matches_played=9, minutes_played=800,
              shot_accuracy=0.5, xg_per_shot=0.10, cross_accuracy=None),
        _stat(season="2026-2027", league_api_id=2, matches_played=3, minutes_played=270,
              shot_accuracy=0.3, xg_per_shot=0.20, cross_accuracy=0.4),
    ]
    merged = merge_season_stats(rows, "2026-2027")
    d = team_xg._player_dict_from_merged(_joueur(), merged, "2026-2027", rows)
    assert d["shot_accuracy"] == pytest.approx((0.5 * 9 + 0.3 * 3) / 12)
    assert d["xg_per_shot"] == pytest.approx((0.10 * 9 + 0.20 * 3) / 12)
    assert d["cross_accuracy"] == 0.4  # ligne a None ignoree
    # Tout a None -> repli 0.0 d'aujourd'hui
    rows0 = [_stat(season="2026-2027", matches_played=2, minutes_played=180)]
    d0 = team_xg._player_dict_from_merged(
        _joueur(), merge_season_stats(rows0, "2026-2027"), "2026-2027", rows0)
    assert (d0["shot_accuracy"], d0["xg_per_shot"], d0["cross_accuracy"]) == (0.0, 0.0, 0.0)


def test_joueur_transfere_deux_clubs_meme_saison_tout_compte():
    rows = [
        _stat(player_api_id=7, league_api_id=61, season="2026-2027", matches_played=6,
              minutes_played=500, goals=2, expected_goals=1.5),
        _stat(player_api_id=7, league_api_id=39, season="2026-2027", matches_played=5,
              minutes_played=400, goals=1, expected_goals=1.2),
    ]
    merged = merge_season_stats(rows, "2026-2027")
    d = team_xg._player_dict_from_merged(_joueur(), merged, "2026-2027", rows)
    assert d["minutes_played"] == 900
    assert d["goals"] == 3
    assert d["npxg_per_90"] == pytest.approx(2.7 / 900 * 90)


def test_stats_season_suit_jusqu_a_l_allocation():
    p = dict(SAMPLE_FW, stats_season="2025-2026")
    shares = compute_player_shares([p], "Test", lambda_team=1.5)
    assert shares[0].stats_season == "2025-2026"
    alloc = allocate_player(shares[0], 1.5, False, 1.5 * ASSIST_GOAL_RATE)
    assert alloc.stats_season == "2025-2026"
    # Joueur sans stats : pas de saison
    shares = compute_player_shares([dict(SAMPLE_FW)], "Test", lambda_team=1.5)
    assert allocate_player(shares[0], 1.5, False, 0.9).stats_season is None


def test_les_trois_chargeurs_fusionnent_au_lieu_de_garder_une_ligne():
    for fn in (team_xg._load_team_players, team_xg._load_national_team_players,
               team_xg._load_players_by_api_ids):
        src = inspect.getsource(fn)
        assert "matches_played.desc()" not in src, fn.__name__
        assert "_load_latest_season_merged" in src, fn.__name__
    helper = inspect.getsource(team_xg._load_latest_season_merged)
    assert "merge_season_stats" in helper
    assert "max_season" in helper


@pytest.mark.asyncio
async def test_price_match_ne_casse_pas_si_saison_courante_indisponible():
    from dataclasses import replace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api.pricing import MatchPriceRequest, price_match
    from app.pricing.team_xg import MatchPricingResult

    shares = compute_player_shares([dict(SAMPLE_FW)], "Home FC", lambda_team=1.5)
    a = replace(allocate_player(shares[0], 1.5, False, 0.9), stats_season="2025-2026")
    pricing = MatchPricingResult(fixture_id=42, home_team="Home FC", away_team="Away FC",
                                 home_match_xg=1.5, away_match_xg=1.2,
                                 home_players=[a], away_players=[])
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = MagicMock(id=42)
    db.execute = AsyncMock(return_value=res)
    with patch("app.pricing.team_xg.load_match_pricing", new=AsyncMock(return_value=pricing)), \
         patch("app.pricing.beta_pricing.compute_beta_allocations", new=AsyncMock(return_value=({}, {}, {}, {}))), \
         patch("app.services.season_service.current_season", new=AsyncMock(side_effect=RuntimeError("db"))):
        out = await price_match(MatchPriceRequest(fixture_id=42), db=db)
    assert out.home_players[0].stats_season == "2025-2026"
    assert out.home_players[0].stats_stale is False


def test_player_allocation_out_porte_la_saison_des_stats():
    from app.api.pricing import PlayerAllocationOut
    fields = PlayerAllocationOut.model_fields
    assert fields["stats_season"].default is None
    assert fields["stats_stale"].default is False
    src = inspect.getsource(__import__("app.api.pricing", fromlist=["x"]).price_match)
    assert "current_season" in src


@pytest.mark.asyncio
async def test_price_match_signale_des_stats_d_une_saison_passee():
    from dataclasses import replace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api.pricing import MatchPriceRequest, price_match
    from app.pricing.team_xg import MatchPricingResult

    shares = compute_player_shares([dict(SAMPLE_FW), dict(SAMPLE_MF)], "Home FC", lambda_team=1.5)
    base = [allocate_player(s, 1.5, False, 0.9) for s in shares]
    a_ancien = replace(base[0], stats_season="2025-2026")
    a_courant = replace(base[1], stats_season="2026-2027")
    a_sans = replace(base[1], player_id=3, stats_season=None)
    fx = MagicMock(id=42, home_team="Home FC", away_team="Away FC")
    pricing = MatchPricingResult(
        fixture_id=42, home_team="Home FC", away_team="Away FC",
        home_match_xg=1.5, away_match_xg=1.2,
        home_players=[a_ancien, a_courant], away_players=[a_sans],
    )
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = fx
    db.execute = AsyncMock(return_value=res)
    with patch("app.pricing.team_xg.load_match_pricing", new=AsyncMock(return_value=pricing)), \
         patch("app.pricing.beta_pricing.compute_beta_allocations", new=AsyncMock(return_value=({}, {}, {}, {}))), \
         patch("app.services.season_service.current_season", new=AsyncMock(return_value="2026-2027")):
        out = await price_match(MatchPriceRequest(fixture_id=42), db=db)
    h0, h1 = out.home_players
    assert (h0.stats_season, h0.stats_stale) == ("2025-2026", True)
    assert (h1.stats_season, h1.stats_stale) == ("2026-2027", False)
    assert (out.away_players[0].stats_season, out.away_players[0].stats_stale) == (None, False)
