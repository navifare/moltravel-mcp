"""MoltTravel MCP Server — exposes all tools from all providers.

At startup, discovers tools from every upstream MCP provider and registers
them on this server with a `provider_toolname` naming convention.
Non-MCP providers (like restcountries) are registered manually.

Recommended agent flow for flights:
  1. kiwi_search-flight  — broad flight search
  2. navifare_format_flight_pricecheck_request — format a Kiwi result for price checking
  3. navifare_flight_pricecheck — compare prices across booking sites
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional

import pydantic
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata

from providers import MCP_PROVIDERS, get_country_info, get_travel_advice, list_fcdo_countries
from providers import airports, airlines, visas

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("molttravel")

server = FastMCP(
    "molttravel",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
    instructions=(
        "MoltTravel aggregates travel tools from multiple providers.\n\n"
        "Recommended flow for flights:\n"
        "1. Use `kiwi_search-flight` to search for flights.\n"
        "2. Use `navifare_format_flight_pricecheck_request` to format a specific "
        "flight result for price checking.\n"
        "3. Use `navifare_flight_pricecheck` to compare prices across booking sites.\n\n"
        "Experiences & Activities:\n"
        "- `peek_search_experiences` — search for travel experiences and activities\n"
        "- `peek_experience_details` — get details about a specific experience\n"
        "- `peek_experience_availability` — check availability and pricing\n"
        "- `peek_search_regions` — find region IDs for location-based searches\n"
        "- `peek_list_tags` — list category tags for filtering\n"
        "- `peek_render_activity_tiles` — render embeddable activity widgets\n\n"
        "Airport & Airline Reference:\n"
        "- `airports_lookup` — look up airport by IATA/ICAO code\n"
        "- `airports_search` — search airports by name/city\n"
        "- `airports_near` — find airports near a lat/lon point\n"
        "- `airlines_lookup` — look up airline by IATA/ICAO code\n"
        "- `airlines_search` — search airlines by name\n\n"
        "Visa Requirements:\n"
        "- `visa_check` — check visa requirement for a passport+destination pair\n"
        "- `visa_summary` — overview of visa-free access for a passport country\n\n"
        "Other tools:\n"
        "- `restcountries_country_info` — look up country details (capital, currency, timezone, etc.)\n"
        "- `fcdo_travel_advice` — get UK FCDO travel advice for a specific country\n"
        "- `fcdo_list_countries` — list all countries with FCDO travel advice\n"
        "- `data_status` — check which static datasets are loaded\n\n"
        "All tools are prefixed with their provider name (e.g. kiwi_, navifare_, peek_, restcountries_, fcdo_)."
    ),
)


def _extract_text(result: dict) -> str:
    """Pull text content out of an MCP tool result."""
    content = result.get("result", {}).get("content", [])
    parts = []
    for item in content:
        if item.get("type") == "text":
            parts.append(item["text"])
    return "\n".join(parts) if parts else json.dumps(result, indent=2)


_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _build_arg_model(tool_name: str, input_schema: dict) -> type[ArgModelBase]:
    """Build a dynamic Pydantic model from a JSON Schema ``inputSchema``."""
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    fields: dict[str, Any] = {}

    for prop_name, prop_schema in properties.items():
        json_type = prop_schema.get("type", "string")
        py_type = _JSON_TYPE_MAP.get(json_type, Any)
        default = prop_schema.get("default", ...)

        if prop_name not in required and default is ...:
            py_type = Optional[py_type]
            default = None

        field_kwargs: dict[str, Any] = {}
        if prop_schema.get("description"):
            field_kwargs["description"] = prop_schema["description"]

        fields[prop_name] = (py_type, pydantic.Field(default=default, **field_kwargs))

    return pydantic.create_model(
        f"{tool_name}_Args",
        __base__=ArgModelBase,
        **fields,
    )


def _register_mcp_tool(provider_name: str, tool_def: dict):
    """Register one upstream MCP tool on our server."""
    upstream_name = tool_def["name"]
    local_name = f"{provider_name}_{upstream_name}"
    description = tool_def.get("description", "")
    input_schema = tool_def.get("inputSchema", {"type": "object", "properties": {}})

    async def handler(**kwargs) -> str:
        client = MCP_PROVIDERS[provider_name]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, client.call_tool, upstream_name, kwargs
        )
        return _extract_text(result)

    handler.__name__ = local_name
    handler.__doc__ = f"[{provider_name}] {description}"

    # Build dynamic arg model from upstream inputSchema
    arg_model = _build_arg_model(local_name, input_schema)
    meta = FuncMetadata(arg_model=arg_model, wrap_output=False)

    tool = Tool(
        fn=handler,
        name=local_name,
        description=f"[{provider_name}] {description}",
        parameters=input_schema,
        fn_metadata=meta,
        is_async=True,
        context_kwarg=None,
    )
    server._tool_manager._tools[local_name] = tool
    log.info(f"Registered tool: {local_name}")


def discover_and_register():
    """Discover tools from all MCP providers and register them."""
    for name, client in MCP_PROVIDERS.items():
        try:
            tools = client.list_tools()
            log.info(f"{name}: found {len(tools)} tool(s)")
            for tool_def in tools:
                _register_mcp_tool(name, tool_def)
        except Exception as e:
            log.warning(f"{name}: failed to discover tools — {e}")


# -- Non-MCP tools (registered manually) --

@server.tool(name="restcountries_country_info", description="[restcountries] Look up country information from REST Countries.")
async def restcountries_country_info(
    query: str,
    search_by: str = "name",
    fields: str | None = None,
) -> str:
    """[restcountries] Look up country information from REST Countries.

    Args:
        query: Search term — country name, ISO code, currency code, language, or region.
               Examples: "Switzerland", "CH", "CHF", "french", "europe".
               Ignored when search_by is "all".
        search_by: Search mode — "name" (default), "code" (ISO 3166-1 alpha-2/3),
                   "currency", "language", "region", or "all".
        fields: Comma-separated fields to return. Default includes name, capital,
                currencies, languages, timezones, flags, population, region, borders.
    """
    result = await get_country_info(query=query, search_by=search_by, fields=fields)
    if "error" in result:
        return f"Error: {result['error']}" + (f" — {result.get('message', '')}" if "message" in result else "")
    return _extract_text(result)


@server.tool(name="fcdo_travel_advice", description="[fcdo] Get UK FCDO travel advice for a specific country. Includes safety, entry requirements, health, and warnings.")
async def fcdo_travel_advice(country: str) -> str:
    """[fcdo] Get UK FCDO travel advice for a specific country.

    Args:
        country: Country name (e.g. "Spain", "South Korea", "United Arab Emirates").
                 Will be converted to the GOV.UK slug format automatically.
    """
    result = await get_travel_advice(country=country)
    if "error" in result:
        return f"Error: {result['error']}" + (f" — {result.get('message', '')}" if "message" in result else "")
    return _extract_text(result)


@server.tool(name="fcdo_list_countries", description="[fcdo] List all countries with UK FCDO travel advice.")
async def fcdo_list_countries() -> str:
    """[fcdo] List all countries that have UK FCDO travel advice available."""
    result = await list_fcdo_countries()
    if "error" in result:
        return f"Error: {result['error']}" + (f" — {result.get('message', '')}" if "message" in result else "")
    return _extract_text(result)


# -- Static data tools --

@server.tool(name="airports_lookup", description="Look up an airport by IATA (3-char) or ICAO (4-char) code. Returns full details including runways.")
async def airports_lookup(code: str) -> str:
    """Look up an airport by IATA (3-char) or ICAO (4-char) code.

    Args:
        code: IATA code (e.g. "ZRH", "JFK") or ICAO code (e.g. "LSZH", "KJFK").
    """
    return await airports.lookup_airport(code)


@server.tool(name="airports_search", description="Search airports by name or city. Returns up to 20 results sorted by size.")
async def airports_search(
    query: str,
    country: str | None = None,
    type_filter: str | None = None,
) -> str:
    """Search airports by name or municipality substring.

    Args:
        query: Search term (e.g. "Zurich", "Heathrow", "international").
        country: Optional ISO country code filter (e.g. "CH", "US", "GB").
        type_filter: Optional type filter: large_airport, medium_airport, small_airport, heliport, seaplane_base.
    """
    return await airports.search_airports(query, country=country, type_filter=type_filter)


@server.tool(name="airports_near", description="Find airports near a geographic point. Returns results sorted by distance.")
async def airports_near(
    latitude: float,
    longitude: float,
    radius_km: float = 100,
    limit: int = 10,
    include_small: bool = False,
) -> str:
    """Find airports near a latitude/longitude point.

    Args:
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.
        radius_km: Search radius in km (default 100, max 500).
        limit: Max results (default 10, max 50).
        include_small: If False (default), only airports with scheduled service.
    """
    return await airports.airports_near(latitude, longitude, radius_km=radius_km, limit=limit, include_small=include_small)


@server.tool(name="airlines_lookup", description="Look up an airline by IATA (2-char) or ICAO (3-char) code.")
async def airlines_lookup(code: str) -> str:
    """Look up an airline by IATA (2-char) or ICAO (3-char) code.

    Args:
        code: IATA code (e.g. "LX", "BA") or ICAO code (e.g. "SWR", "BAW").
    """
    return await airlines.lookup_airline(code)


@server.tool(name="airlines_search", description="Search airlines by name. Returns up to 20 results.")
async def airlines_search(
    query: str,
    country: str | None = None,
    active_only: bool = True,
) -> str:
    """Search airlines by name substring.

    Args:
        query: Search term (e.g. "Swiss", "British", "Delta").
        country: Optional country name filter (e.g. "Switzerland", "United Kingdom").
        active_only: If True (default), only show active airlines.
    """
    return await airlines.search_airlines(query, country=country, active_only=active_only)


@server.tool(name="visa_check", description="Check visa requirement for a passport country visiting a destination.")
async def visa_check(passport: str, destination: str) -> str:
    """Check visa requirement for a passport+destination pair.

    Args:
        passport: Passport country — name (e.g. "Switzerland"), alias ("USA", "UK"), or ISO code.
        destination: Destination country — name, alias, or ISO code.
    """
    return await visas.check_visa(passport, destination)


@server.tool(name="visa_summary", description="Overview of visa-free access for a passport country — counts by category.")
async def visa_summary(passport: str) -> str:
    """Get a summary of visa requirements for a passport country.

    Args:
        passport: Passport country — name (e.g. "Switzerland"), alias ("USA", "UK"), or ISO code.
    """
    return await visas.visa_summary(passport)


@server.tool(name="data_status", description="Check which static datasets (airports, airlines, visas) are loaded and their row counts.")
async def data_status() -> str:
    """Check which static datasets are loaded and their row counts."""
    airport_count = await airports.get_airport_count()
    airline_count = await airlines.get_airline_count()
    visa_count = await visas.get_visa_count()

    lines = ["Static data status:"]
    lines.append(f"  Airports: {'loaded' if airport_count else 'not loaded'} ({airport_count} records)")
    lines.append(f"  Airlines: {'loaded' if airline_count else 'not loaded'} ({airline_count} records)")
    lines.append(f"  Visas:    {'loaded' if visa_count else 'not loaded'} ({visa_count} records)")
    return "\n".join(lines)


# -- Startup --

discover_and_register()

if __name__ == "__main__":
    server.run(transport="streamable-http")
