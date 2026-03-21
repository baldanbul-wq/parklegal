import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.strtree import STRtree

COURTS_URL = "https://mos-gorsud.ru/api/courts"
REFRESH_EVERY_SECONDS = 24 * 3600
HTTP_TIMEOUT = 25


@dataclass(frozen=True)
class CourtHit:
    id: str
    full_name: str
    address: str
    phones: Optional[str]
    subway: Optional[str]
    code: Optional[str]


_CACHE: Dict[str, Any] = {
    "loaded_at": 0.0,
    "last_error": None,
    "geoms": [],
    "geom_to_court": {},
    "index": None,
}


async def _fetch_courts() -> List[dict]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "ParkLegal-DocGen/1.0",
        "Referer": "https://mos-gorsud.ru/territorial",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(COURTS_URL, headers=headers)
        r.raise_for_status()
        return r.json()


def _looks_like_point(x) -> bool:
    return (
        isinstance(x, list)
        and len(x) >= 2
        and isinstance(x[0], (int, float))
        and isinstance(x[1], (int, float))
    )


def _to_xy_ring(points: list, assume: str) -> List[Tuple[float, float]]:
    # assume: "latlon" or "lonlat"
    ring: List[Tuple[float, float]] = []
    for pt in points:
        if not _looks_like_point(pt):
            continue
        a, b = float(pt[0]), float(pt[1])
        if assume == "latlon":
            lat, lon = a, b
        else:
            lon, lat = a, b
        ring.append((lon, lat))  # shapely expects (x=lon, y=lat)
    return ring


def _polygon_from_rings(rings: list, assume: str):
    if not rings or not isinstance(rings[0], list):
        return None
    outer = _to_xy_ring(rings[0], assume)
    if len(outer) < 4:
        return None

    holes_xy: List[List[Tuple[float, float]]] = []
    for hole in rings[1:]:
        if not isinstance(hole, list) or not hole:
            continue
        h = _to_xy_ring(hole, assume)
        if len(h) >= 4:
            holes_xy.append(h)

    try:
        p = Polygon(shell=outer, holes=holes_xy if holes_xy else None)
    except Exception:
        return None

    if not p.is_valid:
        try:
            p = p.buffer(0)
        except Exception:
            return None

    if p.is_empty:
        return None
    return p


def _bbox_score_for_moscow(geom) -> float:
    # Чем ближе bbox к Москве (lon ~ 37, lat ~ 55), тем лучше.
    # Возвращаем большую величину = лучше.
    try:
        minx, miny, maxx, maxy = geom.bounds  # x=lon, y=lat
    except Exception:
        return -1e9

    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0

    # Москва примерно: lon 36..38.5, lat 55..56.2
    ok_x = 36.0 <= cx <= 38.8
    ok_y = 54.8 <= cy <= 56.5

    # штраф за удалённость
    dist = abs(cx - 37.6) + abs(cy - 55.75)

    score = 0.0
    if ok_x:
        score += 5.0
    if ok_y:
        score += 5.0
    score -= dist * 10.0
    return score


def _build_geom_for_court(court: dict):
    pd = court.get("polygonData")
    if not pd:
        return None

    try:
        data = json.loads(pd)
    except Exception:
        return None

    if not isinstance(data, list) or not data:
        return None

    polys_latlon: List[Polygon] = []
    polys_lonlat: List[Polygon] = []

    for poly in data:
        if not isinstance(poly, list) or not poly:
            continue

        if _looks_like_point(poly[0]):
            rings = [poly]
        else:
            rings = poly

        p1 = _polygon_from_rings(rings, "latlon")
        if p1 is not None:
            if p1.geom_type == "Polygon":
                polys_latlon.append(p1)
            elif p1.geom_type == "MultiPolygon":
                polys_latlon.extend(list(p1.geoms))

        p2 = _polygon_from_rings(rings, "lonlat")
        if p2 is not None:
            if p2.geom_type == "Polygon":
                polys_lonlat.append(p2)
            elif p2.geom_type == "MultiPolygon":
                polys_lonlat.extend(list(p2.geoms))

    if not polys_latlon and not polys_lonlat:
        return None

    # Выбираем вариант, который “похож на Москву” по bbox
    geom_a = polys_latlon[0] if polys_latlon else None
    geom_b = polys_lonlat[0] if polys_lonlat else None

    score_a = _bbox_score_for_moscow(geom_a) if geom_a is not None else -1e9
    score_b = _bbox_score_for_moscow(geom_b) if geom_b is not None else -1e9

    polys = polys_latlon if score_a >= score_b else polys_lonlat
    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    return MultiPolygon(polys)


def _rebuild_cache(raw: List[dict]) -> None:
    geoms: List[Polygon | MultiPolygon] = []
    geom_to_court: Dict[int, dict] = {}

    for c in raw:
        g = _build_geom_for_court(c)
        if g is None:
            continue
        geoms.append(g)
        geom_to_court[id(g)] = c

    _CACHE["geoms"] = geoms
    _CACHE["geom_to_court"] = geom_to_court
    _CACHE["index"] = STRtree(geoms) if geoms else None


async def refresh_courts(force: bool = False) -> None:
    now = time.time()
    if (not force) and _CACHE["index"] and (now - _CACHE["loaded_at"] < REFRESH_EVERY_SECONDS):
        return

    raw = await _fetch_courts()
    _rebuild_cache(raw)
    _CACHE["loaded_at"] = now
    _CACHE["last_error"] = None


async def refresh_loop() -> None:
    while True:
        try:
            await refresh_courts(force=False)
        except Exception as e:
            _CACHE["last_error"] = str(e)
        await asyncio.sleep(REFRESH_EVERY_SECONDS)


def find_court_by_latlon(lat: float, lon: float) -> Optional[CourtHit]:
    """
    Shapely 2.x: STRtree.query(...) возвращает индексы (numpy.int64),
    поэтому достаем геометрию через geoms[i].
    """
    idx = _CACHE.get("index")
    geoms = _CACHE.get("geoms") or []
    geom_to_court = _CACHE.get("geom_to_court") or {}

    if not idx or not geoms:
        return None

    point = Point(lon, lat)

    try:
        cand_idx = idx.query(point)  # numpy array of indices (Shapely 2)
    except Exception:
        return None

    # Пробуем сначала covers (включая границу), потом contains
    for i in cand_idx:
        try:
            g = geoms[int(i)]
            if g.covers(point) or g.contains(point):
                c = geom_to_court.get(id(g))
                if not c:
                    continue
                return CourtHit(
                    id=str(c.get("id", "")),
                    full_name=str(c.get("fullName", "")),
                    address=str(c.get("address", "")),
                    phones=(c.get("phones") or None),
                    subway=(c.get("subwayStation") or None),
                    code=(str(c.get("code")) if c.get("code") is not None else None),
                )
        except Exception:
            continue

    return None
    point = Point(float(lon), float(lat))
    candidates = idx.query(point)

    for g in candidates:
        try:
            if g.covers(point):
                c = geom_to_court.get(id(g))
                if not c:
                    continue
                return CourtHit(
                    id=str(c.get("id", "")),
                    code=str(c.get("code", "")) if c.get("code") is not None else None,
                    full_name=str(c.get("fullName", "")),
                    address=str(c.get("address", "")),
                    phones=(c.get("phones") or None),
                    subway=(c.get("subwayStation") or None),
                )
        except Exception:
            continue

    return None
