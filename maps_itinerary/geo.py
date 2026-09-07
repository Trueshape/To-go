"""Distances and travel times between places.

Falls back to great-circle distance with a mode-dependent speed when there is no
API key. With a key it uses the Routes API compute_route_matrix for real times.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

import requests

from .model import Place

# rough door-to-door speeds incl. stops/walking, km/h
_FALLBACK_SPEED_KMH = {"walking": 4.5, "transit": 16.0, "driving": 22.0}
_ROUTE_MATRIX = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_MODE_MAP = {"walking": "WALK", "transit": "TRANSIT", "driving": "DRIVE"}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass
class Matrix:
    minutes: list[list[float]]
    km: list[list[float]]
    mode: str
    real: bool  # True when it came from the Routes API

    def time(self, i: int, j: int) -> float:
        return self.minutes[i][j]

    def dist(self, i: int, j: int) -> float:
        return self.km[i][j]


def build_matrix(places: list[Place], mode: str, api_key: str | None = None) -> Matrix:
    api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    n = len(places)
    if api_key and n <= 25:  # matrix API caps elements; keep it to day-sized sets
        try:
            return _routes_matrix(places, mode, api_key)
        except requests.RequestException as e:
            print(f"  ! Routes API matrix failed ({e}); using straight-line estimate")
    return _fallback_matrix(places, mode)


def _fallback_matrix(places: list[Place], mode: str) -> Matrix:
    speed = _FALLBACK_SPEED_KMH.get(mode, 16.0)
    n = len(places)
    km = [[0.0] * n for _ in range(n)]
    mins = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = haversine_km(
                (places[i].lat, places[i].lng), (places[j].lat, places[j].lng)
            )
            # detour factor: real routes are longer than the crow flies
            d_route = d * (1.3 if mode == "walking" else 1.4)
            km[i][j] = round(d_route, 2)
            mins[i][j] = round(d_route / speed * 60 + (4 if mode == "transit" else 0), 1)
    return Matrix(mins, km, mode, real=False)


def _routes_matrix(places: list[Place], mode: str, api_key: str) -> Matrix:
    pts = [
        {
            "waypoint": {
                "location": {"latLng": {"latitude": p.lat, "longitude": p.lng}}
            }
        }
        for p in places
    ]
    body = {
        "origins": pts,
        "destinations": pts,
        "travelMode": _MODE_MAP.get(mode, "TRANSIT"),
    }
    if mode == "driving":
        body["routingPreference"] = "TRAFFIC_AWARE"
    r = requests.post(
        _ROUTE_MATRIX,
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "originIndex,destinationIndex,duration,distanceMeters,condition",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    n = len(places)
    km = [[0.0] * n for _ in range(n)]
    mins = [[0.0] * n for _ in range(n)]
    for cell in r.json():
        i, j = cell.get("originIndex", 0), cell.get("destinationIndex", 0)
        if i == j:
            continue
        secs = float(str(cell.get("duration", "0s")).rstrip("s") or 0)
        mins[i][j] = round(secs / 60, 1)
        km[i][j] = round(cell.get("distanceMeters", 0) / 1000, 2)
    return Matrix(mins, km, mode, real=True)
