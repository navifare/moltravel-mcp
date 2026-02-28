"""Shared utilities for static-data providers: CSV fetching and haversine."""

import csv
import io
import logging
import math

import httpx

log = logging.getLogger("molttravel.data")

_HTTP_TIMEOUT = 30.0


async def fetch_csv(
    url: str,
    *,
    has_header: bool = True,
    fieldnames: list[str] | None = None,
    encoding: str = "utf-8",
) -> list[dict[str, str]]:
    """Download a CSV file and return rows as list of dicts.

    Args:
        url: URL to fetch.
        has_header: If True, first row is treated as header.
        fieldnames: Column names for headerless CSVs. Ignored if has_header.
        encoding: Text encoding.
    """
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    text = resp.text
    reader: csv.DictReader
    if has_header:
        reader = csv.DictReader(io.StringIO(text))
    else:
        reader = csv.DictReader(io.StringIO(text), fieldnames=fieldnames)

    return list(reader)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in km between two lat/lon points."""
    R = 6371.0
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
