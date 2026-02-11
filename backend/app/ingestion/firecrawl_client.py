"""Firecrawl web scraping client.

Handles JavaScript rendering and anti-bot bypass for FBref/Understat.
"""

import asyncio
from typing import Any

import httpx

from app.config import settings


class FirecrawlClient:
    """Client for Firecrawl API."""
    
    BASE_URL = "https://api.firecrawl.dev/v1"
    
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.firecrawl_api_key
        if not self.api_key:
            raise ValueError("FIRECRAWL_API_KEY not configured")
    
    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
    
    async def scrape(
        self,
        url: str,
        formats: list[str] | None = None,
        wait_for: int = 3000,
        timeout: int = 30000,
    ) -> dict[str, Any]:
        """Scrape a single URL.
        
        Args:
            url: URL to scrape
            formats: Output formats (markdown, html, rawHtml, links, screenshot)
            wait_for: Wait time in ms for JS rendering
            timeout: Request timeout in ms
            
        Returns:
            Scraped content with requested formats
        """
        if formats is None:
            formats = ["html", "markdown"]
        
        payload = {
            "url": url,
            "formats": formats,
            "waitFor": wait_for,
            "timeout": timeout,
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}/scrape",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    
    async def scrape_fbref_players(self, league: str) -> str:
        """Scrape FBref player stats page.
        
        Args:
            league: ligue_1 or premier_league
            
        Returns:
            Raw HTML content
        """
        urls = {
            "ligue_1": "https://fbref.com/en/comps/13/stats/Ligue-1-Stats",
            "premier_league": "https://fbref.com/en/comps/9/stats/Premier-League-Stats",
        }
        
        url = urls.get(league)
        if not url:
            raise ValueError(f"Unknown league: {league}")
        
        result = await self.scrape(url, formats=["rawHtml"], wait_for=5000)
        
        if result.get("success") and result.get("data"):
            return result["data"].get("rawHtml", "")
        
        raise RuntimeError(f"Firecrawl scrape failed: {result}")
    
    async def scrape_understat_league(self, league: str) -> str:
        """Scrape Understat league page.
        
        Args:
            league: ligue_1 or premier_league
            
        Returns:
            Raw HTML content
        """
        slugs = {
            "ligue_1": "Ligue_1",
            "premier_league": "EPL",
        }
        
        slug = slugs.get(league)
        if not slug:
            raise ValueError(f"Unknown league: {league}")
        
        url = f"https://understat.com/league/{slug}/2025"
        result = await self.scrape(url, formats=["rawHtml"], wait_for=3000)
        
        if result.get("success") and result.get("data"):
            return result["data"].get("rawHtml", "")
        
        raise RuntimeError(f"Firecrawl scrape failed: {result}")


async def get_firecrawl_client() -> FirecrawlClient | None:
    """Get Firecrawl client if API key configured."""
    if not settings.firecrawl_api_key:
        return None
    return FirecrawlClient()
