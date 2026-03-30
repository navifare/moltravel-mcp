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
from providers import gemini

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("molttravel")
client_log = logging.getLogger("molttravel.clients")

# Track client info by MCP session ID
_sessions: dict[str, dict] = {}


class ClientTrackingMiddleware:
    """ASGI middleware that logs MCP client identity and tool calls."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)

        # Buffer the full request body so we can inspect it and replay it
        first_msg = await receive()
        body = first_msg.get("body", b"")
        more_body = first_msg.get("more_body", False)
        all_body = bytearray(body)
        while more_body:
            msg = await receive()
            all_body.extend(msg.get("body", b""))
            more_body = msg.get("more_body", False)

        # Extract client info from headers
        headers = dict(scope.get("headers", []))
        user_agent = headers.get(b"user-agent", b"").decode("utf-8", errors="replace")
        # Get client IP from x-forwarded-for (Render proxy) or ASGI scope
        forwarded = headers.get(b"x-forwarded-for", b"").decode()
        client_ip = forwarded.split(",")[0].strip() if forwarded else (scope.get("client", [""])[0] or "")
        session_id = headers.get(b"mcp-session-id", b"").decode()

        # Parse JSON-RPC to identify the method
        try:
            payload = json.loads(bytes(all_body))
            method = payload.get("method", "")
            params = payload.get("params", {})
        except (json.JSONDecodeError, AttributeError):
            method = ""
            params = {}

        if method == "initialize":
            client_info = params.get("clientInfo", {})
            client_name = client_info.get("name", "unknown")
            client_version = client_info.get("version", "?")
            proto_version = params.get("protocolVersion", "?")
            client_log.info(
                "NEW SESSION ip=%s client=%s/%s protocol=%s ua=%s",
                client_ip, client_name, client_version, proto_version, user_agent,
            )
            # Store for later correlation (will be keyed by session ID from response)
            _sessions[f"pending:{client_ip}"] = {
                "client_name": client_name,
                "client_version": client_version,
                "ip": client_ip,
                "ua": user_agent,
            }

        elif method == "tools/call":
            tool_name = params.get("name", "?")
            args = params.get("arguments", {})
            # Look up session info
            sess = _sessions.get(session_id, _sessions.get(f"pending:{client_ip}", {}))
            client_name = sess.get("client_name", "unknown")
            client_log.info(
                "TOOL CALL ip=%s client=%s session=%s tool=%s args=%s",
                client_ip, client_name, session_id[:16] if session_id else "none",
                tool_name, json.dumps(args, default=str)[:200],
            )

        elif method == "tools/list":
            sess = _sessions.get(session_id, _sessions.get(f"pending:{client_ip}", {}))
            client_name = sess.get("client_name", "unknown")
            client_log.info(
                "TOOLS LIST ip=%s client=%s session=%s",
                client_ip, client_name, session_id[:16] if session_id else "none",
            )

        # Capture session ID from response headers to link to pending session
        captured_session_id = None
        original_send = send
        async def send_wrapper(message):
            nonlocal captured_session_id
            if message["type"] == "http.response.start":
                for name_bytes, val_bytes in message.get("headers", []):
                    if name_bytes.lower() == b"mcp-session-id":
                        captured_session_id = val_bytes.decode()
                        # Move pending session to proper key
                        pending_key = f"pending:{client_ip}"
                        if pending_key in _sessions:
                            _sessions[captured_session_id] = _sessions.pop(pending_key)
                        break
            return await original_send(message)

        # Replay the buffered body
        body_sent = False
        async def replay_receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": bytes(all_body), "more_body": False}
            return await receive()

        return await self.app(scope, replay_receive, send_wrapper)

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
        "Multi-Day Tours (TourRadar):\n"
        "- `tourradar_*` — search 50K+ multi-day tours by destination, dates, duration, and style\n"
        "- Get full itineraries, pricing, operator info, and brochure downloads\n"
        "- Best for organized tours (3+ days): guided tours, cruises, safaris, treks\n\n"
        "Experiences & Activities (Peek):\n"
        "- `peek_search_experiences` — search for day activities and experiences\n"
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
        "All tools are prefixed with their provider name (e.g. kiwi_, navifare_, tourradar_, peek_, restcountries_, fcdo_).\n\n"
        "Master tool:\n"
        "- `travel_agent` — ask any travel question in natural language and it will "
        "automatically route to the right tools and return a combined answer."
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


def _build_arg_model(tool_name: str, input_schema: dict) -> type[ArgModelBase]:
    """Build a permissive arg model — upstream MCP servers validate their own schemas."""
    fields: dict[str, Any] = {}
    for prop_name in input_schema.get("properties", {}):
        fields[prop_name] = (Any, pydantic.Field(default=None))
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
        # Strip None values so upstream sees absent fields (not null)
        cleaned = {k: v for k, v in kwargs.items() if v is not None}
        client = MCP_PROVIDERS[provider_name]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, client.call_tool, upstream_name, cleaned
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


# -- travel_agent (LLM-routed master tool) --

MAX_STEPS = 3
MAX_TOOL_CALLS = 7
TOOL_TIMEOUT = 30


def _build_tools_manifest() -> list[dict]:
    """Build a manifest of all registered tools, excluding travel_agent itself."""
    manifest = []
    for name, tool in server._tool_manager._tools.items():
        if name == "travel_agent":
            continue
        manifest.append({
            "name": name,
            "description": tool.description or "",
            "parameters": tool.parameters or {"type": "object", "properties": {}},
        })
    return manifest


async def _execute_tool(name: str, arguments: dict) -> str:
    """Execute a single tool by name with a timeout."""
    tool = server._tool_manager._tools.get(name)
    if tool is None:
        return f"Error: unknown tool '{name}'"
    try:
        result = await asyncio.wait_for(tool.fn(**arguments), timeout=TOOL_TIMEOUT)
        return str(result) if result is not None else "(no output)"
    except asyncio.TimeoutError:
        return f"Error: tool '{name}' timed out after {TOOL_TIMEOUT}s"
    except Exception as e:
        return f"Error calling '{name}': {e}"


def _normalize_plan(plan: list) -> list[list[dict]]:
    """Normalize Gemini output into [[step1_calls], [step2_calls], ...].

    Accepts both:
      - Flat list of calls: [{"tool": ...}, ...]  (legacy, single step)
      - Nested steps: [[{"tool": ...}], [{"tool": ...}]]
    """
    if not plan:
        return []
    # If first element is a list, it's already nested steps
    if isinstance(plan[0], list):
        return [step for step in plan if isinstance(step, list)]
    # Flat list → wrap in single step
    return [plan]


import re

_STEP_REF = re.compile(r"\$\{step\[(\d+)\]\.([^}]+)\}")


def _resolve_refs(obj: object, history: list[dict[str, str]]) -> object:
    """Recursively replace ${step[N].tool_name} placeholders with actual results."""
    if isinstance(obj, str):
        def _replace(m: re.Match) -> str:
            step_idx = int(m.group(1))
            tool_name = m.group(2)
            if step_idx < len(history) and tool_name in history[step_idx]:
                return history[step_idx][tool_name]
            return m.group(0)  # leave unresolved refs as-is
        return _STEP_REF.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_refs(v, history) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_refs(v, history) for v in obj]
    return obj


# -- Startup --

discover_and_register()


# Register travel_agent after MCP discovery so all tools are available
if gemini.GEMINI_API_KEY:
    @server.tool(
        name="travel_agent",
        description=(
            "Ask a travel question in natural language. "
            "Routes to the right tools automatically and returns a combined answer. "
            "Example: 'Cheapest flights from Zurich to Rome next week, and do I need a visa?'"
        ),
    )
    async def travel_agent(query: str) -> str:
        """Natural-language travel assistant that routes queries to the right tools."""
        manifest = _build_tools_manifest()

        try:
            raw_plan = await gemini.route_query(query, manifest)
        except ValueError as e:
            return f"Routing error: {e}"
        except Exception as e:
            return f"Failed to plan tool calls: {e}"

        steps = _normalize_plan(raw_plan)
        if not steps:
            return "I couldn't determine which tools to use for that query. Try being more specific."

        # Cap steps and total calls
        steps = steps[:MAX_STEPS]
        total = 0
        for i, step in enumerate(steps):
            remaining = MAX_TOOL_CALLS - total
            if remaining <= 0:
                steps = steps[:i]
                break
            steps[i] = step[:remaining]
            total += len(steps[i])

        # Validate all tool names upfront
        valid_tools = set(server._tool_manager._tools.keys()) - {"travel_agent"}
        for step in steps:
            for call in step:
                if call.get("tool") not in valid_tools:
                    return (
                        f"Routing error: unknown tool '{call.get('tool')}'. "
                        f"Available: {', '.join(sorted(valid_tools))}"
                    )

        # Execute steps sequentially; calls within each step run in parallel
        history: list[dict[str, str]] = []  # history[step_idx][tool_name] = result
        sections: list[str] = []

        for step_idx, step in enumerate(steps):
            # Resolve ${step[N].tool_name} references from previous steps
            resolved_step = _resolve_refs(step, history)

            tasks = [
                _execute_tool(call["tool"], call.get("arguments", {}))
                for call in resolved_step
            ]
            results = await asyncio.gather(*tasks)

            step_results: dict[str, str] = {}
            for call, result in zip(resolved_step, results):
                tool_name = call["tool"]
                step_results[tool_name] = result
                sections.append(f"## {tool_name}\n{result}")

            history.append(step_results)

        return "\n\n".join(sections)

    log.info("Registered tool: travel_agent (Gemini-routed)")
else:
    log.info("GEMINI_API_KEY not set — travel_agent tool not registered")

if __name__ == "__main__":
    # Wrap the ASGI app with client tracking middleware
    _original_streamable = server.streamable_http_app

    def _wrapped_streamable():
        app = _original_streamable()
        return ClientTrackingMiddleware(app)

    server.streamable_http_app = _wrapped_streamable
    server.run(transport="streamable-http")
