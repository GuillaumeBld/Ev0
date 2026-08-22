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
    transfermarkt_club_id = Column(Integer, nullable=True, unique=True)
    aliases = Column(ARRAY(Text), nullable=False, server_default="{}")

    # Engagement du club pour la saison courante. Nullables : un club relegue
    # garde sa ligne et son historique, il perd seulement son engagement.
    # Le championnat devient une donnee — il etait auparavant deduit en
    # regroupant les joueurs par nom de club, colonne fausse pour 37 % d'entre
    # eux, d'ou des clubs andorrans dans le filtre Ligue des champions.
    league_api_id = Column(Integer, nullable=True)
    season = Column(String(10), nullable=True)
