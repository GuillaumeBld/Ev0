"""Canonical team model — single source of truth for club names."""

from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY

from app.models.base import Base


class CanonicalTeam(Base):
    __tablename__ = "canonical_teams"

    id = Column(Integer, primary_key=True)
    name_fr = Column(String(200), nullable=False, unique=True)
    # All known raw spellings from various sources (Understat, FotMob, API-Football…)
    aliases = Column(ARRAY(Text), nullable=False, server_default="{}")
