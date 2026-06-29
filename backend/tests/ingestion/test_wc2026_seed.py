"""Tests for WC 2026 squad seed parser."""

from app.ingestion.wc2026.seed_squads import _parse_squads, _NATION_META


# Match the real Wikipedia column order: No. / Pos. / Player / DOB / Caps / Goals / Club
SAMPLE_HTML = """
<html><body>
<h2><span class="mw-headline" id="Group_A">Group A</span></h2>
<h3><span class="mw-headline" id="Mexico">Mexico</span></h3>
<table class="wikitable">
<tr><th>No.</th><th>Pos.</th><th>Player</th><th>Date of birth</th><th>Caps</th><th>Goals</th><th>Club</th></tr>
<tr><td>1</td><td>1GK</td><td><a href="/wiki/G_Ochoa">Guillermo Ochoa</a></td><td>1985</td><td>150</td><td>0</td><td><a href="/wiki/AEL">AEL Limassol</a></td></tr>
<tr><td>9</td><td>4FW</td><td><a href="/wiki/R_Jimenez">Raúl Jiménez</a></td><td>1991</td><td>120</td><td>30</td><td><a href="/wiki/Fulham">Fulham</a></td></tr>
</table>
<h3><span class="mw-headline" id="South_Korea">South Korea</span></h3>
<table class="wikitable">
<tr><th>No.</th><th>Pos.</th><th>Player</th><th>Date of birth</th><th>Caps</th><th>Goals</th><th>Club</th></tr>
<tr><td>7</td><td>3MF</td><td><a href="/wiki/Son">Son Heung-min</a></td><td>1992</td><td>130</td><td>35</td><td><a href="/wiki/Tottenham">Tottenham</a></td></tr>
</table>
</body></html>
"""


def test_parse_squads_extracts_players():
    players = _parse_squads(SAMPLE_HTML)
    assert len(players) == 3


def test_parse_squads_positions_mapped():
    players = _parse_squads(SAMPLE_HTML)
    positions = {p["player_name"]: p["position"] for p in players}
    assert positions["Guillermo Ochoa"] == "GK"
    assert positions["Raúl Jiménez"] == "FWD"
    assert positions["Son Heung-min"] == "MID"


def test_parse_squads_nation_assigned():
    players = _parse_squads(SAMPLE_HTML)
    mexico_players = [p for p in players if p["nation_en"] == "Mexico"]
    assert len(mexico_players) == 2
    korea_players = [p for p in players if p["nation_en"] == "South Korea"]
    assert len(korea_players) == 1


def test_parse_squads_group_assigned():
    players = _parse_squads(SAMPLE_HTML)
    assert all(p["group_letter"] == "A" for p in players)


def test_parse_squads_shirt_number():
    players = _parse_squads(SAMPLE_HTML)
    ochoa = next(p for p in players if p["player_name"] == "Guillermo Ochoa")
    assert ochoa["shirt_number"] == 1


def test_nation_meta_has_flag_for_mexico():
    meta = _NATION_META.get("Mexico")
    assert meta is not None
    assert meta["flag"] != ""


def test_nation_meta_french_name():
    meta = _NATION_META.get("France")
    assert meta is not None
    assert meta["fr"] == "France"


def test_parse_squads_numeric_prefixed_positions():
    """Wikipedia uses sort-key prefixes like '1GK', '2DF', '3MF', '4FW'."""
    players = _parse_squads(SAMPLE_HTML)
    positions = {p["player_name"]: p["position"] for p in players}
    assert positions["Guillermo Ochoa"] == "GK"
    assert positions["Raúl Jiménez"] == "FWD"
    assert positions["Son Heung-min"] == "MID"


def test_parse_squads_club_from_last_column():
    players = _parse_squads(SAMPLE_HTML)
    ochoa = next(p for p in players if p["player_name"] == "Guillermo Ochoa")
    assert ochoa["club"] == "AEL Limassol"


def test_parse_squads_club_skips_flagicon_link():
    """Club cell may have a flag <a><img></a> before the real club <a> — must skip it."""
    html = """
<html><body>
<h2 id="Group_A">Group A</h2>
<h3 id="Mexico">Mexico</h3>
<table class="wikitable">
<tr><th>No.</th><th>Pos.</th><th>Player</th><th>DOB</th><th>Caps</th><th>Goals</th><th>Club</th></tr>
<tr><td>1</td><td>1GK</td><td><a href="#">Ochoa</a></td><td>1985</td><td>10</td><td>0</td>
<td><span class="flagicon"><a href="#"><img alt="flag"/></a></span><a href="#">Monterrey</a></td>
</tr>
</table>
</body></html>
"""
    players = _parse_squads(html)
    assert len(players) == 1
    assert players[0]["club"] == "Monterrey"
