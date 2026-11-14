"""Référentiel des pays : nom (toutes graphies rencontrées) -> (ISO2, continent).

Sert à ranger dans l'Atlas toute équipe, tout championnat et toute sélection,
quelle que soit sa source (Bzzoiro, Transfermarkt, portail CSV) : chaque
source écrit les pays à sa façon (« Türkiye » / « Turkey », « USA » /
« United States », « Côte d'Ivoire » / « Ivory Coast »...).

Continents : EUR, AFR, ASI, NAM (Amérique du Nord, centrale, Caraïbes), SAM, OCE.
Angleterre, Écosse, Pays de Galles et Irlande du Nord sont regroupés sous GB
(« Royaume-Uni »), comme la carte de l'Atlas les dessine.
"""
from __future__ import annotations

import unicodedata

# ISO2 -> (nom anglais, nom français, continent)
COUNTRIES: dict[str, tuple[str, str, str]] = {
    # Europe
    "AL": ("Albania", "Albanie", "EUR"), "AD": ("Andorra", "Andorre", "EUR"),
    "AM": ("Armenia", "Arménie", "EUR"), "AT": ("Austria", "Autriche", "EUR"),
    "AZ": ("Azerbaijan", "Azerbaïdjan", "EUR"), "BY": ("Belarus", "Biélorussie", "EUR"),
    "BE": ("Belgium", "Belgique", "EUR"), "BA": ("Bosnia and Herzegovina", "Bosnie-Herzégovine", "EUR"),
    "BG": ("Bulgaria", "Bulgarie", "EUR"), "HR": ("Croatia", "Croatie", "EUR"),
    "CY": ("Cyprus", "Chypre", "EUR"), "CZ": ("Czechia", "Tchéquie", "EUR"),
    "DK": ("Denmark", "Danemark", "EUR"), "EE": ("Estonia", "Estonie", "EUR"),
    "FO": ("Faroe Islands", "Îles Féroé", "EUR"), "FI": ("Finland", "Finlande", "EUR"),
    "FR": ("France", "France", "EUR"), "GE": ("Georgia", "Géorgie", "EUR"),
    "DE": ("Germany", "Allemagne", "EUR"), "GI": ("Gibraltar", "Gibraltar", "EUR"),
    "GB": ("United Kingdom", "Royaume-Uni", "EUR"), "GR": ("Greece", "Grèce", "EUR"),
    "HU": ("Hungary", "Hongrie", "EUR"), "IS": ("Iceland", "Islande", "EUR"),
    "IE": ("Ireland", "Irlande", "EUR"), "IL": ("Israel", "Israël", "EUR"),
    "IT": ("Italy", "Italie", "EUR"), "KZ": ("Kazakhstan", "Kazakhstan", "EUR"),
    "XK": ("Kosovo", "Kosovo", "EUR"), "LV": ("Latvia", "Lettonie", "EUR"),
    "LI": ("Liechtenstein", "Liechtenstein", "EUR"), "LT": ("Lithuania", "Lituanie", "EUR"),
    "LU": ("Luxembourg", "Luxembourg", "EUR"), "MT": ("Malta", "Malte", "EUR"),
    "MD": ("Moldova", "Moldavie", "EUR"), "MC": ("Monaco", "Monaco", "EUR"),
    "ME": ("Montenegro", "Monténégro", "EUR"), "NL": ("Netherlands", "Pays-Bas", "EUR"),
    "MK": ("North Macedonia", "Macédoine du Nord", "EUR"), "NO": ("Norway", "Norvège", "EUR"),
    "PL": ("Poland", "Pologne", "EUR"), "PT": ("Portugal", "Portugal", "EUR"),
    "RO": ("Romania", "Roumanie", "EUR"), "RU": ("Russia", "Russie", "EUR"),
    "SM": ("San Marino", "Saint-Marin", "EUR"), "RS": ("Serbia", "Serbie", "EUR"),
    "SK": ("Slovakia", "Slovaquie", "EUR"), "SI": ("Slovenia", "Slovénie", "EUR"),
    "ES": ("Spain", "Espagne", "EUR"), "SE": ("Sweden", "Suède", "EUR"),
    "CH": ("Switzerland", "Suisse", "EUR"), "TR": ("Turkey", "Turquie", "EUR"),
    "UA": ("Ukraine", "Ukraine", "EUR"),
    # Afrique
    "DZ": ("Algeria", "Algérie", "AFR"), "AO": ("Angola", "Angola", "AFR"),
    "BJ": ("Benin", "Bénin", "AFR"), "BW": ("Botswana", "Botswana", "AFR"),
    "BF": ("Burkina Faso", "Burkina Faso", "AFR"), "BI": ("Burundi", "Burundi", "AFR"),
    "CV": ("Cape Verde", "Cap-Vert", "AFR"), "CM": ("Cameroon", "Cameroun", "AFR"),
    "CF": ("Central African Republic", "Centrafrique", "AFR"), "TD": ("Chad", "Tchad", "AFR"),
    "KM": ("Comoros", "Comores", "AFR"), "CG": ("Congo", "Congo", "AFR"),
    "CD": ("DR Congo", "RD Congo", "AFR"), "CI": ("Ivory Coast", "Côte d'Ivoire", "AFR"),
    "DJ": ("Djibouti", "Djibouti", "AFR"), "EG": ("Egypt", "Égypte", "AFR"),
    "GQ": ("Equatorial Guinea", "Guinée équatoriale", "AFR"), "ER": ("Eritrea", "Érythrée", "AFR"),
    "SZ": ("Eswatini", "Eswatini", "AFR"), "ET": ("Ethiopia", "Éthiopie", "AFR"),
    "GA": ("Gabon", "Gabon", "AFR"), "GM": ("Gambia", "Gambie", "AFR"),
    "GH": ("Ghana", "Ghana", "AFR"), "GN": ("Guinea", "Guinée", "AFR"),
    "GW": ("Guinea-Bissau", "Guinée-Bissau", "AFR"), "KE": ("Kenya", "Kenya", "AFR"),
    "LS": ("Lesotho", "Lesotho", "AFR"), "LR": ("Liberia", "Liberia", "AFR"),
    "LY": ("Libya", "Libye", "AFR"), "MG": ("Madagascar", "Madagascar", "AFR"),
    "MW": ("Malawi", "Malawi", "AFR"), "ML": ("Mali", "Mali", "AFR"),
    "MR": ("Mauritania", "Mauritanie", "AFR"), "MU": ("Mauritius", "Maurice", "AFR"),
    "MA": ("Morocco", "Maroc", "AFR"), "MZ": ("Mozambique", "Mozambique", "AFR"),
    "NA": ("Namibia", "Namibie", "AFR"), "NE": ("Niger", "Niger", "AFR"),
    "NG": ("Nigeria", "Nigeria", "AFR"), "RW": ("Rwanda", "Rwanda", "AFR"),
    "ST": ("Sao Tome and Principe", "Sao Tomé-et-Principe", "AFR"), "SN": ("Senegal", "Sénégal", "AFR"),
    "SC": ("Seychelles", "Seychelles", "AFR"), "SL": ("Sierra Leone", "Sierra Leone", "AFR"),
    "SO": ("Somalia", "Somalie", "AFR"), "ZA": ("South Africa", "Afrique du Sud", "AFR"),
    "SS": ("South Sudan", "Soudan du Sud", "AFR"), "SD": ("Sudan", "Soudan", "AFR"),
    "TZ": ("Tanzania", "Tanzanie", "AFR"), "TG": ("Togo", "Togo", "AFR"),
    "TN": ("Tunisia", "Tunisie", "AFR"), "UG": ("Uganda", "Ouganda", "AFR"),
    "ZM": ("Zambia", "Zambie", "AFR"), "ZW": ("Zimbabwe", "Zimbabwe", "AFR"),
    # Asie
    "AF": ("Afghanistan", "Afghanistan", "ASI"), "BH": ("Bahrain", "Bahreïn", "ASI"),
    "BD": ("Bangladesh", "Bangladesh", "ASI"), "BT": ("Bhutan", "Bhoutan", "ASI"),
    "BN": ("Brunei", "Brunei", "ASI"), "KH": ("Cambodia", "Cambodge", "ASI"),
    "CN": ("China", "Chine", "ASI"), "TW": ("Chinese Taipei", "Taïwan", "ASI"),
    "HK": ("Hong Kong", "Hong Kong", "ASI"), "IN": ("India", "Inde", "ASI"),
    "ID": ("Indonesia", "Indonésie", "ASI"), "IR": ("Iran", "Iran", "ASI"),
    "IQ": ("Iraq", "Irak", "ASI"), "JP": ("Japan", "Japon", "ASI"),
    "JO": ("Jordan", "Jordanie", "ASI"), "KW": ("Kuwait", "Koweït", "ASI"),
    "KG": ("Kyrgyzstan", "Kirghizistan", "ASI"), "LA": ("Laos", "Laos", "ASI"),
    "LB": ("Lebanon", "Liban", "ASI"), "MO": ("Macau", "Macao", "ASI"),
    "MY": ("Malaysia", "Malaisie", "ASI"), "MV": ("Maldives", "Maldives", "ASI"),
    "MN": ("Mongolia", "Mongolie", "ASI"), "MM": ("Myanmar", "Birmanie", "ASI"),
    "NP": ("Nepal", "Népal", "ASI"), "KP": ("North Korea", "Corée du Nord", "ASI"),
    "OM": ("Oman", "Oman", "ASI"), "PK": ("Pakistan", "Pakistan", "ASI"),
    "PS": ("Palestine", "Palestine", "ASI"), "PH": ("Philippines", "Philippines", "ASI"),
    "QA": ("Qatar", "Qatar", "ASI"), "SA": ("Saudi Arabia", "Arabie saoudite", "ASI"),
    "SG": ("Singapore", "Singapour", "ASI"), "KR": ("South Korea", "Corée du Sud", "ASI"),
    "LK": ("Sri Lanka", "Sri Lanka", "ASI"), "SY": ("Syria", "Syrie", "ASI"),
    "TJ": ("Tajikistan", "Tadjikistan", "ASI"), "TH": ("Thailand", "Thaïlande", "ASI"),
    "TL": ("Timor-Leste", "Timor oriental", "ASI"), "TM": ("Turkmenistan", "Turkménistan", "ASI"),
    "AE": ("United Arab Emirates", "Émirats arabes unis", "ASI"), "UZ": ("Uzbekistan", "Ouzbékistan", "ASI"),
    "VN": ("Vietnam", "Viêt Nam", "ASI"), "YE": ("Yemen", "Yémen", "ASI"),
    # Amérique du Nord, centrale, Caraïbes
    "AI": ("Anguilla", "Anguilla", "NAM"), "AG": ("Antigua and Barbuda", "Antigua-et-Barbuda", "NAM"),
    "AW": ("Aruba", "Aruba", "NAM"), "BS": ("Bahamas", "Bahamas", "NAM"),
    "BB": ("Barbados", "Barbade", "NAM"), "BZ": ("Belize", "Belize", "NAM"),
    "BM": ("Bermuda", "Bermudes", "NAM"), "BQ": ("Bonaire", "Bonaire", "NAM"),
    "VG": ("British Virgin Islands", "Îles Vierges britanniques", "NAM"), "CA": ("Canada", "Canada", "NAM"),
    "KY": ("Cayman Islands", "Îles Caïmans", "NAM"), "CR": ("Costa Rica", "Costa Rica", "NAM"),
    "CU": ("Cuba", "Cuba", "NAM"), "CW": ("Curaçao", "Curaçao", "NAM"),
    "DM": ("Dominica", "Dominique", "NAM"), "DO": ("Dominican Republic", "République dominicaine", "NAM"),
    "SV": ("El Salvador", "Salvador", "NAM"), "GF": ("French Guiana", "Guyane", "NAM"),
    "GD": ("Grenada", "Grenade", "NAM"), "GP": ("Guadeloupe", "Guadeloupe", "NAM"),
    "GT": ("Guatemala", "Guatemala", "NAM"), "GY": ("Guyana", "Guyana", "NAM"),
    "HT": ("Haiti", "Haïti", "NAM"), "HN": ("Honduras", "Honduras", "NAM"),
    "JM": ("Jamaica", "Jamaïque", "NAM"), "MQ": ("Martinique", "Martinique", "NAM"),
    "MX": ("Mexico", "Mexique", "NAM"), "MS": ("Montserrat", "Montserrat", "NAM"),
    "NI": ("Nicaragua", "Nicaragua", "NAM"), "PA": ("Panama", "Panama", "NAM"),
    "PR": ("Puerto Rico", "Porto Rico", "NAM"), "KN": ("Saint Kitts and Nevis", "Saint-Christophe-et-Niévès", "NAM"),
    "LC": ("Saint Lucia", "Sainte-Lucie", "NAM"), "MF": ("Saint Martin", "Saint-Martin", "NAM"),
    "VC": ("Saint Vincent and the Grenadines", "Saint-Vincent-et-les-Grenadines", "NAM"),
    "SX": ("Sint Maarten", "Sint Maarten", "NAM"), "SR": ("Suriname", "Suriname", "NAM"),
    "TT": ("Trinidad and Tobago", "Trinité-et-Tobago", "NAM"), "TC": ("Turks and Caicos Islands", "Îles Turques-et-Caïques", "NAM"),
    "US": ("United States", "États-Unis", "NAM"), "VI": ("US Virgin Islands", "Îles Vierges des États-Unis", "NAM"),
    # Amérique du Sud
    "AR": ("Argentina", "Argentine", "SAM"), "BO": ("Bolivia", "Bolivie", "SAM"),
    "BR": ("Brazil", "Brésil", "SAM"), "CL": ("Chile", "Chili", "SAM"),
    "CO": ("Colombia", "Colombie", "SAM"), "EC": ("Ecuador", "Équateur", "SAM"),
    "PY": ("Paraguay", "Paraguay", "SAM"), "PE": ("Peru", "Pérou", "SAM"),
    "UY": ("Uruguay", "Uruguay", "SAM"), "VE": ("Venezuela", "Venezuela", "SAM"),
    # Océanie
    "AS": ("American Samoa", "Samoa américaines", "OCE"), "AU": ("Australia", "Australie", "OCE"),
    "CK": ("Cook Islands", "Îles Cook", "OCE"), "FJ": ("Fiji", "Fidji", "OCE"),
    "PF": ("Tahiti", "Tahiti", "OCE"), "NC": ("New Caledonia", "Nouvelle-Calédonie", "OCE"),
    "NZ": ("New Zealand", "Nouvelle-Zélande", "OCE"), "PG": ("Papua New Guinea", "Papouasie-Nouvelle-Guinée", "OCE"),
    "WS": ("Samoa", "Samoa", "OCE"), "SB": ("Solomon Islands", "Îles Salomon", "OCE"),
    "TO": ("Tonga", "Tonga", "OCE"), "VU": ("Vanuatu", "Vanuatu", "OCE"),
}

