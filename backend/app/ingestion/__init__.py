"""Data ingestion modules."""

from app.ingestion.api_football import APIFootballClient, get_api_football_client

__all__ = [
    "APIFootballClient",
    "get_api_football_client",
]
