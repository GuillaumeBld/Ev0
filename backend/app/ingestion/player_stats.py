"""Player stats ingestion from FBref.

Fetches shooting (xG, goals) and passing (xA, key passes) stats for players.
"""

import math
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

# FBref team URLs (partial - need to map team names to IDs)
FBREF_BASE_URL = "https://fbref.com"

# Rate limiting
_last_request_time: float = 0.0
RATE_LIMIT_SECONDS = 3.5


def normalize_player_name(name: str) -> str:
    """
    Normalize player name for consistent matching.
    
    - Remove accents
    - Lowercase
    - Replace spaces with hyphens
    """
    # Remove accents
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    
    # Lowercase and kebab-case
    normalized = normalized.lower().strip()
    normalized = re.sub(r"\s+", "-", normalized)
    
    return normalized


def calculate_per_90(stat: float, minutes: int) -> float:
    """
    Calculate per-90-minute stat.
    
    Args:
        stat: Raw stat value (goals, xG, etc.)
        minutes: Total minutes played
    
    Returns:
        Stat per 90 minutes, rounded to 3 decimals
    """
    if minutes <= 0:
        return 0.0
    
    return round((stat / minutes) * 90, 3)


def calculate_form_factor(
    recent_values: list[float],
    decay_lambda: float = 0.025,
    baseline: float | None = None,
) -> float:
    """
    Calculate form factor using exponential decay weighting.
    
    More recent matches have higher weight.
    
    Args:
        recent_values: List of values, most recent first
        decay_lambda: Decay rate (higher = faster decay)
        baseline: Expected average value (if None, uses mean of values)
    
    Returns:
        Form factor (1.0 = average, >1 = good form, <1 = bad form)
    """
    if not recent_values:
        return 1.0
    
    if baseline is None:
        baseline = sum(recent_values) / len(recent_values)
    
    if baseline <= 0:
        return 1.0
    
    # Calculate weighted average with exponential decay
    total_weight = 0.0
    weighted_sum = 0.0
    
    for i, value in enumerate(recent_values):
        weight = math.exp(-decay_lambda * i)
        weighted_sum += value * weight
        total_weight += weight
    
    if total_weight <= 0:
        return 1.0
    
    weighted_avg = weighted_sum / total_weight
    return weighted_avg / baseline


def _rate_limit() -> None:
    """Enforce rate limiting for FBref requests."""
    global _last_request_time
    
    now = time.time()
    elapsed = now - _last_request_time
    
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    
    _last_request_time = time.time()


