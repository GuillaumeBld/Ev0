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
    # la liste vide se lie en '[]'::jsonb ; cast("[]", JSONB) produirait
    # '"[]"', une chaine JSON qui ne correspondrait a rien
    assert [] in valeurs
    # une liste POURVUE ne doit pas etre reprise : sans cette contrainte,
    # chaque passage retraiterait tous les matchs deja faits
    assert "jsonb_array_length" not in texte
