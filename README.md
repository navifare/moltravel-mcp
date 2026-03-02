<p align="center">
  <img src="https://img.shields.io/badge/protocol-MCP-blue?style=flat-square" alt="MCP Protocol">
  <img src="https://img.shields.io/badge/python-3.12+-yellow?style=flat-square&logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/transport-Streamable_HTTP-green?style=flat-square" alt="Streamable HTTP">
  <img src="https://img.shields.io/badge/license-MIT-purple?style=flat-square" alt="MIT License">
</p>

# MoltTravel MCP Server

A **Model Context Protocol (MCP) server** that aggregates travel tools from multiple providers into a single endpoint. Search flights, compare prices, check visa requirements, look up airports and airlines, get travel advisories — all through one unified MCP interface.

```
┌─────────────────────────────────────────────────────────────┐
│                    MoltTravel MCP Server                     │
│                                                             │
│  ┌─────────┐ ┌──────────┐ ┌──────┐ ┌───────────────────┐   │
│  │  Kiwi   │ │ Navifare │ │ Peek │ │   LastMinute.com  │   │
│  │ Flights │ │  Prices  │ │ Exp. │ │      Flights      │   │
│  └────┬────┘ └────┬─────┘ └──┬───┘ └────────┬──────────┘   │
│       │           │          │               │              │
│       └───────────┴──────┬───┴───────────────┘              │
│                          │                                  │
│  ┌───────────────────────┼──────────────────────────────┐   │
│  │              Native Tools (no upstream)               │   │
│  │  Airports · Airlines · Visas · Countries · FCDO      │   │
│  └───────────────────────┼──────────────────────────────┘   │
│                          │                                  │
│                   ┌──────┴──────┐                            │
│                   │ travel_agent│  (optional, Gemini-routed) │
│                   └─────────────┘                            │
└─────────────────────────────────────────────────────────────┘
                           │
                    MCP over HTTP
                           │
                ┌──────────┴──────────┐
                │  Any MCP Client     │
                │  (Claude, Cursor,   │
                │   custom agents)    │
                └─────────────────────┘
```

## Why MoltTravel?

Most travel APIs are fragmented — flights from one provider, hotels from another, visa data from a third. MoltTravel solves this by:

- **Proxying upstream MCP servers** (Kiwi, Navifare, Peek, LastMinute) and exposing their tools under a unified namespace
- **Bundling static datasets** (airports, airlines, visa requirements) that load lazily and need zero API keys
- **Providing a single endpoint** that any MCP-compatible client can connect to
- **Optionally routing natural-language queries** to the right tools via Google Gemini

## Quick Start

```bash
# Clone and install
git clone https://github.com/user/molttravel.git
cd molttravel/server
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run
python molttravel_server.py
```

The server starts on `http://localhost:8000/mcp`. Connect any MCP client to this endpoint.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PORT` | No | `8000` | Server port |
| `GEMINI_API_KEY` | No | — | Enables the `travel_agent` natural-language tool |

## Tools

MoltTravel exposes **21+ tools** across 6 categories. Proxied tools are prefixed with their provider name (e.g. `kiwi_`, `navifare_`, `peek_`).

### Flights

| Tool | Source | Description |
|------|--------|-------------|
| `kiwi_search-flight` | Kiwi.com | Search flights by route, date, passengers, cabin class |
| `navifare_format_flight_pricecheck_request` | Navifare | Parse flight details from natural language into structured format |
| `navifare_flight_pricecheck` | Navifare | Compare a flight's price across multiple booking sites |

### Experiences & Activities

| Tool | Source | Description |
|------|--------|-------------|
| `peek_search_experiences` | Peek.com | Search 300K+ verified activities worldwide |
| `peek_experience_details` | Peek.com | Get full details for an experience |
| `peek_experience_availability` | Peek.com | Check availability and pricing |
| `peek_search_regions` | Peek.com | Find region IDs by name |
| `peek_list_tags` | Peek.com | List activity categories and tags |
| `peek_render_activity_tiles` | Peek.com | Render activity widgets |

### Airports

| Tool | Description |
|------|-------------|
| `airports_lookup` | Look up by IATA (3-char) or ICAO (4-char) code |
| `airports_search` | Search by name, filter by country or type |
| `airports_near` | Find airports within a radius of coordinates |

Data: 45,000+ airports from [OurAirports](https://ourairports.com) (Public Domain), including runways, coordinates, elevation, and municipality.

### Airlines

| Tool | Description |
|------|-------------|
| `airlines_lookup` | Look up by IATA (2-char) or ICAO (3-char) code |
| `airlines_search` | Search by name, filter by country or active status |

Data: 7,000+ airlines from [OpenFlights](https://openflights.org) (ODbL 1.0).

### Visa Requirements

| Tool | Description |
|------|-------------|
| `visa_check` | Check visa requirement between two countries |
| `visa_summary` | Full visa overview for a passport (all destinations) |

Data: [Passport Index](https://github.com/ilyankou/passport-index-dataset) (MIT License). Supports country names, common aliases (USA, UK, UAE), and ISO codes.

### Country Info & Travel Advisories

| Tool | Description |
|------|-------------|
| `restcountries_country_info` | Capital, currencies, languages, timezones, population, borders |
| `fcdo_travel_advice` | UK FCDO safety advisories, entry requirements, health warnings |
| `fcdo_list_countries` | List all countries with FCDO advisories |
| `data_status` | Check which static datasets are loaded |

### Natural Language (Optional)

| Tool | Description |
|------|-------------|
| `travel_agent` | Ask any travel question — Gemini routes to the right tools automatically |

Requires `GEMINI_API_KEY`. Example: *"Cheapest flights from Zurich to Rome next week, and do I need a visa?"*

## Architecture

### MCP Proxy Pattern

MoltTravel discovers tools from upstream MCP servers at startup and re-exposes them. The proxy is **schema-transparent**: clients see the original upstream JSON Schema for each tool, while a permissive Pydantic model ensures arguments pass through without lossy validation. Upstream servers handle their own validation.

```python
# Upstream schema is preserved for clients
parameters=input_schema  # original JSON Schema from upstream

