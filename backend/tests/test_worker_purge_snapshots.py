"""La purge des cotes ne doit plus detruire ce qui sert a calibrer.

Trois choses ne doivent jamais revenir :

  - toucher player_odds_snapshots. Cette table n'a pas d'historique : sa
    contrainte d'unicite ignore l'horodatage, donc une ligne = une selection =
    la derniere cote avant le coup d'envoi. La purger supprimait les matchs
    passes, c'est-a-dire la seule matiere de calibration du pricing joueur.
  - effacer l'ouverture ou la cloture d'une selection d'equipe. C'est ce
    couple qui mesure le marche ; le reste du mouvement de ligne est du
    remplissage.
  - effacer un snapshot cite par une estimation de xG d'equipe, ce qui la
    rendrait non rejouable.

Ces verifications portent sur la requete elle-meme : il n'y a pas de base de
test dans ce projet, et la semantique a ete controlee separement par
simulation sur la base reelle (368 points intermediaires supprimes, 202
ouvertures et clotures conservees, aucune selection laissee vide).
"""
import re

from app.worker import (
    SNAPSHOT_RETENTION_DAYS,
    _PURGE_INTERMEDIAIRES,
    job_purge_old_snapshots,
)

REQUETE = " ".join(_PURGE_INTERMEDIAIRES.split()).lower()


def test_la_purge_ne_touche_jamais_les_cotes_joueurs():
    assert "player_odds_snapshots" not in REQUETE


def test_la_purge_ne_supprime_que_dans_les_cotes_d_equipe():
    cibles = re.findall(r"delete from (\w+)", REQUETE)
    assert cibles == ["match_odds_snapshots"]


def test_l_ouverture_et_la_cloture_sont_epargnees():
    """Une ligne n'est supprimee que si elle n'est ni la premiere ni la derniere."""
    assert "rang_debut > 1" in REQUETE
    assert "rang_fin > 1" in REQUETE
    assert " and " in REQUETE.split("where t.rang_debut")[1][:40]


def test_le_classement_se_fait_par_selection_et_dans_le_temps():
    """Sans la bonne partition, on garderait une seule cote pour tout le match."""
    assert "partition by fixture_id, bookmaker, market_type, outcome" in REQUETE
    assert "order by snapshot_utc)" in REQUETE
    assert "order by snapshot_utc desc)" in REQUETE


def test_les_snapshots_ancres_a_un_xg_sont_proteges():
    assert "team_xg_estimates" in REQUETE
    assert "input_snapshot_ids" in REQUETE
    assert "not exists" in REQUETE


def test_le_type_jsonb_est_verifie_avant_deroulement():
    """input_snapshot_ids vaut parfois null : le derouler sans garde leve."""
    assert "jsonb_typeof(t.input_snapshot_ids) = 'array'" in REQUETE


def test_la_suppression_reste_par_lots():
    assert "limit :batch" in REQUETE


def test_le_delai_reste_parametre_et_inchange():
    assert "make_interval(days => :d)" in REQUETE
    assert SNAPSHOT_RETENTION_DAYS == 45


def test_le_job_reste_appelable():
    assert callable(job_purge_old_snapshots)
