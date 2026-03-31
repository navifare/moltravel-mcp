from .mcp_client import McpClient
from .restcountries import get_country_info
from .fcdo import get_travel_advice, list_countries as list_fcdo_countries
from . import airports, airlines, visas

# MCP providers — tools discovered automatically at startup
MCP_PROVIDERS = {
    "kiwi": McpClient("https://mcp.kiwi.com/mcp"),
    "navifare": McpClient("https://mcp.navifare.com/mcp"),
    "peek": McpClient("https://mcp.peek.com/mcp"),
    "tourradar": McpClient("https://ai.tourradar.com/mcp/main"),
    "lastminute": McpClient("https://mcp.lastminute.com/mcp"),
    "trivago": McpClient("https://mcp.trivago.com/mcp"),
    "skiplagged": McpClient("https://mcp.skiplagged.com/mcp"),
    "ferryhopper": McpClient("https://mcp.ferryhopper.com/mcp"),
}
