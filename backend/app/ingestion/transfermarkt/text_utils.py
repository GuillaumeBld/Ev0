"""Utilitaires texte partages par les modules d'ingestion Transfermarkt.

`fold_accents` est le miroir Python du repli accent-insensible utilise cote
front (voir `frontend/src/lib/teamLogos.ts::getTeamId`, qui applique
`name.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()`) : NFD
decompose chaque caractere accentue en (lettre de base + marque(s) combinante(s)
separee(s)), puis on retire ces marques combinantes pour ne garder que la
lettre de base, avant de passer en minuscule. Volontairement reutilisable par
d'autres taches de sync Transfermarkt (matching clubs, joueurs, etc.) : ne pas
dupliquer cette logique ailleurs.
"""
from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")


def fold_accents(s: str) -> str:
    """Normalise `s` pour un matching accent/casse-insensible.

    Etapes : normalisation NFD (decomposition canonique), suppression des
    marques diacritiques combinantes (categorie Unicode 'Mn'), passage en
    minuscule, puis compression des espaces multiples en un seul (et trim).

    Exemples : "Dembélé" -> "dembele" ; "Paris Saint-Germain" ->
    "paris saint-germain" ; "  RC   Lens " -> "rc lens".
    """
    decomposed = unicodedata.normalize("NFD", s)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.lower()
    return _WHITESPACE_RE.sub(" ", lowered).strip()
