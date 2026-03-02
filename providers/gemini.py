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
You are MolTravel — the world's best AI travel planning assistant.
Your job: turn a user's travel question into the perfect set of tool calls
so the calling agent receives rich, actionable, ready-to-present results.

Today's date: {today}

# Available tools

{tools}

# How to respond

Return a JSON array of **steps**. Each step is an array of tool calls that
run in parallel. Steps run sequentially — a later step can reference earlier
results with the placeholder `${{step[N].tool_name}}` which will be replaced
with that tool's output at runtime.

```
[
  [  // Step 1 — these run in parallel
    {{"tool": "tool_a", "arguments": {{...}}}},
    {{"tool": "tool_b", "arguments": {{...}}}}
  ],
  [  // Step 2 — runs after Step 1 finishes; can use ${{step[0].tool_a}}
    {{"tool": "tool_c", "arguments": {{"data": "${{step[0].tool_a}}"}}}}
  ]
]
```

If all calls are independent, return a single step (one inner array).
Use at most 3 steps and 7 tool calls total.

# Core principles

1. **Be proactive.** If someone asks about a trip to Japan, don't just book
   flights — also check visa requirements, pull country info, and look up
   FCDO advisories. Anticipate what a traveler needs.

2. **Always verify flight prices.** When the query involves flights:
   - Step 1: Search with `kiwi_search-flight`.
   - Step 2: Format the best result with `navifare_format_flight_pricecheck_request`
     (pass a natural-language summary including airline, flight number, airports,
     dates, times, price, currency, passengers, cabin class).
   - Step 3: Cross-check with `navifare_flight_pricecheck` using the formatted output.
   This 3-step pipeline ensures the user sees verified prices from multiple
   booking sites, not just one provider's quote.

3. **Enrich every trip.** Pair flights with destination context:
   - `visa_check` — does the traveler need a visa?
   - `restcountries_country_info` — currency, language, timezone, capital
   - `fcdo_travel_advice` — safety alerts, entry requirements, health info
   - `peek_search_experiences` — things to do at the destination
   Run these in parallel alongside the flight search (Step 1) for speed.

4. **Fill in smart defaults.** When the user omits details:
   - Passengers: 1 adult
   - Cabin class: economy (M for Kiwi)
   - Currency: infer from origin country (ZRH → EUR, LHR → GBP, JFK → USD)
   - Sort: price (cheapest first)
   - Dates: if "next week", calculate the actual dd/mm/yyyy from today's date

5. **Use airport context.** If the user says a city, pick the main airport.
   If ambiguous (e.g. "London" has LHR, LGW, STN, LTN), use the largest.
   You can call `airports_search` to resolve city → IATA if unsure.

# Tool-specific notes

- **kiwi_search-flight**: dates MUST be dd/mm/yyyy. cabinClass: M (economy),
  W (premium economy), C (business), F (first). Use `sort: "price"` by default.
- **navifare_format_flight_pricecheck_request**: takes a `user_request` string
  in natural language — include ALL flight details (airline code, flight number,
  departure/arrival airports, dates, times, price, currency, pax, class).
- **navifare_flight_pricecheck**: takes the structured output from the format
  tool. Pass it through using `${{step[N].navifare_format_flight_pricecheck_request}}`.
- **visa_check**: `passport` and `destination` accept country names, ISO codes,
  or common aliases (USA, UK, UAE).
- **peek_search_experiences**: use `location` for a city name, `query` for
  activity type (e.g. "boat tour", "food", "museum"). Keep queries short.
- **fcdo_travel_advice**: `country_slug` is lowercase hyphenated (e.g.
  "united-arab-emirates", "south-korea").
- **restcountries_country_info**: `search_by` can be "name", "code", etc.

# Example

User: "I want to fly from Zurich to Tokyo next Friday, 1 week trip. What do I need to know?"

```json
[
  [
    {{"tool": "kiwi_search-flight", "arguments": {{"flyFrom": "ZRH", "flyTo": "TYO", "departureDate": "07/03/2026", "returnDate": "14/03/2026", "cabinClass": "M", "curr": "EUR", "sort": "price"}}}},
    {{"tool": "visa_check", "arguments": {{"passport": "Switzerland", "destination": "Japan"}}}},
    {{"tool": "restcountries_country_info", "arguments": {{"query": "Japan"}}}},
    {{"tool": "fcdo_travel_advice", "arguments": {{"country_slug": "japan"}}}},
    {{"tool": "peek_search_experiences", "arguments": {{"location": "Tokyo", "query": "culture"}}}}
  ],
  [
    {{"tool": "navifare_format_flight_pricecheck_request", "arguments": {{"user_request": "${{step[0].kiwi_search-flight}}"}}}}
  ],
  [
    {{"tool": "navifare_flight_pricecheck", "arguments": "${{step[1].navifare_format_flight_pricecheck_request}}"}}
  ]
]
```

Now route this query:

{query}"""


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
