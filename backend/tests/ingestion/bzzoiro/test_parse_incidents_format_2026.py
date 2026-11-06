"""Le parseur d'incidents doit lire le format Bzzoiro reel.

Regression du 06/11/2026. Bzzoiro a change de format en juin 2026 ; le
parseur cherchait toujours l'ancien et ne reconnaissait plus AUCUN incident.
Silencieusement : pas d'erreur, pas d'alerte, simplement zero but ingere de
juin a novembre.

    avant  {"incidentType": "goal", "player": {"name": "X"}, "assist1": {...}}
    depuis {"type": "goal", "player": "X", "assist": "Y", "goal_type": "regular"}

Consequence en bout de chaine : sans but en base, `auto_settle` ne peut
comparer un pari a rien, donc il annule. 8 514 paris annules sur 9 903
reglements, soit 86 %, et aucun verdict exploitable pour backtester.

Toutes les charges utiles de ce fichier sont COPIEES de la reponse reelle de
/api/v2/events/{id}/incidents/ le 06/11/2026, pas inventees.
"""
from app.ingestion.bzzoiro.sync_incidents import _parse_incidents

# ── Charges utiles reelles ────────────────────────────────────────

BUT_NORMAL_AVEC_PASSE = {
    "type": "goal", "assist": "T. Barry", "minute": 81,
    "player": "K. Dewsbury-Hall", "is_home": True, "goal_type": "regular",
    "player_id": 809, "added_time": None, "away_score": 1, "home_score": 3,
}

BUT_CONTRE_SON_CAMP = {
    "type": "goal", "minute": 57, "player": "M. Moreno", "is_home": False,
    "goal_type": "ownGoal", "player_id": 3927, "added_time": None,
    "away_score": 1, "home_score": 1,
}

BUT_SUR_PENALTY = {
    "type": "goal", "minute": 90, "player": "S. Aghehowa", "is_home": True,
    "goal_type": "penalty", "player_id": 1005, "added_time": None,
    "away_score": 1, "home_score": 4,
}

BUT_SANS_PASSE = {
    "type": "goal", "minute": 57, "player": "M. Gutiérrez", "is_home": True,
    "goal_type": "regular", "player_id": 3886, "added_time": None,
    "away_score": 0, "home_score": 2,
}

CHANGEMENT = {
    "type": "substitution", "minute": 89, "is_home": True,
    "player_in": "L. Vázquez", "player_in_id": 4210,
    "player_out": "A. Moreira", "player_out_id": 3301, "added_time": None,
}

CARTON = {
    "type": "card", "minute": 34, "player": "B. Henrichs", "player_id": 2201,
    "card_type": "yellow", "reason": "Foul", "is_home": False, "added_time": None,
}

PERIODE = {"text": "FT", "type": "period", "minute": 90, "is_live": False,
           "away_score": 0, "home_score": 2}

TEMPS_ADDITIONNEL = {"type": "injuryTime", "minute": 45, "length": 3}

PENALTY_MANQUE = {
    "type": "penalty_missed", "minute": 70, "player": "X. Simons",
    "player_id": 777, "reason": "Saved", "is_home": True, "added_time": None,
}


def _par_type(rows):
    out = {}
    for r in rows:
        out.setdefault(r["event_type"], []).append(r)
    return out


# ── Buteur ────────────────────────────────────────────────────────

def test_but_normal_lu_avec_son_identifiant():
    rows = _parse_incidents([BUT_SANS_PASSE])
    assert len(rows) == 1
    r = rows[0]
    assert r["player_name"] == "M. Gutiérrez"
    assert r["event_type"] == "goal"
    assert r["minute"] == 57
    assert r["player_id"] == 3886, "l'identifiant Bzzoiro doit etre conserve"


def test_penalty_distingue_du_but_normal():
    rows = _parse_incidents([BUT_SUR_PENALTY])
    assert [r["event_type"] for r in rows] == ["penalty_goal"]
    assert rows[0]["player_name"] == "S. Aghehowa"


def test_contre_son_camp_distingue_et_sans_passeur():
    """Un csc compte au score du match mais ne fait gagner aucun pari buteur."""
    rows = _parse_incidents([BUT_CONTRE_SON_CAMP])
    assert [r["event_type"] for r in rows] == ["own_goal"]
    assert rows[0]["player_name"] == "M. Moreno"


def test_goal_type_inconnu_compte_comme_but_normal():
    """Jeter un but inconnu rendrait la couverture incomplete et annulerait
    TOUS les paris du match -- bien pire que de le compter normal."""
    exotique = {**BUT_SANS_PASSE, "goal_type": "bicyclette_renversee"}
    rows = _parse_incidents([exotique])
    assert [r["event_type"] for r in rows] == ["goal"]