# Graphies alternatives (repliées) -> ISO2.
_ALIASES: dict[str, str] = {
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "great britain": "GB", "uk": "GB",
    "turkiye": "TR", "usa": "US", "united states of america": "US",
    "cote d'ivoire": "CI", "cote divoire": "CI", "czech republic": "CZ",
    "bosnia & herzegovina": "BA", "bosnia-herzegovina": "BA", "bosnia": "BA",
    "republic of ireland": "IE", "korea republic": "KR", "korea, south": "KR",
    "republic of korea": "KR", "korea dpr": "KP", "korea, north": "KP",
    "dr congo": "CD", "congo dr": "CD", "democratic republic of the congo": "CD",
    "congo-kinshasa": "CD", "congo-brazzaville": "CG", "republic of the congo": "CG",
    "cabo verde": "CV", "cape verde islands": "CV", "macedonia": "MK", "fyr macedonia": "MK",
    "ir iran": "IR", "china pr": "CN", "taiwan": "TW", "kyrgyz republic": "KG",
    "the gambia": "GM", "swaziland": "SZ", "east timor": "TL", "burma": "MM",
    "st. kitts & nevis": "KN", "st. kitts and nevis": "KN", "st. lucia": "LC",
    "st. vincent & grenadines": "VC", "st. vincent and the grenadines": "VC",
    "trinidad & tobago": "TT", "antigua & barbuda": "AG", "sao tome & principe": "ST",
    "turks & caicos islands": "TC", "united states virgin islands": "VI",
    "u.s. virgin islands": "VI", "curacao": "CW", "netherlands antilles": "CW",
    "holland": "NL", "russian federation": "RU", "syrian arab republic": "SY",
    "viet nam": "VN", "lao pdr": "LA", "brunei darussalam": "BN", "hong kong, china": "HK",
    "macao": "MO", "chinese taipei": "TW", "palestinian territory": "PS",
    "uae": "AE", "emirates": "AE", "ivory coast": "CI", "moldova, republic of": "MD",
    "faroe": "FO", "faeroe islands": "FO", "kosova": "XK", "french polynesia": "PF",
}


def fold(name: str) -> str:
    """Minuscules, sans accents, espaces normalisés."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace("’", "'").split())


_BY_NAME: dict[str, str] = {}
for _iso, (_en, _fr, _cont) in COUNTRIES.items():
    _BY_NAME.setdefault(fold(_en), _iso)
    _BY_NAME.setdefault(fold(_fr), _iso)
for _alias, _iso in _ALIASES.items():
    _BY_NAME.setdefault(fold(_alias), _iso)


def country_iso(name: str | None) -> str | None:
    """ISO2 d'un nom de pays (toutes sources), ou None si inconnu."""
    if not name:
        return None
    return _BY_NAME.get(fold(name))


def country_meta(name: str | None) -> tuple[str, str] | None:
    """(ISO2, continent) d'un nom de pays, ou None."""
    iso = country_iso(name)
    return (iso, COUNTRIES[iso][2]) if iso else None


def country_name_fr(iso: str) -> str:
    return COUNTRIES[iso][1] if iso in COUNTRIES else iso