# Permissive model — all fields typed as Any
# Upstream MCP validates; we just pass through
fields[prop_name] = (Any, pydantic.Field(default=None))
```

### Tool Discovery

```
Startup
  ├── Connect to each MCP provider (kiwi, navifare, peek, lastminute)
  ├── Call tools/list on each
  ├── Register discovered tools as {provider}_{tool_name}
  └── Register native tools (airports, airlines, visas, countries, FCDO)
```

### Data Loading

Static datasets (airports, airlines, visas) are **lazy-loaded** on first use with async locks — no startup penalty, no redundant downloads.

## Usage Examples

### Connect with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "molttravel": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Connect with Claude Code

Add to your `.claude/settings.json`:

```json
{
  "mcpServers": {
    "molttravel": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Connect Programmatically

Any MCP client library works. The server speaks JSON-RPC 2.0 over Streamable HTTP:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8000/mcp") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("airports_lookup", {"code": "ZRH"})
```

## Deployment

### Docker

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

### Render / Railway / Fly.io

The server reads `PORT` from the environment and binds to `0.0.0.0`, so it works out of the box on any container platform.

## Project Structure

```
server/
├── molttravel_server.py       # Main server — tool registration, proxy logic, native tools
├── requirements.txt           # mcp[cli], httpx
├── test_search.py             # HTTP client test script
└── providers/
    ├── __init__.py            # MCP_PROVIDERS registry, exports
    ├── mcp_client.py          # Generic upstream MCP client (HTTP + event-stream)
    ├── data_loader.py         # CSV fetcher + haversine distance
    ├── airports.py            # OurAirports — 45K airports, runways, regions
    ├── airlines.py            # OpenFlights — 7K airlines
    ├── visas.py               # Passport Index — visa requirements
    ├── restcountries.py       # REST Countries API
    ├── fcdo.py                # UK FCDO travel advisories (GOV.UK API)
    └── gemini.py              # Google Gemini Flash — natural-language tool router
```

## Adding a New MCP Provider

1. Add the upstream URL to `providers/__init__.py`:

```python
MCP_PROVIDERS = {
    "kiwi": McpClient("https://mcp.kiwi.com/mcp"),
    "navifare": McpClient("https://mcp.navifare.com/mcp"),
    "your_provider": McpClient("https://mcp.example.com/mcp"),  # add here
}
```

2. Restart the server. Tools are discovered automatically and registered as `your_provider_{tool_name}`.

## Adding a Native Tool

Register directly on the FastMCP server instance:

```python
@server.tool(name="my_tool")
async def my_tool(query: str) -> str:
    """Description shown to MCP clients."""
    return "result"
```

## Data Sources & Licenses

| Dataset | Source | License |
|---------|--------|---------|
| Airports | [OurAirports](https://ourairports.com/data/) | Public Domain |
| Airlines | [OpenFlights](https://openflights.org/data.php) | ODbL 1.0 |
| Visa Requirements | [Passport Index Dataset](https://github.com/ilyankou/passport-index-dataset) | MIT |
| Country Info | [REST Countries](https://restcountries.com) | Mozilla Public License 2.0 |
| Travel Advisories | [UK FCDO / GOV.UK](https://www.gov.uk/foreign-travel-advice) | OGL v3.0 |

## Contributing

Contributions are welcome! Whether it's adding new providers, improving existing tools, or fixing bugs:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Test locally (`python molttravel_server.py` + call some tools)
5. Submit a pull request

## License

MIT
