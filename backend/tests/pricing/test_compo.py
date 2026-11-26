"""Moteur de compo — titulaires par identifiant, Alpha ET Beta, xG vraiment réparti.

Constat prod du 23/11 (Man City – Napoli, 10 titulaires par camp) :
  * côté City, les cotes « avec sub » de la compo étaient IDENTIQUES à celles
    de l'effectif complet (Haaland 2,13 → 2,13) ;
  * côté Napoli elles bougeaient (De Bruyne 4,45 → 3,39) ;
  * Beta renvoyait partout ses cotes « effectif complet ».

Cause : compute_player_shares divise par max(Σ poids, λ_équipe). Quand la
somme des poids des titulaires reste sous le xG du match (City, xG élevé),
le dénominateur vaut λ_équipe dans les deux calculs → mêmes parts, mêmes
cotes ; une partie du xG n'est attribuée à personne. Beta, lui, n'était
jamais recalculé sur la compo.
"""
from __future__ import annotations

import inspect
import math
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.pricing.team_xg as team_xg
from app.pricing.career_blend import BlendedRhythm
from app.pricing.team_xg import (
    InputWarning,
    compute_lineup_allocation,
    compute_player_shares,
    select_lineup_starters,
)

BACKEND = Path(__file__).resolve().parents[2]


def _p(pid: int, pos: str = "MF", npxg: float = 0.10, xa: float = 0.08,
       matches: int = 20, minutes: int = 1700) -> dict:
    return {
        "player_id": pid,
        "player_name": f"Joueur {pid}",
        "position": pos,
        "matches_played": matches,
        "minutes_played": minutes,
        "npxg_per_90": npxg,
        "xg_per_90": npxg,
        "xa_per_90": xa,
        "npxg_total": npxg * matches,
        "goals_total": 3,
        "xa_total": xa * matches,
        "assists_total": 2,
        "avg_rating": 7.0,
        "shot_accuracy": 0.4,
        "xg_per_shot": 0.12,
        "has_bzz_stats": True,
    }


def _squad() -> list[dict]:
    """Effectif « City » : 16 joueurs dont la somme des poids reste SOUS le
    xG du match (2,4) → c'est exactement le cas où la compo ne changeait rien."""
    squad = [
        _p(1, "CF_lone", npxg=0.55, xa=0.10),
        _p(2, "winger", npxg=0.25, xa=0.20),
        # Titulaire à minutes partielles : ~45 min par match en moyenne
        _p(3, "AM", npxg=0.20, xa=0.25, matches=20, minutes=900),
        _p(4, "winger", npxg=0.22, xa=0.18),
        _p(5, "CM", npxg=0.08, xa=0.10),
        _p(6, "CM", npxg=0.06, xa=0.09),
        _p(7, "FB", npxg=0.03, xa=0.06),
        _p(8, "CB", npxg=0.04, xa=0.02),
        _p(9, "CB", npxg=0.03, xa=0.02),
        _p(10, "FB", npxg=0.02, xa=0.05),
    ]
    # Remplaçants
    squad += [_p(pid, "MF", npxg=0.05, xa=0.04, minutes=500) for pid in range(11, 17)]
    return squad


TEAM_XG = 2.4
STARTERS = set(range(1, 11))


# ── (a) xG réparti entre les titulaires ─────────────────────────────

def _lambda_poste_but(a) -> float:
    return -math.log(1.0 - a.p_goal_supersub)


def _lambda_poste_passe(a) -> float:
    return -math.log(1.0 - a.p_assist_supersub)


def test_effectif_les_onze_postes_probables_totalisent_le_xg():
    """Sans compo : le onze probable se partage exactement le xG du match,
    même quand la somme des rythmes reste sous ce xG (cas City)."""
    squad = _squad()
    onze = team_xg.onze_probable(squad)
    assert onze == set(range(1, 12)), "titulaires habituels, pas les remplaçants"
    shares = compute_player_shares(squad, "City", lambda_team=TEAM_XG)
    allocs = [team_xg.allocate_player(s, TEAM_XG, False, TEAM_XG * team_xg.ASSIST_GOAL_RATE)
              for s in shares if s.player_id in onze]
    assert sum(_lambda_poste_but(a) for a in allocs) == pytest.approx(TEAM_XG, rel=0.01)


