"""Ingestion des donnees de match depuis l'API v2.

Les colonnes shotmap / lineups / incidents de bzz_events existent depuis la
CDM mais sont restees vides pour le football de clubs : sync_events lit un
champ que l'API v1 ne renvoie pas. Mesure du 25/08/2026 : 0 compo et 0 carte
de tirs sur 8 965 matchs termines.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.bzzoiro.sync_match_detail import (
    doit_interroger,
    ecrire_compos,
    est_confirmee,
    fetch_incidents,
    fetch_lineups,
    fetch_match_stats,
    type_de_compo,
)

MAINTENANT = datetime(2026, 8, 25, 12, tzinfo=UTC)


def _reponse_compos(status="confirmed"):
    return {
        "event_id": 209544,
        "lineup_status": status,
        "updated_at": "2026-08-25T03:04:09Z",
        "lineups": {
            "home": {
                "formation": "4-2-3-1",
                "players": [
                    {"id": 823, "name": "Bernd Leno", "position": "G",
                     "jersey_number": 1, "captain": False},
                ],
                "substitutes": [
                    {"id": 999, "name": "Benjamin Lecomte", "position": "G",
                     "jersey_number": "12", "captain": False},
                ],
            },
            "away": {"formation": "3-4-2-1", "players": [], "substitutes": []},
        },
        "unavailable_players": {"home": [], "away": []},
    }


def _session():
    s = MagicMock()
    s.execute = AsyncMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    s.delete = AsyncMock()
    return s


# --- Recuperation ----------------------------------------------------------


async def test_fetch_compos_appelle_le_bon_point_d_acces():
    client = MagicMock()
    client.get_page = AsyncMock(return_value=_reponse_compos())

    res = await fetch_lineups(client, 209544)

    assert res["lineup_status"] == "confirmed"
    client.get_page.assert_called_once_with("/api/v2/events/209544/lineups/")


async def test_fetch_compos_absentes_rend_none():
    """Un match sans compo publiee ne doit pas lever."""
    client = MagicMock()
    client.get_page = AsyncMock(side_effect=Exception("404"))

    assert await fetch_lineups(client, 999) is None


async def test_fetch_stats_appelle_le_bon_point_d_acces():
    client = MagicMock()
    client.get_page = AsyncMock(return_value={"event_id": 1, "shotmap": [{"xg": 0.1}]})

    res = await fetch_match_stats(client, 209544)

    assert len(res["shotmap"]) == 1
    client.get_page.assert_called_once_with("/api/v2/events/209544/stats/")


async def test_fetch_incidents_deballe_la_liste():
    client = MagicMock()
    client.get_page = AsyncMock(return_value={"incidents": [{"type": "goal"}]})

    assert await fetch_incidents(client, 209544) == [{"type": "goal"}]


# --- Statut de la compo ----------------------------------------------------


def test_compo_confirmee():
    assert est_confirmee(_reponse_compos("confirmed")) is True
    assert est_confirmee(_reponse_compos("predicted")) is False
    assert est_confirmee(None) is False
    assert est_confirmee({}) is False


def test_type_de_compo_suit_le_statut():
    """official quand Bzzoiro confirme, bzzoiro sinon.

    Ce sont les deux types que PRIORITY connait deja : le resolveur prefere
    official (0) a bzzoiro (1) sans qu'aucune ligne ne soit a reecrire.
    """
    assert type_de_compo(_reponse_compos("confirmed")) == "official"
    assert type_de_compo(_reponse_compos("predicted")) == "bzzoiro"


# --- Regle d'interrogation -------------------------------------------------


def _session_compos(lignes):
    """lignes : liste de (lineup_type, updated_at)."""
    s = MagicMock()
    r = MagicMock()
    r.all.return_value = list(lignes)
    s.execute = AsyncMock(return_value=r)
    return s


async def test_interroge_quand_aucune_compo():
    ko = MAINTENANT + timedelta(hours=40)
    assert await doit_interroger(_session_compos([]), 7, ko, MAINTENANT) is True


async def test_n_interroge_plus_une_fois_l_officielle_connue():
    """Elle ne changera plus : inutile d'y revenir."""
    ko = MAINTENANT + timedelta(minutes=30)
    session = _session_compos([("official", MAINTENANT)])
    assert await doit_interroger(session, 7, ko, MAINTENANT) is False


async def test_probable_captee_suspend_la_veille():
    """Deux jours de requetes pour rien : on s'arrete apres la probable."""
    ko = MAINTENANT + timedelta(hours=40)
    session = _session_compos([("bzzoiro", MAINTENANT)])
    assert await doit_interroger(session, 7, ko, MAINTENANT) is False


async def test_controle_a_h_moins_24():
    """Une requete en entrant sous 24 h, pas une de plus."""
    ko = MAINTENANT + timedelta(hours=20)
    avant = ko - timedelta(hours=30)
    assert await doit_interroger(_session_compos([("bzzoiro", avant)]), 7, ko, MAINTENANT) is True
    dedans = ko - timedelta(hours=22)
    assert await doit_interroger(_session_compos([("bzzoiro", dedans)]), 7, ko, MAINTENANT) is False


