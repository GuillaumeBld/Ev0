import asyncio
import sys
import os

# Add parent directory to path to allow importing app modules if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ingestion.understat_scraper import fetch_understat_league

async def test_direct_scraper():
    print("Testing Direct Understat Scraper (No Firecrawl)...")
    try:
        players, teams = await fetch_understat_league("ligue_1")
        
        print(f"\nResults for Ligue 1:")
        print(f"  Players found: {len(players)}")
        print(f"  Teams found:   {len(teams)}")
        
        if players:
            print(f"  Sample Player: {players[0]['name']} (xG: {players[0]['xg']})")
        
        if len(players) > 100:
            print("\nSUCCESS: Direct scraping works and retrieved ample data.")
        else:
            print("\nWARNING: Retrieved very few players. Check if season has started or structure changed.")
            
    except Exception as e:
        print(f"\nERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_direct_scraper())