def test_compo_repartit_tout_le_xg_entre_les_titulaires():
    """Avec remplaçant, les postes de la compo totalisent EXACTEMENT le xG de
    l'équipe et le budget de passes décisives — finition comprise."""
    allocs = compute_lineup_allocation(_squad(), STARTERS, "City", TEAM_XG)
    assert {a.player_id for a in allocs} == STARTERS
    assert sum(_lambda_poste_but(a) for a in allocs) == pytest.approx(TEAM_XG, rel=0.01)
    budget = TEAM_XG * team_xg.ASSIST_GOAL_RATE
    assert sum(_lambda_poste_passe(a) for a in allocs) == pytest.approx(budget, rel=0.01)


def test_compo_penalty_pris_dans_le_xg_pas_ajoute():
    allocs = compute_lineup_allocation(_squad(), STARTERS, "City", TEAM_XG, pen_taker_id=1)
    assert sum(_lambda_poste_but(a) for a in allocs) == pytest.approx(TEAM_XG, rel=0.01)


def test_compo_raccourcit_les_cotes_avec_sub_y_compris_minutes_partielles():
    squad = _squad()
    full = {s.player_id: s for s in compute_player_shares(squad, "City", lambda_team=TEAM_XG)}
    full_allocs = {
        pid: team_xg.allocate_player(s, TEAM_XG, False, TEAM_XG * team_xg.ASSIST_GOAL_RATE)
        for pid, s in full.items()
    }
    compo = {a.player_id: a for a in compute_lineup_allocation(squad, STARTERS, "City", TEAM_XG)}
    for pid in (1, 3):  # avant-centre ; meneur à ~45 min
        assert compo[pid].fair_odds_goal_supersub < full_allocs[pid].fair_odds_goal_supersub
        assert compo[pid].fair_odds_assist_supersub < full_allocs[pid].fair_odds_assist_supersub
    assert full[3].expected_minutes < 60  # bien un joueur à minutes partielles


def test_compo_respecte_le_tireur_de_penalty_force_s_il_est_titulaire():
    allocs = compute_lineup_allocation(_squad(), STARTERS, "City", TEAM_XG, pen_taker_id=2)
    assert [a.player_id for a in allocs if a.is_pen_taker] == [2]


def test_compo_reprend_p_sub_de_l_effectif():
    allocs = compute_lineup_allocation(
        _squad(), STARTERS, "City", TEAM_XG, sub_by_id={1: (0.12, 71.0)},
    )
    a1 = next(a for a in allocs if a.player_id == 1)
    assert a1.p_sub == pytest.approx(0.12)
    assert a1.avg_sub_time == pytest.approx(71.0)


# ── (c) identifiant inconnu → ignoré et signalé ─────────────────────

def test_identifiant_inconnu_ignore_et_signale():
    with patch.object(team_xg, "alert_bg") as alert:
        starters, warnings = select_lineup_starters(_squad(), STARTERS | {999_999}, "City")
    assert {p["player_id"] for p in starters} == STARTERS
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, InputWarning)
    assert w.code == "UNKNOWN_STARTER_ID"
    assert w.player_id == 999_999
    alert.assert_called_once()
    assert alert.call_args.args[1] == "incidents"


def test_identifiant_inconnu_ne_fait_pas_echouer_le_calcul():
    with patch.object(team_xg, "alert_bg"):
        allocs = compute_lineup_allocation(_squad(), STARTERS | {999_999}, "City", TEAM_XG)
    assert {a.player_id for a in allocs} == STARTERS


def test_moins_de_cinq_titulaires_reconnus_rend_vide_et_signale():
    with patch.object(team_xg, "alert_bg"):
        starters, warnings = select_lineup_starters(_squad(), {1, 2, 3, 77, 78}, "City")
        allocs = compute_lineup_allocation(_squad(), {1, 2, 3, 77, 78}, "City", TEAM_XG)
    assert allocs == []
    assert any(w.code == "TOO_FEW_STARTERS" for w in warnings)


# ── (b) Beta suit la compo ──────────────────────────────────────────

