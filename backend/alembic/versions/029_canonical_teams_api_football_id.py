"""canonical_teams: add api_football_id and name_en

Revision ID: 029
Revises: 028
Create Date: 2026-05-21
"""
import sqlalchemy as sa
from alembic import op

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None

# Mapping: name_fr -> (api_football_id, name_en)
# name_en is None when identical to name_fr or unknown
_DATA: dict[str, tuple[int | None, str | None]] = {
    "Ajax":                (194,  "AFC Ajax"),
    "Angers":              (76,   "Angers SCO"),
    "Arsenal":             (42,   None),
    "Aston Villa":         (66,   None),
    "Atalanta":            (499,  "Atalanta BC"),
    "Athletic Bilbao":     (531,  "Athletic Club"),
    "Atlético Madrid":     (530,  "Atletico Madrid"),
    "Augsbourg":           (186,  "Augsburg"),
    "Auxerre":             (77,   "AJ Auxerre"),
    "Barcelone":           (529,  "Barcelona"),
    "Bayern Munich":       (157,  "FC Bayern München"),
    "Benfica":             (211,  "SL Benfica"),
    "Betis":               (543,  "Real Betis"),
    "Bochum":              (177,  "VfL Bochum"),
    "Bournemouth":         (35,   "AFC Bournemouth"),
    "Brentford":           (55,   None),
    "Brest":               (130,  "Stade Brestois 29"),
    "Brighton":            (51,   "Brighton & Hove Albion"),
    "Bruges":              (550,  "Club Brugge"),
    "Celtic":              (371,  "Celtic FC"),
    "Chelsea":             (49,   None),
    "Clermont":            (115,  "Clermont Foot"),
    "Cologne":             (167,  "FC Köln"),
    "Crystal Palace":      (52,   None),
    "Darmstadt":           (185,  "SV Darmstadt 98"),
    "Dinamo Zagreb":       (454,  "GNK Dinamo Zagreb"),
    "Dortmund":            (165,  "Borussia Dortmund"),
    "Everton":             (45,   None),
    "Feyenoord":           (209,  None),
    "Fiorentina":          (502,  "ACF Fiorentina"),
    "Francfort":           (169,  "Eintracht Frankfurt"),
    "Fribourg":            (160,  "SC Freiburg"),
    "Fulham":              (36,   None),
    "Heidenheim":          (180,  "1. FC Heidenheim"),
    "Hoffenheim":          (None, "TSG Hoffenheim"),  # ID uncertain, skip
    "Inter Milan":         (505,  "Internazionale"),
    "Ipswich":             (57,   "Ipswich Town"),
    "Juventus":            (496,  None),
    "Kiel":                (178,  "Holstein Kiel"),
    "Lazio":               (487,  "SS Lazio"),
    "Le Havre":            (1093, "Le Havre AC"),
    "Leeds":               (63,   "Leeds United"),
    "Leicester":           (46,   "Leicester City"),
    "Leipzig":             (173,  "RB Leipzig"),
    "Lens":                (116,  "RC Lens"),
    "Leverkusen":          (168,  "Bayer Leverkusen"),
    "Lille":               (79,   "LOSC Lille"),
    "Liverpool":           (40,   None),
    "Lorient":             (114,  "FC Lorient"),
    "Lyon":                (80,   "Olympique Lyonnais"),
    "Manchester City":     (50,   None),
    "Manchester United":   (33,   None),
    "Marseille":           (81,   "Olympique de Marseille"),
    "Mayence":             (164,  "1. FSV Mainz 05"),
    "Metz":                (112,  "FC Metz"),
    "Milan":               (489,  "AC Milan"),
    "Monaco":              (91,   "AS Monaco"),
    "Montpellier":         (92,   "Montpellier HSC"),
    "Mönchengladbach":     (163,  "Borussia Mönchengladbach"),
    "Nantes":              (83,   "FC Nantes"),
    "Naples":              (492,  "Napoli"),
    "Newcastle":           (34,   "Newcastle United"),
    "Nice":                (84,   "OGC Nice"),
    "Nottingham Forest":   (65,   None),
    "PSV Eindhoven":       (197,  None),
    "Paris Saint-Germain": (85,   None),
    "Porto":               (212,  "FC Porto"),
    "Rangers":             (369,  "Rangers FC"),
    "Real Madrid":         (541,  None),
    "Real Sociedad":       (548,  None),
    "Red Bull Salzbourg":  (1062, "Red Bull Salzburg"),
    "Reims":               (82,   "Stade de Reims"),
    "Rennes":              (111,  "Stade Rennais"),
    "Rodez":               (1082, "Rodez AF"),
    "Roma":                (497,  "AS Roma"),
    "Saint-Pauli":         (183,  "FC St. Pauli"),
    "Saint-Étienne":       (94,   "AS Saint-Étienne"),
    "Shakhtar":            (440,  "Shakhtar Donetsk"),
    "Southampton":         (41,   None),
    "Sporting CP":         (228,  None),
    "Strasbourg":          (95,   "RC Strasbourg"),
    "Stuttgart":           (172,  "VfB Stuttgart"),
    "Sunderland":          (71,   None),
    "Séville":             (536,  "Sevilla"),
    "Tottenham":           (47,   "Tottenham Hotspur"),
    "Toulouse":            (96,   "Toulouse FC"),
    "Union Berlin":        (182,  "1. FC Union Berlin"),
    "Valence":             (532,  "Valencia"),
    "Villarreal":          (533,  None),
    "Werder Brême":        (162,  "Werder Bremen"),
    "West Ham":            (48,   "West Ham United"),
    "Wolfsbourg":          (161,  "VfL Wolfsburg"),
    "Wolverhampton":       (39,   "Wolverhampton Wanderers"),
}


def upgrade() -> None:
    op.add_column("canonical_teams", sa.Column("api_football_id", sa.Integer(), nullable=True))
    op.add_column("canonical_teams", sa.Column("name_en", sa.String(200), nullable=True))
    op.create_unique_constraint("uq_canonical_teams_api_football_id", "canonical_teams", ["api_football_id"])

    conn = op.get_bind()
    for name_fr, (api_id, name_en) in _DATA.items():
        conn.execute(
            sa.text(
                "UPDATE canonical_teams SET api_football_id = :api_id, name_en = :name_en"
                " WHERE name_fr = :name_fr"
            ),
            {"api_id": api_id, "name_en": name_en, "name_fr": name_fr},
        )


def downgrade() -> None:
    op.drop_constraint("uq_canonical_teams_api_football_id", "canonical_teams")
    op.drop_column("canonical_teams", "name_en")
    op.drop_column("canonical_teams", "api_football_id")