class FBrefPlayerStatsParser:
    """Parser for FBref player stats tables."""
    
    def parse_shooting(self, html: str) -> list[dict[str, Any]]:
        """
        Parse FBref shooting stats table.
        
        Extracts: player, minutes, goals, shots, shots_on_target, xg, npxg
        """
        soup = BeautifulSoup(html, "html.parser")
        stats = []
        
        table = soup.find("table", id=re.compile(r"stats.*shooting", re.IGNORECASE))
        if not table:
            table = soup.find("table", id="stats_shooting")
        if not table:
            return stats
        
        tbody = table.find("tbody")
        if not tbody:
            return stats
        
        for row in tbody.find_all("tr"):
            player_stat = self._parse_shooting_row(row)
            if player_stat:
                stats.append(player_stat)
        
        return stats
    
    def _parse_shooting_row(self, row) -> dict[str, Any] | None:
        """Parse a single shooting stats row."""
        try:
            player_cell = row.find("td", {"data-stat": "player"})
            if not player_cell:
                return None
            
            player_name = player_cell.get_text(strip=True)
            player_link = player_cell.find("a")
            player_id = None
            if player_link and "href" in player_link.attrs:
                match = re.search(r"/players/([^/]+)/", player_link["href"])
                if match:
                    player_id = match.group(1)
            
            # Extract stats
            minutes = self._get_int(row, "minutes")
            goals = self._get_int(row, "goals")
            shots = self._get_int(row, "shots")
            shots_on_target = self._get_int(row, "shots_on_target")
            xg = self._get_float(row, "xg")
            npxg = self._get_float(row, "npxg")
            
            # Calculate per-90
            xg_per_90 = calculate_per_90(xg, minutes)
            npxg_per_90 = calculate_per_90(npxg, minutes)
            
            return {
                "player_name": player_name,
                "player_id": player_id,
                "minutes": minutes,
                "goals": goals,
                "shots": shots,
                "shots_on_target": shots_on_target,
                "xg": xg,
                "npxg": npxg,
                "xg_per_90": xg_per_90,
                "npxg_per_90": npxg_per_90,
            }
        except Exception:
            return None
    
    def parse_passing(self, html: str) -> list[dict[str, Any]]:
        """
        Parse FBref passing stats table.
        
        Extracts: player, assists, xa, key_passes, passes_into_penalty_area, etc.
        """
        soup = BeautifulSoup(html, "html.parser")
        stats = []
        
        table = soup.find("table", id=re.compile(r"stats.*passing", re.IGNORECASE))
        if not table:
            table = soup.find("table", id="stats_passing")
        if not table:
            return stats
        
        tbody = table.find("tbody")
        if not tbody:
            return stats
        
        for row in tbody.find_all("tr"):
            player_stat = self._parse_passing_row(row)
            if player_stat:
                stats.append(player_stat)
        
        return stats
    
    def _parse_passing_row(self, row) -> dict[str, Any] | None:
        """Parse a single passing stats row."""
        try:
            player_cell = row.find("td", {"data-stat": "player"})
            if not player_cell:
                return None
            
            player_name = player_cell.get_text(strip=True)
            
            # Extract stats
            minutes = self._get_int(row, "minutes")
            assists = self._get_int(row, "assists")
            xa = self._get_float(row, "xa")
            key_passes = self._get_int(row, "key_passes")
            passes_into_penalty_area = self._get_int(row, "passes_into_penalty_area")
            progressive_passes = self._get_int(row, "progressive_passes")
            crosses = self._get_int(row, "crosses")
            
            # Calculate per-90
            xa_per_90 = calculate_per_90(xa, minutes)
            
            return {
                "player_name": player_name,
                "minutes": minutes,
                "assists": assists,
                "xa": xa,
                "xa_per_90": xa_per_90,
                "key_passes": key_passes,
                "passes_into_penalty_area": passes_into_penalty_area,
                "progressive_passes": progressive_passes,
                "crosses": crosses,
            }
        except Exception:
            return None
    
    def parse_gca(self, html: str) -> list[dict[str, Any]]:
        """
        Parse FBref goal/shot-creating actions table.
        
        Extracts: SCA (shot-creating actions), GCA (goal-creating actions)
        """
        soup = BeautifulSoup(html, "html.parser")
        stats = []
        
        table = soup.find("table", id=re.compile(r"stats.*gca", re.IGNORECASE))
        if not table:
            return stats
        
        tbody = table.find("tbody")
        if not tbody:
            return stats
        
        for row in tbody.find_all("tr"):
            player_stat = self._parse_gca_row(row)
            if player_stat:
                stats.append(player_stat)
        
        return stats
    
    def _parse_gca_row(self, row) -> dict[str, Any] | None:
        """Parse a single GCA stats row."""
        try:
            player_cell = row.find("td", {"data-stat": "player"})
            if not player_cell:
                return None
            
            player_name = player_cell.get_text(strip=True)
            minutes = self._get_int(row, "minutes")
            sca = self._get_int(row, "sca")
            gca = self._get_int(row, "gca")
            
            return {
                "player_name": player_name,
                "minutes": minutes,
                "sca": sca,
                "gca": gca,
                "sca_per_90": calculate_per_90(sca, minutes),
            }
        except Exception:
            return None
    
    def _get_int(self, row, stat_name: str) -> int:
        """Extract integer from cell."""
        cell = row.find("td", {"data-stat": stat_name})
        if not cell:
            return 0
        text = cell.get_text(strip=True)
        try:
            return int(text.replace(",", ""))
        except ValueError:
            return 0
    
    def _get_float(self, row, stat_name: str) -> float:
        """Extract float from cell."""
        cell = row.find("td", {"data-stat": stat_name})
        if not cell:
            return 0.0
        text = cell.get_text(strip=True)
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return 0.0


def fetch_player_stats(team_slug: str, season: str) -> list[dict[str, Any]]:
    """
    Fetch player stats for a team from FBref.
    
    Combines shooting and passing stats for each player.
    
    Args:
        team_slug: Team identifier (e.g., "paris-saint-germain")
        season: Season string (e.g., "2024-2025")
    
    Returns:
        List of player stats dicts with combined shooting + passing
    """
    # TODO: Map team slugs to FBref team IDs
    # For now, return empty - this needs team ID mapping
    
    _rate_limit()
    
    # Placeholder - actual implementation needs team ID lookup
    # URL format: https://fbref.com/en/squads/{team_id}/{season}/all_comps/
    
    return []


def merge_player_stats(
    shooting: list[dict],
    passing: list[dict],
    gca: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """
    Merge shooting, passing, and GCA stats for players.
    
    Matches by normalized player name.
    """
    merged = {}
    
    # Start with shooting stats
    for stat in shooting:
        name = normalize_player_name(stat["player_name"])
        merged[name] = stat.copy()
    
    # Add passing stats
    for stat in passing:
        name = normalize_player_name(stat["player_name"])
        if name in merged:
            # Merge, keeping shooting stats as base
            for key, value in stat.items():
                if key not in merged[name] or merged[name][key] is None:
                    merged[name][key] = value
        else:
            merged[name] = stat.copy()
    
    # Add GCA stats
    if gca:
        for stat in gca:
            name = normalize_player_name(stat["player_name"])
            if name in merged:
                merged[name]["sca"] = stat.get("sca", 0)
                merged[name]["gca"] = stat.get("gca", 0)
                merged[name]["sca_per_90"] = stat.get("sca_per_90", 0.0)
    
    return list(merged.values())
