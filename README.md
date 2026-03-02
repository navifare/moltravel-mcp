<div align="center">

# MoltTravel

**One MCP server. Every travel tool.**

Search flights, compare prices, check visas, look up airports,<br>
get travel advisories — through a single endpoint.

[![MCP Protocol](https://img.shields.io/badge/MCP-Protocol-0066FF?style=for-the-badge)](https://modelcontextprotocol.io)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-A020F0?style=for-the-badge)](LICENSE)

<br>

```
  Kiwi.com    Navifare     Peek.com    LastMinute
  (flights)   (prices)   (experiences)  (flights)
      \          |           |          /
       \         |           |         /
        +--------+-----------+--------+
        |                             |
        |    MoltTravel MCP Server    |
        |                             |
        |  Airports  Airlines  Visas  |
        |  Countries  FCDO  Gemini AI |
        |                             |
        +-------------|---------------+
                      |
               MCP over HTTP
                      |
              Any MCP Client
        (Claude, Cursor, your app)
```

</div>

<br>

## Why?

Travel data is scattered across dozens of APIs, each with its own auth, format, and quirks. MoltTravel aggregates them behind a single [Model Context Protocol](https://modelcontextprotocol.io) endpoint:

- **21+ tools** from 4 upstream MCP providers + 6 built-in datasets
- **Zero config** for static data — airports, airlines, and visas load lazily with no API keys
- **Schema-transparent proxy** — clients see the real upstream JSON Schema; upstream servers validate their own args
- **One line to connect** from Claude Desktop, Claude Code, Cursor, or any MCP client

<br>

## Quick Start

```bash
git clone https://github.com/navifare/moltravel-mcp.git
cd moltravel-mcp
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python molttravel_server.py
```

Server starts at **`http://localhost:8000/mcp`**. That's it.

<br>

## Connect Your Client

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "molttravel": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```
</details>

<details>
<summary><strong>Claude Code</strong></summary>

Add to `.claude/settings.json`:
```json
{
  "mcpServers": {
    "molttravel": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```
</details>

<details>
<summary><strong>Python (programmatic)</strong></summary>

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8000/mcp") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("airports_lookup", {"code": "ZRH"})
```
</details>

<br>

## Tools

### Flights & Pricing

| Tool | Provider | What it does |
|:-----|:---------|:-------------|
| `kiwi_search-flight` | Kiwi.com | Search flights by route, date, passengers, cabin class |
| `navifare_format_flight_pricecheck_request` | Navifare | Parse flight details from natural language into structured data |
| `navifare_flight_pricecheck` | Navifare | Compare a flight's price across multiple booking sites |

### Experiences & Activities

| Tool | Provider | What it does |
|:-----|:---------|:-------------|
| `peek_search_experiences` | Peek.com | Search 300K+ verified activities worldwide |
| `peek_experience_details` | Peek.com | Full details, reviews, and photos for an experience |
| `peek_experience_availability` | Peek.com | Check availability and pricing for specific dates |
| `peek_search_regions` | Peek.com | Find region IDs by name |
| `peek_list_tags` | Peek.com | Browse activity categories and tags |
| `peek_render_activity_tiles` | Peek.com | Render embeddable activity widgets |

### Reference Data <sub>(built-in, no API keys needed)</sub>

| Tool | Dataset | What it does |
|:-----|:--------|:-------------|
| `airports_lookup` | OurAirports | Look up by IATA or ICAO code |
| `airports_search` | OurAirports | Search by name, filter by country or type |
| `airports_near` | OurAirports | Find airports within a radius of any coordinates |
| `airlines_lookup` | OpenFlights | Look up by IATA or ICAO code |
| `airlines_search` | OpenFlights | Search by name, filter by country or active status |
| `visa_check` | Passport Index | Check visa requirement between two countries |
| `visa_summary` | Passport Index | Full visa-free/VOA/e-visa breakdown for a passport |
| `restcountries_country_info` | REST Countries | Capital, currencies, languages, timezones, population |
| `fcdo_travel_advice` | UK FCDO | Safety advisories, entry requirements, health warnings |
| `fcdo_list_countries` | UK FCDO | List all countries with travel advisories |
| `data_status` | — | Check which datasets are loaded and record counts |

### Natural Language <sub>(optional)</sub>

| Tool | What it does |
|:-----|:-------------|
| `travel_agent` | Ask any travel question — Gemini routes to the right tools and returns a combined answer |

> Requires `GEMINI_API_KEY`. Example: *"Cheapest flights from Zurich to Rome next week, and do I need a visa?"*

<br>

## How It Works

### 1. Tool Discovery

On startup, MoltTravel connects to each upstream MCP server, calls `tools/list`, and registers every discovered tool with a `{provider}_{tool_name}` prefix:

```
kiwi       → kiwi_search-flight, kiwi_feedback-to-devs
navifare   → navifare_flight_pricecheck, navifare_format_flight_pricecheck_request
peek       → peek_search_experiences, peek_experience_details, ...
lastminute → (discovered at runtime)
```

Native tools (airports, airlines, visas, countries, FCDO) are registered directly.

### 2. Schema-Transparent Proxy

Clients see the **original upstream JSON Schema** for each tool — enums, nested objects, `$ref`, everything. Internally, MoltTravel uses a permissive Pydantic model (`Any` for all fields) so arguments pass through without lossy validation. The upstream MCP server validates its own args.

```python
# Client sees the real schema
parameters = input_schema          # original from upstream

# Server doesn't re-validate types — just passes through
fields[prop_name] = (Any, Field(default=None))
```

### 3. Lazy Data Loading

Static datasets (airports, airlines, visas) download on first use behind async locks. No startup penalty, no wasted bandwidth if you only use flight tools.

<br>

## Configuration

| Variable | Default | Description |
|:---------|:--------|:------------|
| `PORT` | `8000` | Server port |
| `GEMINI_API_KEY` | — | Enables the `travel_agent` natural-language routing tool |

<br>

## Deployment

<details>
<summary><strong>Docker</strong></summary>

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "molttravel_server.py"]
```

```bash
docker build -t molttravel .
docker run -p 8000:8000 molttravel
```
</details>

<details>
<summary><strong>Render / Railway / Fly.io</strong></summary>

The server reads `PORT` from the environment and binds to `0.0.0.0` — it works out of the box on any container platform. Just point the start command at `python molttravel_server.py`.
</details>

<br>

## Project Structure

```
moltravel-mcp/
├── molttravel_server.py        # Server core — proxy logic, native tools, routing
├── requirements.txt            # mcp[cli], httpx
├── test_search.py              # Integration test client
└── providers/
    ├── __init__.py             # MCP_PROVIDERS registry + exports
    ├── mcp_client.py           # Generic HTTP client for upstream MCP servers
    ├── data_loader.py          # CSV downloader + haversine distance
    ├── airports.py             # 45K airports from OurAirports
    ├── airlines.py             # 7K airlines from OpenFlights
    ├── visas.py                # Visa requirements from Passport Index
    ├── restcountries.py        # REST Countries API
    ├── fcdo.py                 # UK FCDO travel advisories
    └── gemini.py               # Gemini Flash tool router
```

<br>

## Extending

### Add an MCP provider

Add one line to `providers/__init__.py` and restart:

```python
MCP_PROVIDERS = {
    "kiwi": McpClient("https://mcp.kiwi.com/mcp"),
    "navifare": McpClient("https://mcp.navifare.com/mcp"),
    "peek": McpClient("https://mcp.peek.com/mcp"),
    "your_provider": McpClient("https://mcp.example.com/mcp"),  # new
}
```

Tools are discovered and registered automatically as `your_provider_{tool_name}`.

### Add a native tool

```python
@server.tool(name="my_tool")
async def my_tool(query: str) -> str:
    """Description shown to MCP clients."""
    return "result"
```

<br>

## Data Sources & Licenses

| Dataset | Source | License |
|:--------|:-------|:--------|
| Airports | [OurAirports](https://ourairports.com/data/) | Public Domain |
| Airlines | [OpenFlights](https://openflights.org/data.php) | ODbL 1.0 |
| Visa Requirements | [Passport Index](https://github.com/ilyankou/passport-index-dataset) | MIT |
| Country Info | [REST Countries](https://restcountries.com) | MPL 2.0 |
| Travel Advisories | [UK FCDO](https://www.gov.uk/foreign-travel-advice) | OGL v3.0 |

<br>

## Contributing

1. Fork the repo
2. Create a branch (`git checkout -b feature/my-feature`)
3. Make changes and test (`python molttravel_server.py`)
4. Open a pull request

<br>

## License

[MIT](LICENSE)
