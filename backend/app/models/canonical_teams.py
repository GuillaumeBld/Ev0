"""Canonical team model — single source of truth for club names."""

from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY

from app.models.base import Base


class CanonicalTeam(Base):
    __tablename__ = "canonical_teams"

    id = Column(Integer, primary_key=True)
    name_fr = Column(String(200), nullable=False, unique=True)
    name_en = Column(String(200), nullable=True)
    api_football_id = Column(Integer, nullable=True, unique=True)
    bzz_team_id = Column(Integer, nullable=True, unique=True)
    sofascore_team_id = Column(Integer, nullable=True, unique=True)
    aliases = Column(ARRAY(Text), nullable=False, server_default="{}")
