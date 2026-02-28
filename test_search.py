"""Test script — calls the MoltTravel MCP server locally."""

import json
import uuid
import urllib.request
import ssl
import sys

SERVER_URL = "http://localhost:8000/mcp"


def post_json(url, data, headers=None):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=120) as resp:
        resp_body = resp.read().decode("utf-8")
        resp_headers = dict(resp.headers)
        return resp_body, resp_headers


def parse_response(body, headers):
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
    return json.loads(body)


def main():
    session_headers = {}

    # 1. Initialize
    print("Connecting to MoltTravel MCP server...")
    body, headers = post_json(SERVER_URL, {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    })
    init = parse_response(body, headers)
    print(f"Server: {init.get('result', {}).get('serverInfo', {})}")

    session_id = headers.get("Mcp-Session-Id", headers.get("mcp-session-id", ""))
    if session_id:
        session_headers["Mcp-Session-Id"] = session_id

    # Initialized notification
    try:
        post_json(SERVER_URL, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_headers)
    except Exception:
        pass

    # 2. List tools
    print("\nListing tools...")
    body, headers = post_json(SERVER_URL, {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/list",
    }, session_headers)
    tools = parse_response(body, headers)
    for tool in tools.get("result", {}).get("tools", []):
        print(f"  - {tool['name']}: {tool.get('description', '')[:100]}")

    # 3. Search flights
    print("\nSearching: Rome → Milan, Mar 15-22, 1 adult, economy")
    body, headers = post_json(SERVER_URL, {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {
            "name": "search_flights",
            "arguments": {
                "origin": "Rome",
                "destination": "Milan",
                "departure_date": "2026-03-15",
                "return_date": "2026-03-22",
                "adults": 1,
                "cabin_class": "economy",
            },
        },
    }, session_headers)
    result = parse_response(body, headers)

    content = result.get("result", {}).get("content", [])
    for item in content:
        if item.get("type") == "text":
            print(item["text"])
        else:
            print(json.dumps(item, indent=2))


if __name__ == "__main__":
    main()
