"""Transfermarkt est la SEULE autorite sur la composition d'un effectif.

Trois regressions constatees en production le 05/09/2026, qui melangeaient les
joueurs de deux clubs sur la page Joueurs :

  - un second systeme (`sync_loan_teams`) ecrivait `bzz_players.loan_team_*`
    en devinant le club d'un joueur d'apres ses feuilles de match. Apres un
    seul match, son club et son adversaire sont a egalite et le depart etait
    tire au hasard : Porro, Kudus et van de Ven (Tottenham) se sont retrouves
    "pretes a Newcastle" sur la foi du seul Tottenham-Newcastle du 29 aout ;
  - la page Joueurs affichait un joueur sur son club actuel OU son club de
    pret, donc le meme joueur apparaissait dans DEUX effectifs ;
  - le job quotidien ne synchronisait que les clubs deja ancres a
    Transfermarkt, sans jamais resoudre les nouveaux : 32 clubs des cinq
    grands championnats n'avaient aucun effectif synchronise depuis la
    bascule de saison, et les clubs hors des cinq grands (etrangers de Coupe
    d'Europe, relegues) n'etaient couverts par aucune page competition.

Ces tests portent sur la structure du code (constantes, requete, appels) : il
n'y a pas de base de test dans ce projet, et le comportement a ete verifie
separement sur la base reelle.
"""
from __future__ import annotations

import inspect

from app.ingestion.transfermarkt.resolve_clubs import TM_COMPETITION_CODES


def test_le_detecteur_de_prets_devine_n_existe_plus():
    """Il deduisait le club d'un joueur d'une statistique, pas de l'effectif."""
    import importlib

    for module in (
        "app.ingestion.bzzoiro.sync_loan_teams",
        "app.worker.job_sync_loan_teams",
    ):
        try:
            importlib.import_module(module)
        except (ImportError, ModuleNotFoundError):
            continue
        raise AssertionError(f"{module} devrait avoir disparu")


def test_aucune_tache_planifiee_ne_reecrit_les_prets():
    from app import worker

    source = inspect.getsource(worker)
    assert "sync_loan_teams" not in source
    assert not hasattr(worker, "job_sync_loan_teams")


def test_un_joueur_n_appartient_qu_a_son_club_actuel():
    """Le filtre par equipe ne doit plus retomber sur le club de pret."""
    from app.api import players

    source = inspect.getsource(players.list_players)
    assert "current_team_api_id == team_api_id" in source
    assert "loan_team_api_id == team_api_id" not in source


def test_les_clubs_hors_des_cinq_grands_sont_couverts():
    """Etrangers de Coupe d'Europe et relegues : sans ces pages competition,
    ils n'obtiennent jamais d'identifiant Transfermarkt, donc jamais
    d'effectif."""
    codes = set(TM_COMPETITION_CODES.values())

    # Premiers echelons etrangers des habitues de Coupe d'Europe.
    for code in ("NL1", "PO1", "BE1", "SC1", "A1", "UKR1", "KR1"):
        assert code in codes, f"{code} manquant"

    # Deuxiemes echelons des cinq grands : c'est la que vivent les relegues.
    for code in ("GB2", "L2", "FR2", "ES2", "IT2"):
        assert code in codes, f"{code} manquant"

    # Les cinq grands championnats restent evidemment couverts.
    for code in ("GB1", "FR1", "L1", "ES1", "IT1"):
        assert code in codes, f"{code} manquant"


def test_le_job_quotidien_ancre_les_nouveaux_clubs_avant_de_synchroniser():
    """Un promu entre au referentiel sans identifiant Transfermarkt. Si la
    resolution ne tourne que dans le script manuel, son effectif n'est jamais
    synchronise."""
    from app.worker import job_sync_squads

    source = inspect.getsource(job_sync_squads)
    assert "_resolve_clubs_blocking" in source

    position_resolution = source.index("_resolve_clubs_blocking")
    position_selection = source.index("transfermarkt_club_id.isnot(None)")
    assert position_resolution < position_selection, (
        "la resolution doit passer AVANT la selection des clubs, sinon un "
        "club ancre ce run n'est synchronise qu'au run suivant"
    )


def test_un_echec_de_resolution_ne_bloque_pas_la_synchro():
    """Transfermarkt injoignable ne doit pas priver d'effectif les clubs deja
    ancres — mais l'echec doit etre trace, jamais silencieux."""
    from app.worker import job_sync_squads

    source = inspect.getsource(job_sync_squads)
    bloc = source[source.index("_resolve_clubs_blocking"):]
    assert "except Exception" in bloc
    assert "logger.error" in bloc


def test_un_effectif_se_lit_en_entier():
    """Quand un club precis est demande, un joueur sans statistiques de la
    saison reste affiche (colonnes a tirets). Debut septembre, apres deux
    journees, le filtre sur les statistiques vidait les effectifs de leurs
    blesses et de leurs gardiens remplacants : 4 joueurs sur 25 disparaissaient
    a Newcastle, dont le gardien titulaire Nick Pope."""
    from app.api import players

    source = inspect.getsource(players.list_players)
    assert "garder_sans_stats" in source
    assert "team_api_id is not None" in source.split("garder_sans_stats")[1][:120]


def test_la_vue_par_championnat_reste_filtree():
    """Sans club demande, on classe des joueurs : y faire remonter des milliers
    de lignes vides n'aide personne."""
    from app.api import players

    source = inspect.getsource(players.list_players)
    bloc = source[source.index("garder_sans_stats ="):]
    assert "if not rows and not garder_sans_stats:" in bloc
    assert "continue" in bloc
