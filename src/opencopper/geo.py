"""Approximate country centroids for the world map (display only).

Production-weighted-ish centroids for the producing regions, not geographic
centers — e.g. Australia points at the Pilbara/WA mining belt, not Uluru.
Rough by design; the map is a communication device, not a GIS.
"""

from __future__ import annotations

COUNTRY_CENTROIDS: dict[str, tuple[float, float]] = {
    "Argentina": (-31.0, -64.0),
    "Australia": (-23.0, 120.0),        # Pilbara / WA mining belt
    "Bahrain": (26.0, 50.6),
    "Bolivia": (-19.0, -65.8),
    "Brazil": (-6.0, -50.0),            # Carajás
    "Burma (Myanmar)": (22.0, 97.5),    # Wa State
    "Canada": (52.0, -100.0),
    "Chile": (-24.0, -69.0),            # Atacama copper belt
    "China": (35.0, 103.0),
    "Congo (Kinshasa)": (-10.7, 26.0),  # Copperbelt / Katanga
    "Cuba": (21.0, -78.0),
    "Ghana": (6.5, -1.5),
    "India": (22.0, 79.0),
    "Indonesia": (-2.5, 120.0),         # Sulawesi nickel / Papua
    "Iran": (32.0, 53.0),
    "Kazakhstan": (48.0, 67.0),
    "Madagascar": (-19.0, 47.0),
    "Mali": (17.0, -4.0),
    "Mexico": (27.0, -107.0),           # Sonora
    "New Caledonia": (-21.5, 165.5),
    "Norway": (61.0, 9.0),
    "Peru": (-14.0, -72.0),
    "Philippines": (10.0, 125.0),
    "Poland": (51.5, 16.0),             # Lubin copper
    "Russia": (60.0, 90.0),             # Norilsk-ish longitude
    "South Africa": (-26.0, 27.0),
    "Thailand": (15.0, 101.0),
    "United Arab Emirates": (24.0, 54.0),
    "United States": (39.0, -110.0),    # SW copper / mountain west
    "Zambia": (-12.8, 27.8),            # Copperbelt
    "Zimbabwe": (-19.0, 30.0),
    "Saudi Arabia": (24.0, 45.0),
    "Iraq": (31.0, 44.4),
    "Kuwait": (29.3, 47.5),
    "Qatar": (25.3, 51.2),
    "France": (47.0, 2.5),
    "Ukraine": (49.0, 32.0),
    "Namibia": (-22.5, 17.0),           # Rossing/Husab uranium belt
    "Uzbekistan": (41.5, 64.0),
    "Gabon": (-1.5, 13.0),              # Moanda manganese
    "Mozambique": (-13.5, 38.5),        # Balama graphite
}


def centroid(country: str) -> tuple[float, float] | None:
    return COUNTRY_CENTROIDS.get(country)
