"""Generic MCP client — handles handshake, session, and tool calls for any MCP server."""

import json
import uuid
import ssl
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class McpClient:
    """Stateless MCP client that initializes a fresh session per call."""

    def __init__(self, url: str, client_name: str = "molttravel", timeout: int = 90):
        self.url = url
        self.client_name = client_name
        self.timeout = timeout
        self._ctx = ssl.create_default_context()

    # -- low-level transport --

    def _post(self, data: dict, headers: dict | None = None, retries: int = 3) -> tuple[str, dict]:
        body = json.dumps(data).encode("utf-8")
        for attempt in range(retries):
            try:
                req = Request(self.url, data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Accept", "application/json, text/event-stream")
                if headers:
                    for k, v in headers.items():
                        req.add_header(k, v)
                with urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    return resp.read().decode("utf-8"), dict(resp.headers)
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

    @staticmethod
    def _parse(body: str, headers: dict) -> dict:
        ct = headers.get("Content-Type", headers.get("content-type", ""))
        if "text/event-stream" in ct:
            last = None
            for line in body.split("\n"):
                if line.startswith("data: "):
                    try:
                        last = json.loads(line[6:])
                    except json.JSONDecodeError:
                        pass
            return last or {}
        return json.loads(body) if body.strip() else {}

    @staticmethod
    def _msg(method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
        if params:
            payload["params"] = params
        return payload

    # -- MCP handshake --

    def _handshake(self) -> dict[str, str]:
        """Initialize + notifications/initialized. Returns session headers."""
        body, headers = self._post(self._msg("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": self.client_name, "version": "1.0.0"},
        }))
        self._parse(body, headers)  # consume init response

        session_id = headers.get("Mcp-Session-Id", headers.get("mcp-session-id", ""))
        sh: dict[str, str] = {}
        if session_id:
            sh["Mcp-Session-Id"] = session_id

        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sh)
        except Exception:
            pass

        return sh

    # -- public API --

    def list_tools(self) -> list[dict]:
        """Handshake + tools/list. Returns the list of tool definitions."""
        sh = self._handshake()
        body, headers = self._post(self._msg("tools/list"), sh)
        data = self._parse(body, headers)
        return data.get("result", {}).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Handshake + tools/call. Returns the raw MCP result dict."""
        sh = self._handshake()
        body, headers = self._post(
            self._msg("tools/call", {"name": tool_name, "arguments": arguments}),
            sh,
        )
        return self._parse(body, headers)
