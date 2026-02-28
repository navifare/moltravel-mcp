"""Airport data provider — OurAirports (Public Domain).

Lazy-loads airports, runways, countries, and regions CSVs on first call.
Provides lookup by IATA/ICAO, text search, and proximity search.
"""

import asyncio
import logging

from .data_loader import fetch_csv, haversine

log = logging.getLogger("molttravel.airports")

_URLS = {
    "airports": "https://davidmegginson.github.io/ourairports-data/airports.csv",
    "runways": "https://davidmegginson.github.io/ourairports-data/runways.csv",
    "countries": "https://davidmegginson.github.io/ourairports-data/countries.csv",
    "regions": "https://davidmegginson.github.io/ourairports-data/regions.csv",
}

# Type sort order: large airports first
_TYPE_ORDER = {
    "large_airport": 0,
    "medium_airport": 1,
    "small_airport": 2,
    "heliport": 3,
    "seaplane_base": 4,
    "balloonport": 5,
    "closed": 6,
}

_data: dict | None = None
_lock = asyncio.Lock()


async def _ensure_loaded():
    global _data
    if _data is not None:
        return
    async with _lock:
        if _data is not None:
            return
        log.info("Loading airport data …")
        try:
            airports_raw, runways_raw, countries_raw, regions_raw = await asyncio.gather(
                fetch_csv(_URLS["airports"]),
                fetch_csv(_URLS["runways"]),
                fetch_csv(_URLS["countries"]),
                fetch_csv(_URLS["regions"]),
            )
        except Exception as e:
            log.warning(f"Failed to load airport data: {e}")
            _data = {"airports": [], "by_iata": {}, "by_icao": {}, "runways": {}, "countries": {}, "regions": {}}
            return

        # Index countries and regions
        countries = {r["code"]: r["name"] for r in countries_raw}
        regions = {r["code"]: r["name"] for r in regions_raw}

        # Index runways by airport_ref (airport id)
        runways_by_airport: dict[str, list[dict]] = {}
        for rw in runways_raw:
            aid = rw.get("airport_ref", "")
            if aid:
                runways_by_airport.setdefault(aid, []).append({
                    "length_ft": rw.get("length_ft", ""),
                    "width_ft": rw.get("width_ft", ""),
                    "surface": rw.get("surface", ""),
                    "lighted": rw.get("lighted", ""),
                    "closed": rw.get("closed", ""),
                    "le_ident": rw.get("le_ident", ""),
                    "he_ident": rw.get("he_ident", ""),
                })

        # Build airport records + indexes
        by_iata: dict[str, dict] = {}
        by_icao: dict[str, dict] = {}
        airports: list[dict] = []

        for row in airports_raw:
            iata = (row.get("iata_code") or "").strip()
            icao = (row.get("ident") or "").strip()
            apt = {
                "id": row.get("id", ""),
                "ident": icao,
                "type": row.get("type", ""),
                "name": row.get("name", ""),
                "latitude": row.get("latitude_deg", ""),
                "longitude": row.get("longitude_deg", ""),
                "elevation_ft": row.get("elevation_ft", ""),
                "continent": row.get("continent", ""),
                "country_code": row.get("iso_country", ""),
                "country_name": countries.get(row.get("iso_country", ""), ""),
                "region_code": row.get("iso_region", ""),
                "region_name": regions.get(row.get("iso_region", ""), ""),
                "municipality": row.get("municipality", ""),
                "scheduled_service": row.get("scheduled_service", ""),
                "iata_code": iata,
                "home_link": row.get("home_link", ""),
                "wikipedia_link": row.get("wikipedia_link", ""),
            }
            apt["runways"] = runways_by_airport.get(row.get("id", ""), [])
            airports.append(apt)

            if iata:
                by_iata[iata.upper()] = apt
            if icao:
                by_icao[icao.upper()] = apt

        _data = {
            "airports": airports,
            "by_iata": by_iata,
            "by_icao": by_icao,
            "count": len(airports),
        }
        log.info(f"Loaded {len(airports)} airports, {len(runways_raw)} runways")


