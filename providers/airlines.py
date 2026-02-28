"""Airline data provider — OpenFlights (ODbL 1.0).

Lazy-loads airlines.dat (headerless positional CSV) on first call.
Provides lookup by IATA/ICAO and text search.
"""

import asyncio
import logging

from .data_loader import fetch_csv

log = logging.getLogger("molttravel.airlines")

_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"

# airlines.dat columns (no header): id, name, alias, iata, icao, callsign, country, active
_FIELDNAMES = ["id", "name", "alias", "iata", "icao", "callsign", "country", "active"]

_data: dict | None = None
_lock = asyncio.Lock()


def _clean(val: str | None) -> str | None:
    """Normalize OpenFlights nulls: \\N and empty → None."""
    if val is None:
        return None
    val = val.strip()
    if val in ("\\N", "", "-"):
        return None
    return val


async def _ensure_loaded():
    global _data
    if _data is not None:
        return
    async with _lock:
        if _data is not None:
            return
        log.info("Loading airline data …")
        try:
            rows = await fetch_csv(_URL, has_header=False, fieldnames=_FIELDNAMES)
        except Exception as e:
            log.warning(f"Failed to load airline data: {e}")
            _data = {"airlines": [], "by_iata": {}, "by_icao": {}, "count": 0}
            return

        airlines: list[dict] = []
        by_iata: dict[str, list[dict]] = {}
        by_icao: dict[str, list[dict]] = {}

        for row in rows:
            al = {
                "id": _clean(row.get("id")),
                "name": _clean(row.get("name")) or "Unknown",
                "alias": _clean(row.get("alias")),
                "iata": _clean(row.get("iata")),
                "icao": _clean(row.get("icao")),
                "callsign": _clean(row.get("callsign")),
                "country": _clean(row.get("country")),
                "active": (_clean(row.get("active")) or "").upper() == "Y",
            }
            airlines.append(al)
            if al["iata"]:
                by_iata.setdefault(al["iata"].upper(), []).append(al)
            if al["icao"]:
                by_icao.setdefault(al["icao"].upper(), []).append(al)

        _data = {
            "airlines": airlines,
            "by_iata": by_iata,
            "by_icao": by_icao,
            "count": len(airlines),
        }
        log.info(f"Loaded {len(airlines)} airlines")


def _format_airline(al: dict) -> str:
    parts = [al["name"]]
    if al["alias"]:
        parts[0] += f' (aka "{al["alias"]}")'
    codes = []
    if al["iata"]:
        codes.append(f"IATA: {al['iata']}")
    if al["icao"]:
        codes.append(f"ICAO: {al['icao']}")
    if codes:
        parts.append("  " + "  |  ".join(codes))
    if al["callsign"]:
        parts.append(f"  Callsign: {al['callsign']}")
    if al["country"]:
        parts.append(f"  Country: {al['country']}")
    parts.append(f"  Active: {'Yes' if al['active'] else 'No'}")
    return "\n".join(parts)


def _pick_best(candidates: list[dict]) -> dict:
    """Prefer active airlines when multiple match the same code."""
    active = [a for a in candidates if a["active"]]
    return active[0] if active else candidates[0]


async def lookup_airline(code: str) -> str:
    """Look up an airline by IATA (2-char) or ICAO (3-char) code."""
    await _ensure_loaded()
    code = code.strip().upper()
    candidates = None
    if len(code) == 2:
        candidates = _data["by_iata"].get(code)
    elif len(code) == 3:
        candidates = _data["by_icao"].get(code)
    else:
        candidates = _data["by_iata"].get(code) or _data["by_icao"].get(code)

    if not candidates:
        return f"No airline found for code '{code}'."
    return _format_airline(_pick_best(candidates))


async def search_airlines(
    query: str,
    country: str | None = None,
    active_only: bool = True,
) -> str:
    """Search airlines by name substring."""
    await _ensure_loaded()
    q = query.strip().lower()
    country_lower = country.strip().lower() if country else None

    results = []
    for al in _data["airlines"]:
        if active_only and not al["active"]:
            continue
        if country_lower and (al["country"] or "").lower() != country_lower:
            continue
        name = (al["name"] or "").lower()
        alias = (al["alias"] or "").lower()
        if q in name or q in alias:
            results.append(al)

    results = results[:20]

    if not results:
        return f"No airlines found matching '{query}'."

    lines = [f"Found {len(results)} airline(s) matching '{query}':"]
    for al in results:
        iata = al["iata"] or "--"
        icao = al["icao"] or "---"
        active = "active" if al["active"] else "inactive"
        lines.append(f"  {iata:2s} | {icao:3s} | {al['name']} — {al['country'] or '?'} ({active})")
    return "\n".join(lines)


async def get_airline_count() -> int:
    """Return loaded airline count (0 if not loaded)."""
    return _data["count"] if _data else 0
