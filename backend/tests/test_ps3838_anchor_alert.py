from datetime import UTC, datetime, timedelta

from app.worker import _stale_odds_alert_lines, _unanchored_alert_lines

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def test_fixture_within_seven_days_is_reported():
    rows = [("Atlético Madrid - Málaga CF", NOW + timedelta(days=3))]
    lines = _unanchored_alert_lines(rows, NOW)
    assert len(lines) == 1
    assert "Atlético Madrid" in lines[0]


def test_fixture_beyond_seven_days_is_silent():
    rows = [("Lille - Paris Saint-Germain", NOW + timedelta(days=9))]
    assert _unanchored_alert_lines(rows, NOW) == []


def test_boundary_at_seven_days():
    assert _unanchored_alert_lines([("A - B", NOW + timedelta(days=7))], NOW) == []
    assert len(_unanchored_alert_lines([("A - B", NOW + timedelta(days=6, hours=23))], NOW)) == 1


def test_past_fixture_is_ignored():
    assert _unanchored_alert_lines([("A - B", NOW - timedelta(hours=1))], NOW) == []


def test_empty_input_is_silent():
    assert _unanchored_alert_lines([], NOW) == []


def test_ampersand_in_label_is_escaped():
    """Un & non echappe casse le parsing HTML Telegram et perd le message entier."""
    rows = [("Foo & Bar - Baz", NOW + timedelta(days=1))]
    lines = _unanchored_alert_lines(rows, NOW)
    assert len(lines) == 1
    assert "&amp;" in lines[0]
    # aucun '&' nu en dehors de l'entite HTML echappee
    assert lines[0].replace("&amp;", "").count("&") == 0


# ---------------------------------------------------------------------------
# I1 : matchs ANCRES mais sans cotes PS3838 exploitables recentes -- meme
# horizon de 7 jours, mais une cause distincte de _unanchored_alert_lines.
# ---------------------------------------------------------------------------


def test_stale_odds_fixture_within_seven_days_is_reported():
    rows = [("Atlético Madrid - Málaga CF", NOW + timedelta(days=3))]
    lines = _stale_odds_alert_lines(rows, NOW)
    assert len(lines) == 1
    assert "Atlético Madrid" in lines[0]


def test_stale_odds_fixture_beyond_seven_days_is_silent():
    rows = [("Lille - Paris Saint-Germain", NOW + timedelta(days=9))]
    assert _stale_odds_alert_lines(rows, NOW) == []


def test_stale_odds_label_is_distinguishable_from_unanchored_label():
    """La spec exige un libelle distinguant clairement les deux causes : un
    match non ancre et un match ancre-mais-sans-cotes ne doivent jamais
    produire la meme ligne d'alerte."""
    same_row = [("PSG - Marseille", NOW + timedelta(days=2))]
    unanchored_line = _unanchored_alert_lines(same_row, NOW)[0]
    stale_line = _stale_odds_alert_lines(same_row, NOW)[0]
    assert unanchored_line != stale_line
    assert "non ancré" in unanchored_line
    assert "sans cotes" in stale_line


def test_stale_odds_ampersand_in_label_is_escaped():
    rows = [("Foo & Bar - Baz", NOW + timedelta(days=1))]
    lines = _stale_odds_alert_lines(rows, NOW)
    assert len(lines) == 1
    assert "&amp;" in lines[0]
    assert lines[0].replace("&amp;", "").count("&") == 0


def test_stale_odds_empty_input_is_silent():
    assert _stale_odds_alert_lines([], NOW) == []
