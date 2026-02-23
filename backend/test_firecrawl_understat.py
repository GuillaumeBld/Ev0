import asyncio
import os
import sys
from typing import Any, Optional

# Minimal standalone implementation of FirecrawlClient for testing
# This avoids importing the whole app which requires database dependencies

import httpx
from dotenv import load_dotenv

load_dotenv()

class FirecrawlClient:
    """Client for Firecrawl API."""
    
    BASE_URL = "https://api.firecrawl.dev/v1"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FIRECRAWL_API_KEY")
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
        formats: Optional[list[str]] = None,
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
            print(f"Sending request to Firecrawl (wait_for={wait_for}ms)...")
            resp = await client.post(
                f"{self.BASE_URL}/scrape",
                headers=self.headers,
                json=payload,
            )
            if resp.status_code != 200:
                print(f"Error: {resp.status_code} - {resp.text}")
            resp.raise_for_status()
            return resp.json()
    
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
        # Increase wait time to ensure JS renders fully (Understat can be slow)
        # THIS IS THE FIX WE ARE TESTING: wait_for=10000
        result = await self.scrape(url, formats=["rawHtml"], wait_for=10000)
        
        if result.get("success") and result.get("data"):
            return result["data"].get("rawHtml", "")
        
        raise RuntimeError(f"Firecrawl scrape failed: {result}")

async def test_understat_scrape():
    try:
        client = FirecrawlClient()
    except ValueError as e:
        print(e)
        return

    league = "ligue_1"
    
    print(f"Testing Understat scrape for {league} with 10s wait...")
    try:
        html = await client.scrape_understat_league(league)
        if html:
            print(f"Success! Retrieved {len(html)} bytes of HTML.")
            # Check for key data indicators
            # Understat embeds data in 'JSON.parse' calls inside scripts
            if "JSON.parse" in html and "playersData" in html:
                print("Confirmed: 'playersData' JSON found in HTML (Stats loaded).")
            else:
                print("Warning: 'playersData' NOT clearly found. JS might not have finished or structure changed.")
                # Print a small snippet to see what we got
                print("Snippet:", html[:500])
        else:
            print("Failed: No HTML returned.")
    except Exception as e:
        print(f"Error during scrape: {e}")

if __name__ == "__main__":
    asyncio.run(test_understat_scrape())