def _fixture() -> MagicMock:
    fx = MagicMock()
    fx.id = 1
    fx.league = "premier_league"
    fx.home_team = "City"
    fx.away_team = "Napoli"
    fx.home_bzz_team_id = 63
    fx.away_bzz_team_id = 62
    fx.external_id = None
    return fx


@pytest.mark.asyncio
async def test_beta_compo_differe_de_beta_effectif():
    from app.pricing.beta_pricing import compute_beta_allocations

    squad = _squad()

    async def fake_load(_db, team, bzz_team_id=None):
        return [dict(p) for p in squad] if team == "City" else []

    rhythm = BlendedRhythm(goal_rate_per_90=0.60, assist_rate_per_90=0.15,
                           seasons_used=3, has_career=True)

    async def fake_rhythm(_db, pid):
        return rhythm if pid == 1 else None

    alpha_full = [
        team_xg.allocate_player(s, TEAM_XG, False, TEAM_XG * team_xg.ASSIST_GOAL_RATE)
        for s in compute_player_shares(squad, "City", lambda_team=TEAM_XG)
    ]
    alpha_lineup = compute_lineup_allocation(squad, STARTERS, "City", TEAM_XG)

    with patch("app.pricing.beta_pricing._load_team_players", new=AsyncMock(side_effect=fake_load)), \
         patch("app.pricing.beta_pricing.blended_rhythm", new=AsyncMock(side_effect=fake_rhythm)):
        home_beta, away_beta, home_lineup_beta, away_lineup_beta = await compute_beta_allocations(
            AsyncMock(), _fixture(), alpha_full, [], TEAM_XG, 1.1,
            home_starter_ids=STARTERS,
            alpha_home_lineup=alpha_lineup,
        )

    assert set(home_lineup_beta) == STARTERS
    assert away_lineup_beta == {}
    # Joueur avec carrière : Beta compo ≠ Beta effectif complet
    assert home_lineup_beta[1].fair_odds_goal_supersub < home_beta[1].fair_odds_goal_supersub
    # Beta suit la même règle : ses postes totalisent le xG de l'équipe
    total = sum(_lambda_poste_but(a) for a in home_lineup_beta.values())
    assert total == pytest.approx(TEAM_XG, rel=0.01)


@pytest.mark.asyncio
async def test_api_les_listes_compo_portent_le_beta_de_la_compo():
    from app.api.pricing import MatchPriceRequest, price_match
    from app.pricing.team_xg import MatchPricingResult

    squad = _squad()
    alpha_lineup = compute_lineup_allocation(squad, STARTERS, "City", TEAM_XG)
    alpha_full = [
        team_xg.allocate_player(s, TEAM_XG, False, TEAM_XG * team_xg.ASSIST_GOAL_RATE)
        for s in compute_player_shares(squad, "City", lambda_team=TEAM_XG)
    ]
    fx = _fixture()
    pricing = MatchPricingResult(
        fixture_id=1, home_team="City", away_team="Napoli",
        home_match_xg=TEAM_XG, away_match_xg=1.1,
        home_players=alpha_full, away_players=[],
        home_lineup_players=alpha_lineup,
    )
    full_beta = {a.player_id: a for a in alpha_full}
    lineup_beta = {a.player_id: a for a in alpha_lineup}

    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = fx
    db.execute = AsyncMock(return_value=res)

    load = AsyncMock(return_value=pricing)
    beta = AsyncMock(return_value=(full_beta, {}, lineup_beta, {}))
    with patch("app.pricing.team_xg.load_match_pricing", new=load), \
         patch("app.pricing.beta_pricing.compute_beta_allocations", new=beta), \
         patch("app.api.pricing.badge_fields_for_fixtures", new=AsyncMock(return_value={1: {}})):
        resp = await price_match(
            MatchPriceRequest(fixture_id=1, home_starter_ids=sorted(STARTERS)), db=db,
        )

    assert load.call_args.kwargs["home_starter_ids"] == sorted(STARTERS)
    assert beta.call_args.kwargs["home_starter_ids"] == sorted(STARTERS)
    out = {p.player_id: p for p in resp.home_lineup_players}
    assert out[1].beta_fair_odds_goal_supersub == lineup_beta[1].fair_odds_goal_supersub
    assert out[1].beta_fair_odds_goal_supersub != full_beta[1].fair_odds_goal_supersub


