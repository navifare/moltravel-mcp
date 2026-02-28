"""Kiwi.com MCP client — based on the working kiwi_search.py reference script."""

import json
import uuid
import ssl
import asyncio
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from datetime import datetime

MCP_SERVER_URL = "https://mcp.kiwi.com/mcp"

CABIN_CLASS_MAP = {
    "economy": "M",
    "premium_economy": "W",
    "business": "C",
    "first": "F",
}


def _iso_to_kiwi_date(iso_date: str) -> str:
    """Convert yyyy-mm-dd to dd/mm/yyyy."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    return dt.strftime("%d/%m/%Y")


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

            with urlopen(req, timeout=60, context=ctx) as resp:
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


def _search_sync(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    cabin_class: str = "economy",
) -> dict:
    """Synchronous Kiwi MCP flight search."""
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
    init_data = _parse_response(body, headers)

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

    # Build search arguments
    args: dict = {
        "flyFrom": origin,
        "flyTo": destination,
        "departureDate": _iso_to_kiwi_date(departure_date),
        "adults": adults,
        "cabinClass": CABIN_CLASS_MAP.get(cabin_class, "M"),
    }
    if return_date:
        args["returnDate"] = _iso_to_kiwi_date(return_date)

    # Call search
    body, headers = _post_json(
        MCP_SERVER_URL,
        _mcp_request("tools/call", {"name": "search-flight", "arguments": args}),
        session_headers,
    )
    return _parse_response(body, headers)


async def search_kiwi(
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
