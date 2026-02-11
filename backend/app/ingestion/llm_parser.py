"""LLM-based HTML parser for player stats extraction.

Uses OpenRouter to parse scraped HTML into structured player data.
"""

import json
import re
from typing import Any

import httpx

from app.config import settings


SYSTEM_PROMPT = """Tu es un extracteur de données football. Tu extrais les statistiques de joueurs depuis du HTML brut.

RÈGLES:
- Extrais TOUS les joueurs du HTML
- Retourne UNIQUEMENT du JSON valide, rien d'autre
- Utilise les champs exacts demandés
- Si une stat est manquante, utilise 0 ou null
- Les xG/xA doivent être des floats (ex: 0.45)
- Les minutes/buts/passes doivent être des int"""


FBREF_EXTRACTION_PROMPT = """Extrais les statistiques de joueurs de ce HTML FBref.

Pour chaque joueur, extrais:
- name: nom complet
- team: équipe
- position: poste (FW, MF, DF, GK)
- minutes: minutes jouées (int)
- goals: buts marqués (int)
- assists: passes décisives (int)
- xg: expected goals (float)
- npxg: non-penalty xG (float)  
- xa: expected assists (float)
- shots: tirs (int)

Retourne un JSON avec cette structure exacte:
{
  "players": [
    {"name": "...", "team": "...", "position": "...", "minutes": 0, "goals": 0, "assists": 0, "xg": 0.0, "npxg": 0.0, "xa": 0.0, "shots": 0},
    ...
  ]
}

HTML:
"""


UNDERSTAT_EXTRACTION_PROMPT = """Extrais les statistiques de joueurs de ce HTML Understat.

Cherche le JavaScript contenant `var playersData = JSON.parse('...')`.
Décode les séquences \\xNN en caractères.

Pour chaque joueur, extrais:
- understat_id: l'id du joueur
- name: player_name
- team: team_title
- position: position
- games: matches joués (int)
- minutes: time (int)
- goals: goals (int)
- assists: assists (int)
- xg: xG (float)
- npxg: npxG (float)
- xa: xA (float)
- shots: shots (int)
- key_passes: key_passes (int)

Retourne un JSON avec cette structure exacte:
{
  "players": [
    {"understat_id": "123", "name": "...", "team": "...", "position": "...", "games": 0, "minutes": 0, "goals": 0, "assists": 0, "xg": 0.0, "npxg": 0.0, "xa": 0.0, "shots": 0, "key_passes": 0},
    ...
  ]
}

HTML:
"""