async def test_controle_a_h_moins_6():
    ko = MAINTENANT + timedelta(hours=5)
    avant = ko - timedelta(hours=20)
    assert await doit_interroger(_session_compos([("bzzoiro", avant)]), 7, ko, MAINTENANT) is True
    dedans = ko - timedelta(hours=5, minutes=30)
    assert await doit_interroger(_session_compos([("bzzoiro", dedans)]), 7, ko, MAINTENANT) is False


async def test_veille_serree_dans_les_90_dernieres_minutes():
    """La compo officielle parait la : on interroge a chaque passage."""
    ko = MAINTENANT + timedelta(minutes=80)
    session = _session_compos([("bzzoiro", MAINTENANT)])
    assert await doit_interroger(session, 7, ko, MAINTENANT) is True


async def test_sans_coup_d_envoi_connu_on_interroge():
    """Mieux vaut une requete de trop qu'une compo manquee."""
    session = _session_compos([("bzzoiro", MAINTENANT)])
    assert await doit_interroger(session, 7, None, MAINTENANT) is True


# --- Ecriture des compos ---------------------------------------------------


async def test_ecrit_une_compo_par_camp_pourvu():
    """Le camp exterieur est vide dans cet exemple : une seule compo ecrite."""
    session = _session()
    vide = MagicMock()
    vide.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=vide)

    n = await ecrire_compos(
        session, fixture_id=7,
        equipes={"home": "Fulham", "away": "Chelsea"},
        brut=_reponse_compos("confirmed"),
    )

    assert n == 1


async def test_titulaires_et_remplacants_dans_la_meme_compo():
    session = _session()
    vide = MagicMock()
    vide.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=vide)

    await ecrire_compos(
        session, 7, {"home": "Fulham", "away": "Chelsea"}, _reponse_compos("confirmed")
    )

    ajoutes = [c.args[0] for c in session.add.call_args_list]
    joueurs = [o for o in ajoutes if hasattr(o, "player_name")]
    assert len(joueurs) == 2
    assert [j.is_starter for j in joueurs] == [True, False]
    # G -> GK : l'API et le modele n'utilisent pas le meme vocabulaire
    assert joueurs[0].position == "GK"
    # le numero de maillot est parfois une chaine
    assert joueurs[1].jersey_number == 12


async def test_ne_supprime_jamais_une_compo_d_un_autre_type():
    """La coexistence de bzzoiro et official est ce qui historise."""
    session = _session()
    vide = MagicMock()
    vide.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=vide)

    await ecrire_compos(
        session, 7, {"home": "Fulham", "away": "Chelsea"}, _reponse_compos("confirmed")
    )

    session.delete.assert_not_called()


async def test_compo_absente_n_ecrit_rien():
    session = _session()
    assert await ecrire_compos(session, 7, {"home": "A", "away": "B"}, None) == 0
    session.add.assert_not_called()


# --- Selection des matchs a rattraper ---------------------------------------


def test_sans_donnees_attrape_le_null_json():
    """Piege JSONB : sync_events a ecrit `null` au sens JSON, pas NULL au sens
    SQL. Un filtre `shotmap IS NULL` ne correspond a rien et le rattrapage
    annoncerait 0 traite indefiniment, sans que rien ne l'explique.

    Mesure du 25/08/2026 : sur 8 965 matchs termines, jsonb_typeof(shotmap)
    vaut 'null' pour les 8 965.
    """
    from app.ingestion.bzzoiro.sync_match_detail import sans_donnees

    compilee = sans_donnees().compile()
    texte = str(compilee)
    valeurs = list(compilee.params.values())

    # les trois formes de vide, et elles seules
    assert "IS NULL" in texte
    assert "jsonb_typeof" in texte
    assert "null" in valeurs
    # Une liste VIDE n'est PAS reprise : elle marque "verifie, Bzzoiro n'a
    # pas de tirs". Sans cela, les 6 200 matchs anterieurs a 2025 seraient
    # reinterroges a chaque passage horaire, indefiniment.
    assert [] not in valeurs
    # une liste POURVUE non plus : chaque passage retraiterait tout
    assert "jsonb_array_length" not in texte


async def test_match_sans_tirs_sort_de_la_file():
    """Bzzoiro connait le match mais n'a pas de tirs : on le marque.

    Les donnees de tirs ne remontent pas avant 2025. Verifie le 25/08/2026 :
    /api/v2/events/303685/stats/ (14/12/2024) rend des stats mais zero tir.
    """
    from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match

    ev = MagicMock()
    ev.api_id = 303685
    ev.shotmap = None

    session = MagicMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = [ev]
    session.execute = AsyncMock(return_value=r)
    session.commit = AsyncMock()

    client = MagicMock()
    client.get_page = AsyncMock(return_value={"stats": {"home": {}}, "shotmap": []})

    traites, sans_tirs = await sync_apres_match(session, client)

    assert (traites, sans_tirs) == (0, 1)
    # marque comme verifie : il ne reviendra plus dans la file
    assert ev.shotmap == []
    session.commit.assert_awaited()


