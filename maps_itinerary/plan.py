"""Group places into days and order each day sensibly.

No external solver: k-means style geographic clustering for the days, then
nearest-neighbour + 2-opt for the visiting order inside each day, using the
travel-time matrix from geo.py.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass

from .geo import Matrix, build_matrix, haversine_km
from .model import Day, Itinerary, Leg, Place, Stop

_DIR_URL = (
    "https://www.google.com/maps/dir/?api=1"
    "&origin={o}&destination={d}&travelmode={m}"
)


@dataclass
class PlanConfig:
    days: int | None = None
    max_per_day: int = 6
    pace: str = "normal"           # relaxed | normal | packed
    travel_mode: str = "transit"
    start: tuple[float, float] | None = None
    day_start: str = "09:30"
    lunch_at: str = "13:00"
    lunch_minutes: int = 60
    region_gap_km: float = 40.0    # places farther apart than this = different bases
    merge_gap_km: float = 60.0     # thin adjacent days closer than this get merged
    api_key: str | None = None
    seed: int = 7


_PACE_FACTOR = {"relaxed": 1.25, "normal": 1.0, "packed": 0.8}
# how many minutes of touring a day should hold, by pace (visits + hopping)
_PACE_DAY_MINUTES = {"relaxed": 450, "normal": 540, "packed": 630}


def build_itinerary(
    places: list[Place],
    *,
    title: str,
    source_url: str,
    cfg: PlanConfig,
    enriched: bool,
    anchor_places: list[Place] | None = None,
) -> Itinerary:
    """Plan a day-by-day itinerary.

    ``anchor_places`` (a subset of ``places``) drives the day count and region
    split; every other place is dropped into the nearest resulting day. Used so
    that off-by-default entries (generic areas, closed spots) don't inflate the
    trip - they just ride along in whatever day is closest.
    """
    random.seed(cfg.seed)
    pace = _PACE_FACTOR.get(cfg.pace, 1.0)

    anchors = anchor_places or places
    extras = [p for p in places if p not in anchors]

    n_days = cfg.days or _auto_days(anchors, cfg, pace)
    n_days = max(1, min(n_days, len(anchors)))

    # 1. split into geographic regions (different cities / day-trip bases)
    regions = _regions(anchors, cfg.region_gap_km)
    # never fewer days than regions, and respect the per-day cap
    n_days = max(n_days, len(regions), math.ceil(len(anchors) / cfg.max_per_day))

    # 2. share the day budget out between regions, by size
    clusters: list[list[Place]] = []
    for region in _order_clusters(regions, cfg.start):
        share = max(1, round(n_days * len(region) / len(anchors)))
        share = max(share, math.ceil(len(region) / cfg.max_per_day))
        sub = _cluster(region, min(share, len(region)), cfg)
        sub = _rebalance(sub, cfg.max_per_day)
        clusters.extend(_order_clusters(sub, cfg.start))

    # 2b. merge adjacent days that would each finish well short of a full day
    if cfg.days is None:
        clusters = _merge_thin_days(clusters, cfg, pace)

    # 3. drop the extras into the nearest day
    for p in extras:
        near = min(clusters, key=lambda c: haversine_km((p.lat, p.lng), _centroid(c)))
        near.append(p)

    it = Itinerary(
        title=title,
        source_url=source_url,
        enriched=enriched,
        travel_mode=cfg.travel_mode,
        pace_factor=pace,
        day_start=cfg.day_start,
        lunch_at=cfg.lunch_at,
        lunch_minutes=cfg.lunch_minutes,
    )

    cursor = cfg.start
    for di, group in enumerate(clusters, start=1):
        ordered, matrix = _order_within_day(group, cfg, entry=cursor)
        day = _schedule_day(di, ordered, matrix, cfg, pace)
        it.days.append(day)
        if ordered:
            last = ordered[-1]
            cursor = (last.lat, last.lng)
    return it


# --------------------------------------------------------------------------- #
# day count + clustering
# --------------------------------------------------------------------------- #
def _auto_days(places: list[Place], cfg: PlanConfig, pace: float) -> int:
    budget = _PACE_DAY_MINUTES.get(cfg.pace, 480)
    total = sum(p.visit_minutes(pace) for p in places)
    total += 35 * max(0, len(places) - 1)  # rough inter-stop travel allowance
    by_time = math.ceil(total / budget)
    by_count = math.ceil(len(places) / cfg.max_per_day)
    return max(1, by_time, by_count)


def _regions(places: list[Place], gap_km: float) -> list[list[Place]]:
    """Single-linkage grouping: places within gap_km hop into the same region."""
    n = len(places)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(
                (places[i].lat, places[i].lng), (places[j].lat, places[j].lng)
            )
            if d <= gap_km:
                parent[find(i)] = find(j)

    buckets: dict[int, list[Place]] = {}
    for idx, p in enumerate(places):
        buckets.setdefault(find(idx), []).append(p)
    return list(buckets.values())


def _xy(p: Place | tuple[float, float]) -> tuple[float, float]:
    lat, lng = (p if isinstance(p, tuple) else (p.lat, p.lng))
    return lat, lng * math.cos(math.radians(lat))


def _cluster(places: list[Place], k: int, cfg: PlanConfig) -> list[list[Place]]:
    if k <= 1:
        return [list(places)]
    pts = [_xy(p) for p in places]
    # k-means++ seeding
    centers = [random.choice(pts)]
    while len(centers) < k:
        d2 = [min(_sq(pt, c) for c in centers) for pt in pts]
        tot = sum(d2) or 1.0
        r = random.random() * tot
        acc = 0.0
        for pt, w in zip(pts, d2):
            acc += w
            if acc >= r:
                centers.append(pt)
                break
    for _ in range(60):
        groups: list[list[int]] = [[] for _ in range(k)]
        for idx, pt in enumerate(pts):
            j = min(range(k), key=lambda c: _sq(pt, centers[c]))
            groups[j].append(idx)
        new_centers = []
        for g, c in zip(groups, centers):
            if not g:
                new_centers.append(c)
                continue
            new_centers.append(
                (sum(pts[i][0] for i in g) / len(g), sum(pts[i][1] for i in g) / len(g))
            )
        if new_centers == centers:
            break
        centers = new_centers
    return [[places[i] for i in g] for g in groups if g]


def _day_minutes(group: list[Place], pace: float) -> float:
    """Rough active minutes for a day: visits + a hop between each stop."""
    if not group:
        return 0.0
    visit = sum(p.visit_minutes(pace) for p in group)
    hop = 0.0
    for a, b in zip(group, group[1:]):
        d = haversine_km((a.lat, a.lng), (b.lat, b.lng)) * 1.4
        hop += d / 16.0 * 60 + 4
    return visit + hop


def _merge_thin_days(
    clusters: list[list[Place]], cfg: PlanConfig, pace: float
) -> list[list[Place]]:
    """Fold a day into its neighbour when both are short and close enough that
    combining them still fits a full touring day. Keeps trips from having a
    string of half-empty days."""
    budget = _PACE_DAY_MINUTES.get(cfg.pace, 540)
    out = [list(c) for c in clusters]
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            transfer_km = haversine_km(_centroid(a), _centroid(b))
            if transfer_km > cfg.merge_gap_km:
                continue
            transfer_min = transfer_km * 1.4 / 16.0 * 60
            combined = _day_minutes(a, pace) + _day_minutes(b, pace) + transfer_min
            # merge only if each day alone is light and the sum still fits
            light = _day_minutes(a, pace) < budget * 0.72 and _day_minutes(b, pace) < budget * 0.72
            if light and combined <= budget * 1.08:
                out[i] = a + b
                del out[i + 1]
                changed = True
                break
    return out


def _rebalance(clusters: list[list[Place]], max_per_day: int) -> list[list[Place]]:
    clusters = [list(c) for c in clusters]
    changed = True
    guard = 0
    while changed and guard < 200:
        changed = False
        guard += 1
        clusters.sort(key=len, reverse=True)
        for ci, c in enumerate(clusters):
            while len(c) > max_per_day:
                targets = [
                    (cj, d)
                    for cj, d in enumerate(clusters)
                    if cj != ci and len(d) < max_per_day
                ]
                if not targets:
                    clusters.append([])
                    targets = [(len(clusters) - 1, clusters[-1])]
                # move the member that fits some other day best (least disruptive),
                # not just the one farthest from this day's centre
                victim, cj, dst = min(
                    (
                        (p, cj, d)
                        for p in c
                        for cj, d in targets
                    ),
                    key=lambda t: haversine_km(
                        (t[0].lat, t[0].lng), _centroid(t[2]) or (t[0].lat, t[0].lng)
                    ),
                )
                c.remove(victim)
                dst.append(victim)
                changed = True
    clusters = _polish([c for c in clusters if c], max_per_day)
    return clusters


def _polish(clusters: list[list[Place]], max_per_day: int) -> list[list[Place]]:
    """k-means-style clean-up: send each place to the nearest day that has room."""
    for _ in range(20):
        centers = [_centroid(c) for c in clusters]
        moved = False
        for ci, c in enumerate(clusters):
            if len(c) <= 1:
                continue
            for p in list(c):
                here = haversine_km((p.lat, p.lng), centers[ci])
                best = min(
                    (
                        (cj, haversine_km((p.lat, p.lng), centers[cj]))
                        for cj in range(len(clusters))
                        if cj != ci and len(clusters[cj]) < max_per_day
                    ),
                    key=lambda t: t[1],
                    default=None,
                )
                if best and best[1] + 0.3 < here:
                    c.remove(p)
                    clusters[best[0]].append(p)
                    moved = True
        if not moved:
            break
    return [c for c in clusters if c]


def _order_clusters(
    clusters: list[list[Place]], start: tuple[float, float] | None
) -> list[list[Place]]:
    if len(clusters) <= 1:
        return clusters
    remaining = list(clusters)
    ordered = []
    cur = start or _centroid(min(remaining, key=lambda c: _centroid(c)[0]))
    while remaining:
        nxt = min(remaining, key=lambda c: haversine_km(cur, _centroid(c)))
        ordered.append(nxt)
        remaining.remove(nxt)
        cur = _centroid(nxt)
    return ordered


# --------------------------------------------------------------------------- #
# ordering inside a day
# --------------------------------------------------------------------------- #
def _order_within_day(
    group: list[Place], cfg: PlanConfig, entry: tuple[float, float] | None
) -> tuple[list[Place], Matrix]:
    if len(group) <= 1:
        return group, build_matrix(group, cfg.travel_mode, cfg.api_key) if group else Matrix([], [], cfg.travel_mode, False)

    matrix = build_matrix(group, cfg.travel_mode, cfg.api_key)
    n = len(group)

    # nearest-neighbour seed from the entry point
    start_i = 0
    if entry:
        start_i = min(range(n), key=lambda i: haversine_km(entry, (group[i].lat, group[i].lng)))
    unvisited = set(range(n))
    order = [start_i]
    unvisited.discard(start_i)
    while unvisited:
        last = order[-1]
        nxt = min(unvisited, key=lambda j: matrix.time(last, j))
        order.append(nxt)
        unvisited.discard(nxt)

    order = _two_opt(order, matrix)
    return [group[i] for i in order], _reindex_matrix(matrix, order)


def _two_opt(order: list[int], matrix: Matrix) -> list[int]:
    def cost(o):
        return sum(matrix.time(o[i], o[i + 1]) for i in range(len(o) - 1))

    best = order[:]
    best_c = cost(best)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                c = cost(cand)
                if c + 1e-6 < best_c:
                    best, best_c = cand, c
                    improved = True
    return best


def _reindex_matrix(matrix: Matrix, order: list[int]) -> Matrix:
    m = [[matrix.minutes[a][b] for b in order] for a in order]
    k = [[matrix.km[a][b] for b in order] for a in order]
    return Matrix(m, k, matrix.mode, matrix.real)


# --------------------------------------------------------------------------- #
# scheduling
# --------------------------------------------------------------------------- #
def _schedule_day(
    index: int, ordered: list[Place], matrix: Matrix, cfg: PlanConfig, pace: float
) -> Day:
    label = _day_label(ordered)
    day = Day(index=index, label=label)
    if not ordered:
        return day

    t = _to_min(cfg.day_start)
    lunch_t = _to_min(cfg.lunch_at)
    lunch_done = False

    for i, p in enumerate(ordered):
        if i > 0:
            leg_min = matrix.time(i - 1, i)
            leg_km = matrix.dist(i - 1, i)
            # short hops are always faster on foot than waiting for a train
            mode = "walking" if (leg_km <= 1.1 and cfg.travel_mode != "driving") else cfg.travel_mode
            if mode == "walking" and not matrix.real:
                leg_min = round(leg_km * 1.3 / 4.5 * 60 + 2, 1)
            day.legs.append(
                Leg(
                    from_place=ordered[i - 1].name,
                    to_place=p.name,
                    minutes=int(round(leg_min)),
                    distance_km=round(leg_km, 1),
                    mode=mode,
                    directions_url=_DIR_URL.format(
                        o=f"{ordered[i-1].lat},{ordered[i-1].lng}",
                        d=f"{p.lat},{p.lng}",
                        m=mode,
                    ),
                )
            )
            t += leg_min

        if not lunch_done and t >= lunch_t:
            t += cfg.lunch_minutes
            lunch_done = True
            p.note = (p.note + " " if p.note else "") + "(dopo pausa pranzo)"

        arrive = _to_hhmm(t)
        t += p.visit_minutes(pace)
        day.stops.append(Stop(place=p, arrive=arrive, depart=_to_hhmm(t)))

    return day


# rough centres of the areas this kind of list tends to touch; used only for
# human-readable day labels when there is no address from the Places API.
_KNOWN_AREAS = [
    ("Tokyo", 35.68, 139.75), ("Yokohama", 35.44, 139.64),
    ("Kamakura / Enoshima", 35.31, 139.49), ("Fuji / Fujiyoshida", 35.49, 138.80),
    ("Nikko", 36.75, 139.60), ("Hakone", 35.23, 139.02),
    ("Kyoto", 35.01, 135.76), ("Osaka", 34.69, 135.50), ("Nara", 34.685, 135.83),
    ("Kobe", 34.69, 135.20), ("Hiroshima", 34.39, 132.45), ("Kanazawa", 36.56, 136.65),
    ("Sapporo", 43.06, 141.35), ("Fukuoka", 33.59, 130.40),
]


def _day_label(places: list[Place]) -> str:
    if not places:
        return "—"
    # if an address names a known area, trust that
    blob = " ".join(p.address for p in places).lower()
    for name, _la, _lo in _KNOWN_AREAS:
        if name.split(" / ")[0].lower() in blob:
            return name
    # otherwise label by the nearest known area to the day's centre
    cen = _centroid(places)
    name, dist = min(
        ((n, haversine_km(cen, (la, lo))) for n, la, lo in _KNOWN_AREAS),
        key=lambda t: t[1],
    )
    base = name if dist < 70 else f"{cen[0]:.2f}, {cen[1]:.2f}"
    return base + _ward_suffix(places)


def _ward_suffix(places: list[Place]) -> str:
    wards: list[str] = []
    for p in places:
        m = re.search(r"([A-Za-zĀ-ヿ]+(?:-ku| City| Ward))", p.address)
        if m:
            wards.append(m.group(1).replace(" City", "").replace(" Ward", "").replace("-ku", ""))
    if not wards:
        return ""
    top = max(set(wards), key=wards.count)
    return f" · {top}" if wards.count(top) >= 2 else ""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _sq(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _centroid(places):
    if not places:
        return (0.0, 0.0)
    return (
        sum(p.lat for p in places) / len(places),
        sum(p.lng for p in places) / len(places),
    )


def _to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _to_hhmm(mins: float) -> str:
    mins = int(round(mins))
    return f"{mins // 60 % 24:02d}:{mins % 60:02d}"
