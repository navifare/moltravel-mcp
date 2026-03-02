"""Gemini Flash client for routing natural-language queries to MCP tools."""

import json
import logging
import os
import ssl
from urllib.request import Request, urlopen
from datetime import date

log = logging.getLogger("molttravel.gemini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

SYSTEM_PROMPT = """\
You are a travel API router. Given a user's travel question, decide which tools to call.

Available tools:
{tools}

Rules:
- Return ONLY a JSON array: [{{"tool": "tool_name", "arguments": {{...}}}}]
- Only use tools from the list above.
- Use at most 5 tools.
- Fill in reasonable defaults when the user omits details (e.g. economy class, 1 adult).
- For kiwi tools: dates must be dd/mm/yyyy.
- Today's date: {today}

User query: {query}"""


def _build_tools_text(tools_manifest: list[dict]) -> str:
    """Format tools manifest into a readable list for the prompt."""
    parts = []
    for t in tools_manifest:
        name = t["name"]
        desc = t.get("description", "")
        params = t.get("parameters", {})
        props = params.get("properties", {})
        required = set(params.get("required", []))

        param_lines = []
        for pname, pschema in props.items():
            ptype = pschema.get("type", "string")
            pdesc = pschema.get("description", "")
            req = " (required)" if pname in required else ""
            param_lines.append(f"    - {pname}: {ptype}{req} — {pdesc}")

        param_block = "\n".join(param_lines) if param_lines else "    (no parameters)"
        parts.append(f"- {name}: {desc}\n  Parameters:\n{param_block}")

    return "\n\n".join(parts)


async def route_query(query: str, tools_manifest: list[dict]) -> list[dict]:
    """Ask Gemini Flash which tools to call and with what arguments.

    Returns a list of dicts: [{"tool": "tool_name", "arguments": {...}}, ...]
    Raises ValueError if the response cannot be parsed.
    """
    tools_text = _build_tools_text(tools_manifest)
    prompt = SYSTEM_PROMPT.format(
        tools=tools_text,
        today=date.today().strftime("%d/%m/%Y"),
        query=query,
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0},
    }).encode("utf-8")

    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    ctx = ssl.create_default_context()

    import asyncio
    loop = asyncio.get_event_loop()
    resp_data = await loop.run_in_executor(None, _call_gemini, req, ctx)

    # Extract text from Gemini response
    try:
        text = resp_data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response structure: {e}")

    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini returned invalid JSON: {e}\nRaw: {text[:500]}")

    if not isinstance(result, list):
        raise ValueError(f"Expected JSON array, got {type(result).__name__}")

    return result


def _call_gemini(req: Request, ctx: ssl.SSLContext) -> dict:
    """Synchronous HTTP call to Gemini API."""
    with urlopen(req, timeout=30, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))
