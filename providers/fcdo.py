"""UK FCDO Travel Advisories — direct API calls to GOV.UK Content API.

Contains public sector information licensed under the Open Government Licence v3.0.
"""

import json
import ssl
import asyncio
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote

API_BASE = "https://www.gov.uk/api/content"


def _get(path: str, retries: int = 2) -> dict:
    """GET from GOV.UK Content API and return parsed JSON."""
    url = f"{API_BASE}{path}"
    ctx = ssl.create_default_context()

    import time
    for attempt in range(retries):
        try:
            req = Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urlopen(req, timeout=15, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 404:
                return {"error": "not_found", "message": f"No travel advice found for: {path}"}
            if attempt < retries - 1 and e.code in (502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return {"error": "request_failed"}


def _strip_html(html: str) -> str:
    """Rough HTML-to-text conversion."""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<li>", "- ", text)
    text = re.sub(r"<p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_country_advice(data: dict) -> str:
    """Format a single country's travel advice into readable text."""
    title = data.get("title", "Unknown")
    updated = data.get("updated_at", "")
    lines = [f"## {title}"]
    if updated:
        lines.append(f"Last updated: {updated[:10]}")
    lines.append("")

    # Alert status
    details = data.get("details", {})
    alert_status = details.get("alert_status", [])
    if alert_status:
        lines.append(f"Alert status: {', '.join(alert_status)}")
        lines.append("")

    # Image (risk map)
    image = details.get("image", {})
    if image and image.get("url"):
        lines.append(f"Risk map: https://www.gov.uk{image['url']}")
        lines.append("")

    # Parts (Summary, Safety, Entry requirements, Health, etc.)
    parts = details.get("parts", [])
    for part in parts:
        part_title = part.get("title", "")
        body_html = part.get("body", "")
        if part_title:
            lines.append(f"### {part_title}")
        if body_html:
            lines.append(_strip_html(body_html))
        lines.append("")

    lines.append("---")
    lines.append("Contains public sector information licensed under the Open Government Licence v3.0.")

    return "\n".join(lines)


def _format_country_list(data: dict) -> str:
    """Format the list of all countries with travel advice."""
    links = data.get("links", {})
    children = links.get("children", [])
    if not children:
        return "No countries found."

    lines = [f"## FCDO Travel Advice — {len(children)} countries\n"]
    for child in sorted(children, key=lambda c: c.get("title", "")):
        title = child.get("title", "")
        slug = child.get("base_path", "").replace("/foreign-travel-advice/", "")
        updated = child.get("updated_at", child.get("public_updated_at", ""))[:10]
        lines.append(f"- **{title}** (`{slug}`) — updated {updated}")

    lines.append("\n---")
    lines.append("Contains public sector information licensed under the Open Government Licence v3.0.")
    return "\n".join(lines)


def _to_slug(country: str) -> str:
    """Convert country name to GOV.UK slug format."""
    slug = country.strip().lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


# -- Sync functions for each tool --

def _get_travel_advice_sync(country: str) -> dict:
    """Get travel advice for a specific country."""
    slug = _to_slug(country)
    result = _get(f"/foreign-travel-advice/{slug}")

    if isinstance(result, dict) and "error" in result:
        return result

    text = _format_country_advice(result)
    return {"result": {"content": [{"type": "text", "text": text}]}}


def _list_countries_sync() -> dict:
    """List all countries with FCDO travel advice."""
    result = _get("/foreign-travel-advice")

    if isinstance(result, dict) and "error" in result:
        return result

    text = _format_country_list(result)
    return {"result": {"content": [{"type": "text", "text": text}]}}


# -- Async wrappers --

async def get_travel_advice(country: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_travel_advice_sync, country)


async def list_countries() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _list_countries_sync)
