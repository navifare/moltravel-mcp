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
You are MolTravel — an AI travel planning router.
Turn the user's question into the right tool calls so the calling agent
receives rich, actionable, ready-to-present results.

Today: {today}

# Tools

{tools}

# Response format

Return a JSON array of **steps**. Each step is an array of tool calls that
run in parallel. Steps execute sequentially — later steps can reference
earlier results via `${{step[N].tool_name}}`.

```
[
  [ {{"tool": "a", "arguments": {{...}}}}, {{"tool": "b", "arguments": {{...}}}} ],
  [ {{"tool": "c", "arguments": {{"input": "${{step[0].a}}"}}}} ]
]
```

Single step when all calls are independent. Max 3 steps, 7 calls total.

# Principles

1. **Be proactive.** A flight query is also a visa, country-info, safety,
   and activities query. Anticipate what travelers need and run those calls
   in parallel alongside the main request.

2. **Always verify flight prices.** When the user asks about flights, run
   this 3-step pipeline:
   - Step 1: `kiwi_search-flight` (+ enrichment tools in parallel)
   - Step 2: `navifare_format_flight_pricecheck_request` — pass the Kiwi
     result as `user_request` via `${{step[0].kiwi_search-flight}}`
   - Step 3: `navifare_flight_pricecheck` — pass the formatted data via
     `${{step[1].navifare_format_flight_pricecheck_request}}`

3. **Smart defaults** when the user omits details:
   - 1 adult, economy class
   - Currency from origin: LHR → GBP, BER → EUR, ZRH → CHF, JFK → USD,
     NRT → JPY, SYD → AUD, DXB → AED, GRU → BRL
   - Sort by price
   - "Next week" / "next month" → compute actual dd/mm/yyyy from today
   - City → main IATA code (London → LHR, Paris → CDG, Tokyo → NRT)

4. **Read tool descriptions carefully.** Each tool's description and
   parameter list contain format requirements (date formats, slug styles,
   enum values). Follow them exactly. Don't guess — the descriptions are
   the source of truth. If a tool has no description for a parameter,
   pass a reasonable value based on the parameter name.

# Example

"Fly Zurich to Tokyo next Friday for a week. What should I know?"

```json
[
  [
    {{"tool": "kiwi_search-flight", "arguments": {{"flyFrom": "ZRH", "flyTo": "NRT", "departureDate": "06/03/2026", "returnDate": "13/03/2026", "cabinClass": "M", "curr": "CHF", "sort": "price"}}}},
    {{"tool": "visa_check", "arguments": {{"passport": "Switzerland", "destination": "Japan"}}}},
    {{"tool": "restcountries_country_info", "arguments": {{"query": "Japan"}}}},
    {{"tool": "fcdo_travel_advice", "arguments": {{"country_slug": "japan"}}}},
    {{"tool": "peek_search_experiences", "arguments": {{"location": "Tokyo"}}}}
  ],
  [
    {{"tool": "navifare_format_flight_pricecheck_request", "arguments": {{"user_request": "${{step[0].kiwi_search-flight}}"}}}}
  ],
  [
    {{"tool": "navifare_flight_pricecheck", "arguments": "${{step[1].navifare_format_flight_pricecheck_request}}"}}
  ]
]
```

Route this query:

{query}"""


def _build_tools_text(tools_manifest: list[dict]) -> str:
    """Format tools manifest into a compact list for the prompt."""
    parts = []
    for t in tools_manifest:
        name = t["name"]
        desc = t.get("description", "")
        params = t.get("parameters", {})
        props = params.get("properties", {})
        required = set(params.get("required", []))

        param_parts = []
        for pname, pschema in props.items():
            ptype = pschema.get("type", "any")
            pdesc = pschema.get("description", "")
            req = "*" if pname in required else ""
            entry = f"{pname}{req}: {ptype}"
            if pdesc:
                # Truncate long descriptions to keep prompt lean
                short = pdesc[:120].rstrip()
                if len(pdesc) > 120:
                    short += "..."
                entry += f" — {short}"
            param_parts.append(entry)

        params_str = ", ".join(param_parts) if param_parts else "(none)"
        # Truncate very long tool descriptions
        short_desc = desc[:200].rstrip()
        if len(desc) > 200:
            short_desc += "..."
        parts.append(f"- **{name}**({params_str}): {short_desc}")

    return "\n".join(parts)


async def route_query(query: str, tools_manifest: list[dict]) -> list[dict]:
    """Ask Gemini Flash which tools to call and with what arguments.

    Returns a list (possibly nested) that the caller normalizes into steps.
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
