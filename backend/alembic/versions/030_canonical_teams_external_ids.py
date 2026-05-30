"""canonical_teams: add bzz_team_id and sofascore_team_id

Revision ID: 030
Revises: 029
Create Date: 2026-05-22
"""
import unicodedata
import sqlalchemy as sa
from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def _norm(s: str) -> str:
    """Accent-insensitive, punctuation-insensitive lowercase."""
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    # hyphens → space, remove common noise
    s = s.replace("-", " ").replace(".", "").replace("'", "").replace(",", "")
    # strip club suffixes for matching
    for suffix in (" fc", " ac", " sc", " cf", " af", " asc", " rc", " as", " vfl", " vfb", " rb", " fsv", " 1."):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip()


def upgrade() -> None:
    op.add_column("canonical_teams", sa.Column("bzz_team_id", sa.Integer(), nullable=True))
    op.add_column("canonical_teams", sa.Column("sofascore_team_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_canonical_teams_bzz_team_id", "canonical_teams", ["bzz_team_id"])
    op.create_unique_constraint("uq_canonical_teams_sofascore_team_id", "canonical_teams", ["sofascore_team_id"])

    # Auto-populate bzz_team_id by matching canonical team names → bzz_teams names
    conn = op.get_bind()

    canonical = conn.execute(sa.text(
        "SELECT id, name_fr, name_en, aliases FROM canonical_teams"
    )).fetchall()

    bzz = conn.execute(sa.text(
        "SELECT DISTINCT ON (api_id) api_id, name FROM bzz_teams ORDER BY api_id, name"
    )).fetchall()

    # Build normalized bzz lookup: norm_name → api_id
    bzz_map: dict[str, int] = {}
    for row in bzz:
        bzz_map[_norm(row.name)] = row.api_id

    matched = 0
    for ct in canonical:
        candidates = [ct.name_fr]
        if ct.name_en:
            candidates.append(ct.name_en)
        for alias in (ct.aliases or []):
            candidates.append(alias)

        found_id = None
        for c in candidates:
            key = _norm(c)
            if key in bzz_map:
                found_id = bzz_map[key]
                break
            # Also try without last word (e.g. "Manchester City" → "manchester")
            parts = key.split()
            if len(parts) > 1:
                for partial in [" ".join(parts[:-1]), " ".join(parts[1:])]:
                    if partial in bzz_map:
                        found_id = bzz_map[partial]
                        break
            if found_id:
                break

        if found_id is not None:
            conn.execute(sa.text(
                "UPDATE canonical_teams SET bzz_team_id = :bzz_id WHERE id = :ct_id"
            ), {"bzz_id": found_id, "ct_id": ct.id})
            matched += 1

    print(f"  bzz_team_id: {matched}/{len(canonical)} canonical teams matched")

    # Manual corrections: auto-matching picked wrong bzz api_id for these teams
    # (bzz_teams has multiple entries; the correct one is the UCL/international-facing ID)
    overrides = {
        "Atlético Madrid": 54,
        "Bayern Munich":   79,
        "Porto":           35,
        "Betis":           56,
        "Wolfsbourg":      82,
    }
    for name_fr, bzz_id in overrides.items():
        conn.execute(sa.text(
            "UPDATE canonical_teams SET bzz_team_id = :bzz_id WHERE name_fr = :name"
        ), {"bzz_id": bzz_id, "name": name_fr})
    print(f"  bzz_team_id: {len(overrides)} manual overrides applied")


def downgrade() -> None:
    op.drop_constraint("uq_canonical_teams_sofascore_team_id", "canonical_teams")
    op.drop_constraint("uq_canonical_teams_bzz_team_id", "canonical_teams")
    op.drop_column("canonical_teams", "sofascore_team_id")
    op.drop_column("canonical_teams", "bzz_team_id")
