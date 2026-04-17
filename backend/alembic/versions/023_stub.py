"""Stub migration — revision 023 was applied directly to the VPS DB.
Schema changes are already present; this file exists to satisfy Alembic's
revision chain.

Revision ID: 023
Revises: 022
Create Date: 2026-04-17
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