# ── (d) structurel : plus aucun rapprochement de nom ────────────────

def test_plus_de_rapprochement_de_nom_de_titulaire():
    assert not hasattr(team_xg, "_lineup_name_compatible")
    assert not hasattr(team_xg, "_match_tokens")
    params = inspect.signature(compute_lineup_allocation).parameters
    assert "starter_ids" in params and "starter_names" not in params
    params = inspect.signature(team_xg.load_match_pricing).parameters
    assert "home_starter_ids" in params and "home_starters" not in params

    from app.api.pricing import MatchPriceRequest
    fields = MatchPriceRequest.model_fields
    assert "home_starter_ids" in fields and "away_starter_ids" in fields
    assert "home_starters" not in fields and "away_starters" not in fields

    src = (BACKEND / "app/api/pricing.py").read_text()
    assert "home_starters" not in src and "away_starters" not in src


def test_front_envoie_des_identifiants():
    front = BACKEND.parent / "frontend/src"
    api_ts = (front / "lib/api.ts").read_text()
    assert "home_starter_ids" in api_ts and "home_starters?" not in api_ts
    page = (front / "app/dashboard/calculator/page.tsx").read_text()
    assert "home_starter_ids" in page and "home_starters:" not in page


# ── Compo officielle : identifiants lus sur la compo Bzzoiro ────────

@pytest.mark.asyncio
async def test_compo_officielle_rend_les_identifiants_des_titulaires_de_champ():
    from types import SimpleNamespace

    import app.api.lineups as lineups_api

    lineup = SimpleNamespace(lineup_type="bzzoiro", fixture_id=7, team="Manchester City")
    fx = SimpleNamespace(id=7, external_id="bzz_1180285", home_team="Manchester City")

    async def get(model, key):
        return lineup if model is lineups_api.TeamLineup else fx

    slots = [
        SimpleNamespace(player_api_id=10, position="G", player_name="Donnarumma"),
        SimpleNamespace(player_api_id=11, position="D", player_name="Rúben Dias"),
        SimpleNamespace(player_api_id=None, position="M", player_name="E. Anderson"),
        SimpleNamespace(player_api_id=12, position="F", player_name="Erling Haaland"),
    ]
    res = MagicMock()
    res.scalars.return_value.all.return_value = slots
    session = AsyncMock()
    session.get = AsyncMock(side_effect=get)
    session.execute = AsyncMock(return_value=res)

    with patch.object(lineups_api.logger, "warning") as warn:
        ids = await lineups_api._starter_ids_for_lineup(99, session)

    assert ids == [11, 12]  # gardien exclu, place sans identifiant exclue
    warn.assert_called_once()  # … et journalisée (pas d'alerte sur un chargement de page)
    assert "E. Anderson" in str(warn.call_args.args)
    assert not hasattr(lineups_api, "alert_bg")


def _manual_session(players):
    from types import SimpleNamespace

    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(
        lineup_type="probable_manual", fixture_id=7, team="X"))
    res = MagicMock()
    res.scalars.return_value.all.return_value = players
    session.execute = AsyncMock(return_value=res)
    return session


@pytest.mark.asyncio
async def test_compo_enregistree_avec_identifiants_est_appliquee():
    from types import SimpleNamespace

    import app.api.lineups as lineups_api

    players = [
        SimpleNamespace(player_api_id=i, is_starter=True, position="MF") for i in range(1, 11)
    ] + [SimpleNamespace(player_api_id=50, is_starter=False, position="MF")]
    ids = await lineups_api._starter_ids_for_lineup(99, _manual_session(players))
    assert ids == list(range(1, 11))


@pytest.mark.asyncio
async def test_compo_enregistree_sans_identifiants_n_a_pas_d_identifiants():
    from types import SimpleNamespace

    import app.api.lineups as lineups_api

    players = [SimpleNamespace(player_api_id=None, is_starter=True, position="MF")] * 10
    assert await lineups_api._starter_ids_for_lineup(99, _manual_session(players)) is None


