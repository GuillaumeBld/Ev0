"""Data ingestion modules.

Main strategy: Firecrawl + LLM + API-Football
Fallback strategy: Firecrawl + LLM only
"""

from app.ingestion.smart_sync import smart_sync_all, smart_sync_league
from app.ingestion.firecrawl_client import FirecrawlClient, get_firecrawl_client
from app.ingestion.llm_parser import LLMParser, get_llm_parser
from app.ingestion.api_football import APIFootballClient, get_api_football_client

__all__ = [
    "smart_sync_all",
    "smart_sync_league",
    "FirecrawlClient",
    "get_firecrawl_client",
    "LLMParser",
    "get_llm_parser",
    "APIFootballClient",
    "get_api_football_client",
]