async def test_appel_echoue_laisse_le_match_a_retenter():
    """Une panne reseau ne doit pas condamner un match."""
    from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match

    ev = MagicMock()
    ev.api_id = 1
    ev.shotmap = None

    session = MagicMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = [ev]
    session.execute = AsyncMock(return_value=r)
    session.commit = AsyncMock()

    client = MagicMock()
    client.get_page = AsyncMock(side_effect=Exception("502"))

    traites, sans_tirs = await sync_apres_match(session, client)

    assert (traites, sans_tirs) == (0, 0)
    # laisse a None : il sera repris au prochain passage
    assert ev.shotmap is None
    session.commit.assert_not_called()


# --- Compos des matchs termines --------------------------------------------
#
# Sans elles, le titulaire se devine par son temps de jeu : un titulaire
# remplace a la 60e compte pour un remplacant, un entrant de la 20e compte
# pour un titulaire. Verifie le 31/08/2026 : Bzzoiro rend une compo confirmee
# sur les cinq saisons, jusqu'au 31/12/2021.


def test_une_enveloppe_sans_joueur_n_est_pas_une_compo():
    from app.ingestion.bzzoiro.sync_match_detail import _a_des_titulaires

    assert _a_des_titulaires(_reponse_compos()) is True
    assert _a_des_titulaires({"lineups": {"home": {"players": []},
                                          "away": {"players": []}}}) is False
    assert _a_des_titulaires({"lineups": {}}) is False
    assert _a_des_titulaires({}) is False
    assert _a_des_titulaires(None) is False


def test_la_clause_compos_contourne_le_piege_jsonb():
    """`lineups IS NULL` ne correspond a rien quand la colonne vaut JSON null."""
    from app.ingestion.bzzoiro.sync_match_detail import sans_compos

    sql = str(sans_compos()).lower()
    assert "jsonb_typeof" in sql
    assert "lineups" in sql


def _ev(shotmap=None, lineups=None, api_id=1):
    ev = MagicMock()
    ev.api_id = api_id
    ev.shotmap = shotmap
    ev.lineups = lineups
    return ev


def _session_avec(evenements):
    session = MagicMock()
    r = MagicMock()
    r.scalars.return_value.all.return_value = evenements
    session.execute = AsyncMock(return_value=r)
    session.commit = AsyncMock()
    return session


async def test_la_compo_d_un_match_termine_est_archivee_en_entier():
    """Statut et horodatage comptent autant que les onze noms."""
    from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match

    ev = _ev(shotmap=[], lineups=None)
    session = _session_avec([ev])
    client = MagicMock()
    client.get_page = AsyncMock(return_value=_reponse_compos())

    await sync_apres_match(session, client)

    assert ev.lineups["lineup_status"] == "confirmed"
    assert ev.lineups["lineups"]["home"]["formation"] == "4-2-3-1"
    session.commit.assert_awaited()


async def test_un_match_sans_compo_chez_bzzoiro_sort_de_la_file():
    from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match

    ev = _ev(shotmap=[], lineups=None)
    session = _session_avec([ev])
    client = MagicMock()
    client.get_page = AsyncMock(return_value={"lineups": {"home": {}, "away": {}}})

    await sync_apres_match(session, client)

    assert ev.lineups == {}
    session.commit.assert_awaited()


async def test_un_echec_reseau_laisse_la_compo_a_reprendre():
    from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match

    ev = _ev(shotmap=[], lineups=None)
    session = _session_avec([ev])
    client = MagicMock()
    client.get_page = AsyncMock(side_effect=Exception("502"))

    await sync_apres_match(session, client)

    assert ev.lineups is None
    session.commit.assert_not_called()


async def test_une_compo_deja_prise_n_est_pas_redemandee():
    """Les deux files sont independantes : ici il ne manque que les tirs."""
    from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match

    ev = _ev(shotmap=None, lineups=_reponse_compos())
    session = _session_avec([ev])
    client = MagicMock()
    client.get_page = AsyncMock(return_value={"shotmap": [{"xg": 0.1}]})

    await sync_apres_match(session, client)

    appels = [c.args[0] for c in client.get_page.await_args_list]
    assert not any("lineups" in a for a in appels)


async def test_un_match_n_attendant_que_sa_compo_ne_redemande_pas_les_tirs():
    from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match

    ev = _ev(shotmap=[], lineups=None)
    session = _session_avec([ev])
    client = MagicMock()
    client.get_page = AsyncMock(return_value=_reponse_compos())

    await sync_apres_match(session, client)

    appels = [c.args[0] for c in client.get_page.await_args_list]
    assert appels == ["/api/v2/events/1/lineups/"]
