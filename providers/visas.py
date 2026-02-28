"""Visa requirements provider — Passport Index (MIT).

Lazy-loads passport-index-tidy.csv on first call.
Provides visa requirement check and summary by passport country.
"""

import asyncio
import logging

from .data_loader import fetch_csv

log = logging.getLogger("molttravel.visas")

_URL = "https://raw.githubusercontent.com/ilyankou/passport-index-dataset/master/passport-index-tidy.csv"
_COUNTRIES_URL = "https://davidmegginson.github.io/ourairports-data/countries.csv"

# Common aliases → canonical names used in the dataset
_ALIASES: dict[str, str] = {
    "usa": "United States",
    "us": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "uae": "United Arab Emirates",
    "south korea": "South Korea",
    "korea": "South Korea",
    "north korea": "North Korea",
    "czech republic": "Czech Republic",
    "czechia": "Czech Republic",
    "russia": "Russia",
    "china": "China",
    "hong kong": "Hong Kong",
    "taiwan": "Taiwan",
    "macau": "Macao",
    "macao": "Macao",
    "ivory coast": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire",
    "myanmar": "Myanmar",
    "burma": "Myanmar",
    "eswatini": "Eswatini",
    "swaziland": "Eswatini",
}

# ISO 3166-1 alpha-2 → country name (populated at load time from OurAirports)
_iso_to_name: dict[str, str] = {}

_data: dict | None = None
_lock = asyncio.Lock()


def _resolve_country(name: str) -> str | None:
    """Resolve a country input to the canonical name used in the dataset.

    Accepts full names, common aliases (USA, UK, UAE), and ISO 3166-1
    alpha-2 codes (CH, JP, US).
    """
    if not name:
        return None
    name_stripped = name.strip()
    name_lower = name_stripped.lower()

    # 1. Check alias map (includes common shorthands like "usa", "uk")
    if name_lower in _ALIASES:
        return _ALIASES[name_lower]

    # 2. ISO 3166-1 alpha-2 code (2 uppercase letters)
    name_upper = name_stripped.upper()
    if len(name_upper) == 2 and name_upper.isalpha() and name_upper in _iso_to_name:
        resolved = _iso_to_name[name_upper]
        # Try to match the resolved name against the visa dataset's country list
        if _data and _data["countries_lower"]:
            resolved_lower = resolved.lower()
            if resolved_lower in _data["countries_lower"]:
                return _data["countries_lower"][resolved_lower]
        return resolved

    # 3. Direct match (case-insensitive) against known countries
    if _data and _data["countries_lower"]:
        if name_lower in _data["countries_lower"]:
            return _data["countries_lower"][name_lower]

    # 4. Substring match (only if unambiguous)
    if _data and _data["countries_lower"]:
        matches = [
            canonical
            for lower, canonical in _data["countries_lower"].items()
            if name_lower in lower
        ]
        if len(matches) == 1:
            return matches[0]

    return name_stripped  # Return as-is and let lookup fail gracefully


async def _ensure_loaded():
    global _data, _iso_to_name
    if _data is not None:
        return
    async with _lock:
        if _data is not None:
            return
        log.info("Loading visa data …")
        try:
            rows = await fetch_csv(_URL)
        except Exception as e:
            log.warning(f"Failed to load visa data: {e}")
            _data = {"by_passport": {}, "countries_lower": {}, "count": 0}
            return

        # Load ISO code → country name mapping from OurAirports
        try:
            countries_rows = await fetch_csv(_COUNTRIES_URL)
            _iso_to_name = {
                r["code"].strip().upper(): r["name"].strip()
                for r in countries_rows
                if r.get("code") and r.get("name")
            }
            log.info(f"Loaded {len(_iso_to_name)} ISO country codes")
        except Exception as e:
            log.warning(f"Failed to load ISO country codes: {e}")

        by_passport: dict[str, list[dict]] = {}
        all_countries: set[str] = set()

        for row in rows:
            passport = (row.get("Passport") or "").strip()
            destination = (row.get("Destination") or "").strip()
            requirement = (row.get("Requirement") or "").strip()

            if not passport or not destination:
                continue

            all_countries.add(passport)
            all_countries.add(destination)

            entry = {
                "destination": destination,
                "requirement": requirement,
            }
            by_passport.setdefault(passport, []).append(entry)

        # Build case-insensitive lookup: lowercase → canonical
        countries_lower = {c.lower(): c for c in sorted(all_countries)}

        _data = {
            "by_passport": by_passport,
            "countries_lower": countries_lower,
            "count": len(rows),
        }
        log.info(f"Loaded {len(rows)} visa entries for {len(by_passport)} passport countries")


def _describe_requirement(req: str) -> str:
    """Human-readable description of a visa requirement code."""
    req_lower = req.lower().strip()
    if req_lower == "visa free":
        return "Visa-free entry"
    if req_lower.isdigit():
        return f"Visa-free for {req} days"
    if "visa on arrival" in req_lower or req_lower == "voa":
        return "Visa on arrival"
    if "e-visa" in req_lower or req_lower == "e-visa":
        return "e-Visa available"
    if "visa required" in req_lower:
        return "Visa required"
    if req == "-1" or req_lower == "no admission":
        return "No admission"
    return req


async def check_visa(passport: str, destination: str) -> str:
    """Check visa requirement for a passport country visiting a destination."""
    await _ensure_loaded()
    if not _data["by_passport"]:
        return "Visa data not available. Please try again later."

    p = _resolve_country(passport)
    d = _resolve_country(destination)

    entries = _data["by_passport"].get(p)
    if not entries:
        return f"No visa data found for passport country '{passport}' (resolved to '{p}')."

    for entry in entries:
        if entry["destination"].lower() == d.lower():
            req = entry["requirement"]
            desc = _describe_requirement(req)
            return (
                f"Passport: {p}\n"
                f"Destination: {entry['destination']}\n"
                f"Requirement: {req}\n"
                f"Summary: {p} passport holders — {desc} to {entry['destination']}."
            )

    return f"No visa data found for {p} → {d}."


async def visa_summary(passport: str) -> str:
    """Get a summary of visa requirements for a passport country."""
    await _ensure_loaded()
    if not _data["by_passport"]:
        return "Visa data not available. Please try again later."

    p = _resolve_country(passport)
    entries = _data["by_passport"].get(p)
    if not entries:
        return f"No visa data found for passport country '{passport}' (resolved to '{p}')."

    categories: dict[str, int] = {}
    for entry in entries:
        req = entry["requirement"].strip()
        # Normalize: numeric values → "visa free (X days)"
        if req.isdigit():
            cat = "Visa free (limited days)"
        elif req.lower() == "visa free":
            cat = "Visa free"
        elif "visa on arrival" in req.lower() or req.lower() == "voa":
            cat = "Visa on arrival"
        elif "e-visa" in req.lower():
            cat = "e-Visa"
        elif "visa required" in req.lower():
            cat = "Visa required"
        elif req == "-1" or req.lower() == "no admission":
            cat = "No admission"
        else:
            cat = req
        categories[cat] = categories.get(cat, 0) + 1

    lines = [f"Visa summary for {p} passport holders ({len(entries)} destinations):"]
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        lines.append(f"  {cat}: {count}")
    return "\n".join(lines)


async def get_visa_count() -> int:
    """Return loaded visa entry count (0 if not loaded)."""
    await _ensure_loaded()
    return _data["count"] if _data else 0
