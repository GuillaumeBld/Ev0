

def test_detect_market_type_rejects_team_scoped_markets():
    from app.ingestion.wc2026.sync_wc_outrights import _lvs_detect_market_type
    # Marchés d'équipe (phase KO) — ne doivent PAS matcher le tournoi
    assert _lvs_detect_market_type("Argentine : Meilleur Buteur") is None
    assert _lvs_detect_market_type("Angleterre : Nombre de buts") is None
    # Marché tournoi
    assert _lvs_detect_market_type("Meilleur buteur") == "top_scorer"
    assert _lvs_detect_market_type("Meilleur passeur du tournoi") == "top_assister"