# ── Passeur ───────────────────────────────────────────────────────

def test_passe_decisive_extraite_du_but():
    rows = _parse_incidents([BUT_NORMAL_AVEC_PASSE])
    par_type = _par_type(rows)
    assert par_type["goal"][0]["player_name"] == "K. Dewsbury-Hall"
    assert par_type["assist"][0]["player_name"] == "T. Barry"
    assert par_type["assist"][0]["minute"] == 81, "meme minute que le but"


def test_but_sans_passe_ne_cree_pas_de_passeur():
    rows = _parse_incidents([BUT_SANS_PASSE])
    assert "assist" not in _par_type(rows)


# ── Changements ───────────────────────────────────────────────────

def test_changement_lu_entrant_et_sortant():
    """Decisif pour le reglement : un joueur jamais entre rend le pari nul."""
    rows = _parse_incidents([CHANGEMENT])
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "substitution"
    assert r["player_name"] == "L. Vázquez", "player_name = l'entrant"
    assert r["related_player_name"] == "A. Moreira", "related = le sortant"
    assert r["minute"] == 89
    assert r["player_id"] == 4210


# ── Ce qui doit etre ignore ───────────────────────────────────────

def test_cartons_periodes_et_temps_additionnel_ignores():
    rows = _parse_incidents([CARTON, PERIODE, TEMPS_ADDITIONNEL, PENALTY_MANQUE])
    assert rows == [], "seuls buts, passes et changements interessent le reglement"


# ── Une fiche de match entiere ────────────────────────────────────

def test_fiche_complete_dechiffree():
    """Leverkusen 2-0 : deux buts, une passe, un changement, du bruit."""
    fiche = [
        PERIODE, CARTON, BUT_SANS_PASSE, TEMPS_ADDITIONNEL,
        BUT_NORMAL_AVEC_PASSE, CHANGEMENT, PENALTY_MANQUE,
    ]
    par_type = _par_type(_parse_incidents(fiche))
    assert len(par_type["goal"]) == 2
    assert len(par_type["assist"]) == 1
    assert len(par_type["substitution"]) == 1
    assert "card" not in par_type and "period" not in par_type


def test_couverture_egale_au_score():
    """`auto_settle` ne regle gagne/perdu que si le nombre d'events "but"
    egale le score. Un but manque annule tous les paris du match."""
    fiche = [BUT_SANS_PASSE, BUT_NORMAL_AVEC_PASSE, BUT_SUR_PENALTY,
             BUT_CONTRE_SON_CAMP, CARTON, PERIODE]
    rows = _parse_incidents(fiche)
    comptes_au_score = [r for r in rows
                        if r["event_type"] in ("goal", "own_goal", "penalty_goal")]
    assert len(comptes_au_score) == 4, "les 4 buts, csc et penalty inclus"


# ── Compatibilite avec l'ancien format ────────────────────────────

def test_ancien_format_toujours_lu():
    """Un retour en arriere cote Bzzoiro ne doit rien casser."""
    ancien_but = {
        "incidentType": "goal", "time": 23,
        "player": {"name": "Ancien Buteur"},
        "assist1": {"name": "Ancien Passeur"},
        "isOwnGoal": False,
    }
    ancien_changement = {
        "incidentType": "substitution", "time": 60,
        "playerIn": {"name": "Entrant"}, "playerOut": {"name": "Sortant"},
    }
    par_type = _par_type(_parse_incidents([ancien_but, ancien_changement]))
    assert par_type["goal"][0]["player_name"] == "Ancien Buteur"
    assert par_type["assist"][0]["player_name"] == "Ancien Passeur"
    assert par_type["substitution"][0]["player_name"] == "Entrant"
    assert par_type["substitution"][0]["related_player_name"] == "Sortant"


def test_ancien_format_csc():
    ancien_csc = {
        "incidentType": "goal", "time": 10,
        "player": {"name": "Malchanceux"}, "isOwnGoal": True,
        "assist1": {"name": "Ne doit pas apparaitre"},
    }
    par_type = _par_type(_parse_incidents([ancien_csc]))
    assert par_type["own_goal"][0]["player_name"] == "Malchanceux"
    assert "assist" not in par_type


# ── Robustesse ────────────────────────────────────────────────────

def test_but_sans_buteur_identifiable_ignore():
    assert _parse_incidents([{"type": "goal", "minute": 5, "player": ""}]) == []
    assert _parse_incidents([{"type": "goal", "minute": 5}]) == []


def test_changement_incomplet_ignore():
    assert _parse_incidents([{"type": "substitution", "minute": 5,
                              "player_in": "Seul"}]) == []


def test_liste_vide():
    assert _parse_incidents([]) == []