# ── Résolution : une compo enregistrée sans identifiants est sautée ─

def _lu(lineup_type, players, lid=1):
    from types import SimpleNamespace
    return SimpleNamespace(lineup_type=lineup_type, players=players, id=lid)


def _joueur(pid, starter=True):
    from types import SimpleNamespace
    return SimpleNamespace(player_api_id=pid, is_starter=starter, position="MF")


@pytest.mark.asyncio
async def test_resolution_compo_enregistree_avec_identifiants_gagne():
    from app.ingestion.lineup_resolver import resolve_lineup

    manual = _lu("probable_manual", [_joueur(i) for i in range(1, 11)], lid=2)
    last = _lu("last_known", [_joueur(i) for i in range(20, 30)], lid=3)
    res = await resolve_lineup(1, "City", None, _overrides=[last, manual])
    assert res.lineup_type == "probable_manual" and res.lineup_id == 2


@pytest.mark.asyncio
async def test_resolution_compo_sans_identifiants_sautee_au_profit_de_la_derniere_officielle():
    import app.ingestion.lineup_resolver as resolver

    manual = _lu("probable_manual", [_joueur(None) for _ in range(10)], lid=2)
    last = _lu("last_known", [_joueur(i) for i in range(20, 30)], lid=3)
    with patch.object(resolver.logger, "warning") as warn:
        res = await resolver.resolve_lineup(1, "City", None, _overrides=[last, manual])
    assert res.lineup_type == "last_known" and res.lineup_id == 3
    warn.assert_called_once()


@pytest.mark.asyncio
async def test_resolution_base_saute_la_compo_sans_identifiants():
    """Chemin base : la compo manuelle sans id est sautée, la recherche passe
    à la dernière compo officielle (avec identifiants)."""
    from datetime import datetime

    from app.ingestion.lineup_resolver import resolve_lineup

    manual = _lu("probable_manual", [], lid=2)
    prev_official = _lu("official", [], lid=3)

    def _res(*, all_=None, one=None):
        r = MagicMock()
        r.scalars.return_value.all.return_value = all_ or []
        r.scalar_one_or_none.return_value = one
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _res(all_=[manual]),                                   # compos du match
        _res(all_=[_joueur(None) for _ in range(10)]),         # joueurs manuels sans id
        _res(one=datetime(2026, 11, 25, tzinfo=UTC)),  # coup d'envoi
        _res(one=prev_official),                               # dernière officielle
        _res(all_=[_joueur(i) for i in range(20, 30)]),        # ses joueurs
    ])
    res = await resolve_lineup(1, "City", session)
    assert res.lineup_type == "last_known" and res.lineup_id == 3


def test_sauvegarde_envoie_et_stocke_les_identifiants():
    from app.api.lineups import LineupPlayerIn

    assert "player_api_id" in LineupPlayerIn.model_fields
    src = (BACKEND / "app/api/lineups.py").read_text()
    assert "player_api_id=p.player_api_id" in src
    # Ecran du calculateur (maquette D) : titulaires envoyes par identifiant,
    # jamais par nom ; compo absente = message visible, jamais un no-op muet.
    page = (BACKEND.parent / "frontend/src/app/dashboard/calculator/page.tsx").read_text()
    assert "home_starter_ids:" in page and "away_starter_ids:" in page
    assert "starter_ids ?? []" in page
    assert "Pas de compo connue : coche les titulaires." in page


def test_alerte_incidents_du_calculateur_une_seule_fois(monkeypatch):
    """Le calcul tourne toutes les 60 s : même message → une seule alerte."""
    envoyes = []
    monkeypatch.setattr(team_xg, "alert_bg", lambda m, c: envoyes.append((m, c)))
    monkeypatch.setattr(team_xg, "_incidents_envoyes", set())
    for _ in range(3):
        team_xg._alert_once("Calculateur : équipe non reliée à l'Atlas — X")
    team_xg._alert_once("Calculateur : équipe non reliée à l'Atlas — Y")
    assert envoyes == [
        ("Calculateur : équipe non reliée à l'Atlas — X", "incidents"),
        ("Calculateur : équipe non reliée à l'Atlas — Y", "incidents"),
    ]
