"""REST Countries provider — direct API calls to restcountries.com/v3.1."""

import json
import ssl
import asyncio
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote

API_BASE = "https://restcountries.com/v3.1"

# Fields to request when querying all countries (keep lean)
DEFAULT_FIELDS = "name,capital,currencies,languages,timezones,flags,population,region,borders"

# Full field set for single-country lookups
DETAIL_FIELDS = (
    "name,capital,currencies,languages,timezones,flags,population,"
    "region,subregion,borders,area,car,idd,maps"
)


def _get(path: str, retries: int = 2) -> list[dict] | dict:
    """GET from restcountries API and return parsed JSON."""
    url = f"{API_BASE}{path}"
    ctx = ssl.create_default_context()

    import time
    for attempt in range(retries):
        try:
            req = Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urlopen(req, timeout=15, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                return {"error": "not_found", "message": f"No results for: {path}"}
            if attempt < retries - 1 and e.code in (502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return {"error": "request_failed"}


def _format_country(c: dict) -> str:
    """Format a single country dict into readable text."""
    name = c.get("name", {})
    common = name.get("common", "Unknown")
    official = name.get("official", "")

    lines = [f"**{common}**" + (f" ({official})" if official and official != common else "")]

    capital = c.get("capital")
    if capital:
        lines.append(f"Capital: {', '.join(capital)}")

    pop = c.get("population")
    if pop:
        lines.append(f"Population: {pop:,}")

    region = c.get("region", "")
    subregion = c.get("subregion", "")
    if region:
        loc = region + (f" / {subregion}" if subregion else "")
        lines.append(f"Region: {loc}")

    area = c.get("area")
    if area:
        lines.append(f"Area: {area:,.0f} km²")

    currencies = c.get("currencies", {})
    if currencies:
        parts = []
        for code, info in currencies.items():
            symbol = info.get("symbol", "")
            cname = info.get("name", code)
            parts.append(f"{cname} ({code}{', ' + symbol if symbol else ''})")
        lines.append(f"Currencies: {', '.join(parts)}")

    languages = c.get("languages", {})
    if languages:
        lines.append(f"Languages: {', '.join(languages.values())}")

    timezones = c.get("timezones", [])
    if timezones:
        lines.append(f"Timezones: {', '.join(timezones)}")

    borders = c.get("borders", [])
    if borders:
        lines.append(f"Borders: {', '.join(borders)}")

    car = c.get("car", {})
    if car:
        side = car.get("side", "")
        if side:
            lines.append(f"Driving side: {side}")

    idd = c.get("idd", {})
    if idd:
        root = idd.get("root", "")
        suffixes = idd.get("suffixes", [])
        if root and suffixes:
            codes = [f"{root}{s}" for s in suffixes[:3]]
            lines.append(f"Dial code: {', '.join(codes)}")

    flags = c.get("flags", {})
    if flags:
        flag_url = flags.get("png") or flags.get("svg")
        if flag_url:
            lines.append(f"Flag: {flag_url}")

    return "\n".join(lines)


def _search_sync(
    query: str,
    search_by: str = "name",
    fields: str | None = None,
) -> dict:
    """Synchronous REST Countries lookup."""
    encoded = quote(query, safe="")
    use_fields = fields or DETAIL_FIELDS

    if search_by == "name":
        path = f"/name/{encoded}?fields={use_fields}"
    elif search_by == "code":
        path = f"/alpha/{encoded}?fields={use_fields}"
    elif search_by == "currency":
        path = f"/currency/{encoded}?fields={use_fields}"
    elif search_by == "language":
        path = f"/lang/{encoded}?fields={use_fields}"
    elif search_by == "region":
        path = f"/region/{encoded}?fields={use_fields}"
    elif search_by == "all":
        use_fields = fields or DEFAULT_FIELDS
        path = f"/all?fields={use_fields}"
    else:
        return {"error": f"Unknown search_by: {search_by}. Use: name, code, currency, language, region, all."}

    result = _get(path)

    # Format response
    if isinstance(result, dict) and "error" in result:
        return result

    if isinstance(result, list):
        if len(result) == 0:
            return {"error": "No countries found."}
        sections = [_format_country(c) for c in result[:20]]
        text = f"Found {len(result)} country/countries:\n\n" + "\n\n---\n\n".join(sections)
        if len(result) > 20:
            text += f"\n\n... and {len(result) - 20} more."
    elif isinstance(result, dict):
        text = _format_country(result)
    else:
        text = json.dumps(result, indent=2)

    return {
        "result": {
            "content": [{"type": "text", "text": text}]
        }
    }


async def get_country_info(
    query: str,
    search_by: str = "name",
    fields: str | None = None,
) -> dict:
    """Async wrapper for REST Countries API lookup."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _search_sync,
        query,
        search_by,
        fields,
    )