class LLMParser:
    """LLM-based HTML parser using OpenRouter."""
    
    BASE_URL = "https://openrouter.ai/api/v1"
    
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.openrouter_api_key
        self.model = model or settings.llm_parser_model
        
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")
    
    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ev0.213-130-144-204.traefik.me",
            "X-Title": "Ev0 Stats Parser",
        }
    
    async def _call_llm(self, prompt: str, max_tokens: int = 16000) -> str:
        """Make LLM API call."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}/chat/completions",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    
    def _extract_json(self, text: str) -> dict[str, Any]:
        """Extract JSON from LLM response."""
        # Try to find JSON block
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_match:
            text = json_match.group(1)
        
        # Try to find raw JSON object
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            text = json_match.group(0)
        
        return json.loads(text)
    
    async def parse_fbref_html(self, html: str, league: str) -> list[dict[str, Any]]:
        """Parse FBref HTML into player stats.
        
        Args:
            html: Raw HTML from FBref
            league: League identifier
            
        Returns:
            List of player dicts with stats
        """
        # Truncate HTML to fit in context (keep main stats table)
        # Look for the stats table section
        table_start = html.find('id="stats_standard"')
        if table_start == -1:
            table_start = html.find('id="stats_shooting"')
        if table_start == -1:
            table_start = 0
        
        # Get a window around the table
        start = max(0, table_start - 1000)
        end = min(len(html), table_start + 100000)
        truncated_html = html[start:end]
        
        prompt = FBREF_EXTRACTION_PROMPT + truncated_html[:80000]
        
        try:
            response = await self._call_llm(prompt)
            data = self._extract_json(response)
            players = data.get("players", [])
            
            # Add per-90 calculations
            for p in players:
                minutes = p.get("minutes", 0) or 0
                xg = p.get("xg", 0) or 0
                xa = p.get("xa", 0) or 0
                npxg = p.get("npxg", 0) or 0
                
                if minutes > 0:
                    p["xg_per_90"] = round((xg / minutes) * 90, 3)
                    p["xa_per_90"] = round((xa / minutes) * 90, 3)
                    p["npxg_per_90"] = round((npxg / minutes) * 90, 3)
                else:
                    p["xg_per_90"] = 0.0
                    p["xa_per_90"] = 0.0
                    p["npxg_per_90"] = 0.0
            
            return players
            
        except Exception as e:
            print(f"LLM parsing error for FBref {league}: {e}")
            return []
    
    async def parse_understat_html(self, html: str, league: str) -> list[dict[str, Any]]:
        """Parse Understat HTML into player stats.
        
        Args:
            html: Raw HTML from Understat
            league: League identifier
            
        Returns:
            List of player dicts with stats
        """
        # Try direct regex extraction first (faster, cheaper)
        players = self._try_direct_understat_extraction(html)
        if players:
            return players
        
        # Fallback to LLM parsing
        # Find the script section with playersData
        script_match = re.search(r'<script[^>]*>[\s\S]*?playersData[\s\S]*?</script>', html)
        if script_match:
            script_content = script_match.group(0)
        else:
            script_content = html[:100000]
        
        prompt = UNDERSTAT_EXTRACTION_PROMPT + script_content[:80000]
        
        try:
            response = await self._call_llm(prompt)
            data = self._extract_json(response)
            players = data.get("players", [])
            
            # Add per-90 calculations
            for p in players:
                minutes = p.get("minutes", 0) or 0
                xg = p.get("xg", 0) or 0
                xa = p.get("xa", 0) or 0
                npxg = p.get("npxg", 0) or 0
                
                if minutes > 0:
                    p["xg_per_90"] = round((xg / minutes) * 90, 3)
                    p["xa_per_90"] = round((xa / minutes) * 90, 3)
                    p["npxg_per_90"] = round((npxg / minutes) * 90, 3)
                else:
                    p["xg_per_90"] = 0.0
                    p["xa_per_90"] = 0.0
                    p["npxg_per_90"] = 0.0
            
            return players
            
        except Exception as e:
            print(f"LLM parsing error for Understat {league}: {e}")
            return []
    
    def _try_direct_understat_extraction(self, html: str) -> list[dict[str, Any]]:
        """Try to extract Understat data without LLM."""
        # Multiple regex patterns for different HTML structures
        patterns = [
            r"var\s+playersData\s*=\s*JSON\.parse\('(.+?)'\)",
            r'playersData\s*=\s*JSON\.parse\("(.+?)"\)',
            r"playersData\s*=\s*(\[.+?\]);",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    raw = match.group(1)
                    # Decode hex escapes
                    decoded = re.sub(
                        r'\\x([0-9a-fA-F]{2})',
                        lambda m: chr(int(m.group(1), 16)),
                        raw
                    )
                    # Handle unicode escapes
                    decoded = decoded.encode().decode('unicode_escape')
                    
                    data = json.loads(decoded)
                    
                    players = []
                    for p in data:
                        minutes = int(p.get("time", 0) or 0)
                        xg = float(p.get("xG", 0) or 0)
                        xa = float(p.get("xA", 0) or 0)
                        npxg = float(p.get("npxG", 0) or 0)
                        
                        players.append({
                            "understat_id": str(p.get("id", "")),
                            "name": p.get("player_name", ""),
                            "team": p.get("team_title", ""),
                            "position": p.get("position", ""),
                            "games": int(p.get("games", 0) or 0),
                            "minutes": minutes,
                            "goals": int(p.get("goals", 0) or 0),
                            "assists": int(p.get("assists", 0) or 0),
                            "xg": xg,
                            "npxg": npxg,
                            "xa": xa,
                            "shots": int(p.get("shots", 0) or 0),
                            "key_passes": int(p.get("key_passes", 0) or 0),
                            "xg_per_90": round((xg / minutes) * 90, 3) if minutes > 0 else 0.0,
                            "xa_per_90": round((xa / minutes) * 90, 3) if minutes > 0 else 0.0,
                            "npxg_per_90": round((npxg / minutes) * 90, 3) if minutes > 0 else 0.0,
                        })
                    
                    if players:
                        print(f"  Direct extraction successful: {len(players)} players")
                        return players
                        
                except Exception as e:
                    print(f"  Direct extraction attempt failed: {e}")
                    continue
        
        return []


async def get_llm_parser() -> LLMParser | None:
    """Get LLM parser if API key configured."""
    if not settings.openrouter_api_key:
        return None
    return LLMParser()
