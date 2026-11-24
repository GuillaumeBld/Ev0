"""Où trouver chaque compétition de l'Atlas chez Betclic, Unibet et PMU.

Une seule table, relevée sur les sites des trois books le 24/11/2026. La clé
est l'identifiant Atlas de la compétition (`fixtures.competition_api_id`).
Les six championnats historiques et les compétitions de sélections gardent
leurs tables d'origine dans chaque scraper ; tout le reste est ici.

Ajouter une compétition = une ligne ici. La clé utilisée par les scrapers et
le planificateur est l'étiquette du match (`league_key_for` : « comp_<id> »).

Comment relever les identifiants :
- Betclic : `competitionId` dans la page https://www.betclic.fr/football-sfootball
  (aussi dans l'URL de la compétition : « ...-c<id> ») ;
- Unibet : prendre l'identifiant d'un match dans l'URL du site, lire
  `/lvs-api/ff/e<id>` depuis le serveur : le champ `parent` est « p<nœud> » ;
- PMU : chemin `englishName` du flux Kambi, sans « Football » :
  (pays, championnat) ou (compétition,) quand il n'y a pas de pays.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.competitions import league_key_for


@dataclass(frozen=True)
class BookCompetition:
    betclic: int | None          # competitionId Betclic
    unibet: int | None           # nœud LVS Unibet
    pmu: tuple[str, ...] | None  # chemin Kambi
    # Clé partagée avec une autre compétition (Liga MX : Apertura et Clausura
    # sont deux compétitions de l'Atlas, une seule chez les books).
    same_as: int | None = None


BOOK_COMPETITIONS: dict[int, BookCompetition] = {
    35: BookCompetition(betclic=201, unibet=58542719, pmu=None),  # Copa do Brasil (Brazil)
    39: BookCompetition(betclic=44, unibet=None, pmu=None),  # FA Cup (England)
    40: BookCompetition(betclic=41, unibet=58558744, pmu=None),  # Carabao Cup (England)
    44: BookCompetition(betclic=36, unibet=None, pmu=None),  # Coupe de France (France)
    43: BookCompetition(betclic=55, unibet=58529747, pmu=None),  # DFB-Pokal (Germany)
    42: BookCompetition(betclic=50, unibet=58529890, pmu=None),  # Coppa Italia (Italy)
    96: BookCompetition(betclic=35, unibet=58528532, pmu=('Austria', 'Bundesliga')),  # Bundesliga (Austria)
    14: BookCompetition(betclic=26, unibet=58529988, pmu=('Belgium', 'Jupiler Pro League')),  # Pro League (Belgium)
    22: BookCompetition(betclic=548, unibet=58528218, pmu=('Bulgaria', 'PFL 1')),  # Parva Liga (Bulgaria)
    99: BookCompetition(betclic=220, unibet=58530457, pmu=('Czech Republic', 'First League')),  # Czech First League (Czechia)
    84: BookCompetition(betclic=88, unibet=58528305, pmu=('Denmark', 'Superligaen')),  # Superliga (Denmark)
    24: BookCompetition(betclic=38, unibet=58528252, pmu=('Greece', 'Super League')),  # Super League Greece (Greece)
    10: BookCompetition(betclic=21, unibet=58529946, pmu=('Netherlands', 'Eredivisie')),  # Eredivisie (Netherlands)
    54: BookCompetition(betclic=156, unibet=58529164, pmu=('Norway', 'Eliteserien')),  # Eliteserien (Norway)
    25: BookCompetition(betclic=221, unibet=58528798, pmu=('Poland', 'Ekstraklasa')),  # Ekstraklasa (Poland)
    2: BookCompetition(betclic=32, unibet=58530875, pmu=('Portugal', 'Primeira Liga')),  # Liga Portugal (Portugal)
    23: BookCompetition(betclic=552, unibet=58528328, pmu=('Romania', 'Liga I')),  # Superliga (Romania)
    13: BookCompetition(betclic=33, unibet=58530238, pmu=('Scotland', 'Scottish Premiership')),  # Scottish Premiership (Scotland)
    109: BookCompetition(betclic=559, unibet=58530073, pmu=('Slovakia', 'Super Liga')),  # Niké liga (Slovakia)
    26: BookCompetition(betclic=145, unibet=58528282, pmu=('Sweden', 'Allsvenskan')),  # Allsvenskan (Sweden)
    15: BookCompetition(betclic=27, unibet=58530105, pmu=('Switzerland', 'Super League')),  # Super League (Switzerland)
    11: BookCompetition(betclic=37, unibet=58528495, pmu=('Turkey', 'Süper Lig')),  # Süper Lig (Turkey)
    12: BookCompetition(betclic=28, unibet=58532448, pmu=('England', 'The Championship')),  # Championship (England)
    89: BookCompetition(betclic=19, unibet=58531497, pmu=None),  # Ligue 2 (France)
    94: BookCompetition(betclic=29, unibet=58529686, pmu=('Germany', '2. Bundesliga')),  # 2. Bundesliga (Germany)
    100: BookCompetition(betclic=30, unibet=58529802, pmu=('Italy', 'Serie B')),  # Serie B (Italy)
    88: BookCompetition(betclic=939, unibet=58530879, pmu=('Portugal', 'Liga 2')),  # Liga Portugal 2 (Portugal)
    38: BookCompetition(betclic=31, unibet=58532115, pmu=('Spain', 'La Liga 2')),  # Segunda División (Spain)
    8: BookCompetition(betclic=3453, unibet=58532492, pmu=('Europa League',)),  # Europa League
    83: BookCompetition(betclic=28946, unibet=58554102, pmu=('Conference League',)),  # Conference League
    32: BookCompetition(betclic=197, unibet=58528551, pmu=('Copa Libertadores',)),  # Copa Libertadores
    85: BookCompetition(betclic=230, unibet=58550489, pmu=('Argentina', 'Liga Profesional Argentina')),  # Liga Profesional (Argentina)
    104: BookCompetition(betclic=1874, unibet=58528164, pmu=('Australia', 'A-League')),  # A-League Men (Australia)
    9: BookCompetition(betclic=187, unibet=58540305, pmu=('Brazil', 'Brasileirao Serie A')),  # Brasileirão Série A (Brazil)
    111: BookCompetition(betclic=3584, unibet=58528472, pmu=('Chile', 'Primera Chile')),  # Primera División (Chile)
    80: BookCompetition(betclic=5500, unibet=58529143, pmu=('Colombia', 'Liga Dimayor')),  # Primera A (Colombia)
    112: BookCompetition(betclic=2597, unibet=58530165, pmu=('Ecuador', 'Liga Pro')),  # LigaPro Serie A (Ecuador)
    49: BookCompetition(betclic=503, unibet=58528822, pmu=('Japan', 'J1-League')),  # J1 League (Japan)
    19: BookCompetition(betclic=432, unibet=58555841, pmu=('Mexico', 'Liga MX')),  # Liga MX Apertura (Mexico)
    53: BookCompetition(betclic=None, unibet=None, pmu=('Morocco', 'Botola')),  # Botola Pro (Morocco)
    17: BookCompetition(betclic=4684, unibet=58986505, pmu=('Saudi Arabia', 'Professional League')),  # Saudi Pro League (Saudi Arabia)
    50: BookCompetition(betclic=3304, unibet=58528853, pmu=('South Korea', 'K-League 1')),  # K League 1 (South Korea)
    18: BookCompetition(betclic=504, unibet=58529410, pmu=('USA', 'MLS')),  # Major League Soccer (USA)
    20: BookCompetition(betclic=432, unibet=58555841, pmu=("Mexico", "Liga MX"), same_as=19),  # Liga MX Clausura
}


def book_key(competition_api_id: int) -> str:
    """Clé des scrapers pour une compétition de la table."""
    meta = BOOK_COMPETITIONS[competition_api_id]
    return league_key_for(meta.same_as if meta.same_as is not None else competition_api_id)


# Étiquette du match -> clé des scrapers (identiques, sauf compétition partagée).
SCRAPER_KEY_BY_LEAGUE: dict[str, str] = {
    league_key_for(cid): book_key(cid) for cid in BOOK_COMPETITIONS
}

BETCLIC_IDS: dict[str, int] = {
    book_key(cid): m.betclic for cid, m in BOOK_COMPETITIONS.items() if m.betclic is not None
}
UNIBET_NODES: dict[str, int] = {
    book_key(cid): m.unibet for cid, m in BOOK_COMPETITIONS.items() if m.unibet is not None
}
# Chemin Kambi -> clé, séparé selon qu'il porte un pays ou non.
PMU_DOMESTIC: dict[tuple[str, str], str] = {
    m.pmu: book_key(cid) for cid, m in BOOK_COMPETITIONS.items()
    if m.pmu is not None and len(m.pmu) == 2
}
PMU_NO_COUNTRY: dict[str, str] = {
    m.pmu[0]: book_key(cid) for cid, m in BOOK_COMPETITIONS.items()
    if m.pmu is not None and len(m.pmu) == 1
}