def _format_airport(apt: dict) -> str:
    lines = [
        f"{apt['name']} ({apt['iata_code'] or apt['ident']})",
        f"  Type: {apt['type']}",
        f"  Location: {apt['municipality']}, {apt['region_name']}, {apt['country_name']} ({apt['country_code']})",
        f"  Coordinates: {apt['latitude']}, {apt['longitude']}",
    ]
    if apt["elevation_ft"]:
        lines.append(f"  Elevation: {apt['elevation_ft']} ft")
    lines.append(f"  IATA: {apt['iata_code'] or 'N/A'}  |  ICAO: {apt['ident']}")
    lines.append(f"  Scheduled service: {apt['scheduled_service']}")
    if apt["runways"]:
        lines.append(f"  Runways ({len(apt['runways'])}):")
        for rw in apt["runways"]:
            idents = "/".join(filter(None, [rw.get("le_ident"), rw.get("he_ident")]))
            parts = [f"    {idents}"]
            if rw["length_ft"]:
                parts.append(f"{rw['length_ft']}×{rw['width_ft']}ft")
            if rw["surface"]:
                parts.append(rw["surface"])
            if rw["lighted"] == "1":
                parts.append("lighted")
            if rw["closed"] == "1":
                parts.append("CLOSED")
            lines.append(" — ".join(parts))
    if apt["home_link"]:
        lines.append(f"  Website: {apt['home_link']}")
    if apt["wikipedia_link"]:
        lines.append(f"  Wikipedia: {apt['wikipedia_link']}")
    return "\n".join(lines)


async def lookup_airport(code: str) -> str:
    """Look up an airport by IATA (3-char) or ICAO (4-char) code."""
    await _ensure_loaded()
    code = code.strip().upper()
    apt = None
    if len(code) == 3:
        apt = _data["by_iata"].get(code)
    elif len(code) == 4:
        apt = _data["by_icao"].get(code)
    else:
        apt = _data["by_iata"].get(code) or _data["by_icao"].get(code)

    if not apt:
        return f"No airport found for code '{code}'."
    return _format_airport(apt)


async def search_airports(
    query: str,
    country: str | None = None,
    type_filter: str | None = None,
) -> str:
    """Search airports by name/municipality substring."""
    await _ensure_loaded()
    q = query.strip().lower()
    country_upper = country.strip().upper() if country else None

    results = []
    for apt in _data["airports"]:
        if country_upper and apt["country_code"].upper() != country_upper:
            continue
        if type_filter and apt["type"] != type_filter:
            continue
        name = (apt["name"] or "").lower()
        muni = (apt["municipality"] or "").lower()
        iata = (apt["iata_code"] or "").lower()
        if q in name or q in muni or q == iata:
            results.append(apt)

    # Sort by type priority
    results.sort(key=lambda a: _TYPE_ORDER.get(a["type"], 99))
    results = results[:20]

    if not results:
        return f"No airports found matching '{query}'."

    lines = [f"Found {len(results)} airport(s) matching '{query}':"]
    for apt in results:
        iata = apt["iata_code"] or "----"
        lines.append(
            f"  {iata:4s} | {apt['ident']:4s} | {apt['name']} — "
            f"{apt['municipality']}, {apt['country_code']} ({apt['type']})"
        )
    return "\n".join(lines)


async def airports_near(
    latitude: float,
    longitude: float,
    radius_km: float = 100,
    limit: int = 10,
    include_small: bool = False,
) -> str:
    """Find airports near a lat/lon point."""
    await _ensure_loaded()
    radius_km = min(radius_km, 500)
    limit = min(limit, 50)

    candidates = []
    for apt in _data["airports"]:
        if not apt["latitude"] or not apt["longitude"]:
            continue
        if not include_small and apt["scheduled_service"] != "yes":
            continue
        try:
            alat = float(apt["latitude"])
            alon = float(apt["longitude"])
        except ValueError:
            continue
        dist = haversine(latitude, longitude, alat, alon)
        if dist <= radius_km:
            candidates.append((dist, apt))

    candidates.sort(key=lambda x: x[0])
    candidates = candidates[:limit]

    if not candidates:
        return f"No airports found within {radius_km} km of ({latitude}, {longitude})."

    lines = [f"Airports within {radius_km} km of ({latitude}, {longitude}):"]
    for dist, apt in candidates:
        iata = apt["iata_code"] or "----"
        lines.append(
            f"  {dist:6.1f} km | {iata:4s} | {apt['ident']:4s} | {apt['name']} — "
            f"{apt['municipality']}, {apt['country_code']} ({apt['type']})"
        )
    return "\n".join(lines)


async def get_airport_count() -> int:
    """Return loaded airport count (0 if not loaded)."""
    return _data["count"] if _data else 0
