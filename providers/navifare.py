"""Navifare MCP client — connects to mcp.navifare.com for flight price discovery."""

import json
import uuid
import ssl
import asyncio
from urllib.request import Request, urlopen
from urllib.error import HTTPError

MCP_SERVER_URL = "https://mcp.navifare.com/mcp"


def _mcp_request(method: str, params: dict | None = None) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
    }
    if params:
        payload["params"] = params
    return payload


def _post_json(url: str, data: dict, headers: dict | None = None, retries: int = 3) -> tuple[str, dict]:
    """POST JSON and return (response_body, response_headers) with retry."""
    body = json.dumps(data).encode("utf-8")
    ctx = ssl.create_default_context()

    import time
    for attempt in range(retries):
        try:
            req = Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Accept", "application/json, text/event-stream")
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)

            with urlopen(req, timeout=90, context=ctx) as resp:
                resp_body = resp.read().decode("utf-8")
                resp_headers = dict(resp.headers)
                return resp_body, resp_headers
        except HTTPError as e:
            if attempt < retries - 1 and e.code in (502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return "", {}


def _parse_response(body: str, headers: dict) -> dict:
    content_type = headers.get("Content-Type", headers.get("content-type", ""))

    if "text/event-stream" in content_type:
        last_data = None
        for line in body.split("\n"):
            if line.startswith("data: "):
                try:
                    last_data = json.loads(line[6:])
                except json.JSONDecodeError:
                    pass
        return last_data or {}
    else:
        return json.loads(body)


def _discover_tools(session_headers: dict) -> list[dict]:
    """List available tools from Navifare MCP server."""
    body, headers = _post_json(
        MCP_SERVER_URL,
        _mcp_request("tools/list"),
        session_headers,
    )
    data = _parse_response(body, headers)
    return data.get("result", {}).get("tools", [])


def _search_sync(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    cabin_class: str = "economy",
) -> dict:
    """Synchronous Navifare MCP flight search."""
    session_headers: dict[str, str] = {}

    # Initialize
    body, headers = _post_json(
        MCP_SERVER_URL,
        _mcp_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "molttravel", "version": "1.0.0"},
        }),
    )
    _parse_response(body, headers)

    session_id = headers.get("Mcp-Session-Id", headers.get("mcp-session-id", ""))
    if session_id:
        session_headers["Mcp-Session-Id"] = session_id

    # Send initialized notification
    try:
        _post_json(
            MCP_SERVER_URL,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_headers,
        )
    except Exception:
        pass

    # Discover tools to find the flight search tool name
    tools = _discover_tools(session_headers)
    search_tool_name = None
    for tool in tools:
        name = tool.get("name", "").lower()
        if "search" in name and "flight" in name:
            search_tool_name = tool["name"]
            break
    if not search_tool_name and tools:
        # Fallback: use the first tool
        search_tool_name = tools[0]["name"]
    if not search_tool_name:
        return {"error": "No flight search tool found on Navifare"}

    # Build arguments — Navifare uses a structured trip format
    cabin_map = {
        "economy": "ECONOMY",
        "premium_economy": "PREMIUM_ECONOMY",
        "business": "BUSINESS",
        "first": "FIRST",
    }

    legs = [
        {
            "segments": [
                {
                    "departureAirport": origin,
                    "arrivalAirport": destination,
                    "departureDate": departure_date,
                }
            ]
        }
    ]
    if return_date:
        legs.append(
            {
                "segments": [
                    {
                        "departureAirport": destination,
                        "arrivalAirport": origin,
                        "departureDate": return_date,
                    }
                ]
            }
        )

    args = {
        "trip": {
            "legs": legs,
            "travelClass": cabin_map.get(cabin_class, "ECONOMY"),
            "adults": adults,
        },
        "currency": "USD",
    }

    # Call search
    body, headers = _post_json(
        MCP_SERVER_URL,
        _mcp_request("tools/call", {"name": search_tool_name, "arguments": args}),
        session_headers,
    )
    return _parse_response(body, headers)


async def search_navifare(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    cabin_class: str = "economy",
) -> dict:
    """Async wrapper — runs the sync MCP call in a thread to avoid blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _search_sync,
        origin,
        destination,
        departure_date,
        return_date,
        adults,
        cabin_class,
    )
