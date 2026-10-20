"""Google Maps handoffs, without fetching or copying Google place content.

A coordinate pin/search is not evidence of a Google business listing. Preserve
source-published listing identifiers, and label everything else as a search.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit


_PLACE_ID = re.compile(r"[A-Za-z0-9_-]{3,255}\Z")
_GOOGLE_HOSTS = {
    "google.com", "www.google.com", "maps.google.com",
    "google.es", "www.google.es", "maps.google.es",
}

_LINKS_PATH = Path(__file__).resolve().parent / "data" / "google_maps_links.json"
_CURATED_LINKS = json.loads(_LINKS_PATH.read_text(encoding="utf-8"))["links"]


def google_listing_url(value: Any) -> str | None:
    """Accept only Google HTTPS URLs carrying a place identifier/share code."""
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return None
        host = (parsed.hostname or "").lower()
        if host == "maps.app.goo.gl":
            return value if re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path) else None
        if host == "goo.gl":
            return value if re.fullmatch(r"/maps/[A-Za-z0-9_-]+/?", parsed.path) else None
        if host not in _GOOGLE_HOSTS:
            return None
        if not (parsed.path == "/maps" or parsed.path.startswith("/maps/")
                or (host.startswith("maps.google.") and parsed.path in ("", "/"))):
            return None
        if parsed.path.startswith("/maps/dir"):
            return None
        query = parse_qs(parsed.query)
        place_id = (query.get("query_place_id") or [""])[0]
        q = (query.get("q") or [""])[0]
        cid = (query.get("cid") or [""])[0]
        ftid = (query.get("ftid") or [""])[0]
        identified = (
            bool(_PLACE_ID.fullmatch(place_id))
            or (q.startswith("place_id:") and bool(_PLACE_ID.fullmatch(q[9:])))
            or bool(re.fullmatch(r"[1-9][0-9]{0,19}", cid))
            or bool(re.fullmatch(r"0x[0-9a-fA-F]+:0x[0-9a-fA-F]+", ftid))
            or (parsed.path.startswith("/maps/place/") and bool(re.search(r"!1[6]?s[^!/?]+", parsed.path)))
        )
        return value if identified else None
    except ValueError:
        return None


def google_search_query(station: dict[str, Any]) -> str:
    parts = ["EV charging station"]
    for field in ("name", "address", "city"):
        value = station.get(field)
        if isinstance(value, str):
            clean = " ".join(value.split())[:300]
            if clean and clean.casefold() not in {part.casefold() for part in parts}:
                parts.append(clean)
    try:
        lat, lon = float(station["lat"]), float(station["lon"])
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            parts.append(f"near {lat:.6f},{lon:.6f}")
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return " ".join(parts)


def attach_google_maps_navigation(station: dict[str, Any]) -> None:
    """Add listing/search actions while retaining the existing directions action.

    Listing references must belong to this station's record. No nearby-place
    matching, source requests, or inferred place IDs occur here.
    """
    existing = station.get("navigation")
    navigation = dict(existing) if isinstance(existing, dict) else {}
    metadata = station.get("google_maps")
    metadata = metadata if isinstance(metadata, dict) else {}
    place_id = next((value.strip() for value in (
        station.get("google_place_id"), metadata.get("place_id"), navigation.get("google_place_id"),
    ) if isinstance(value, str) and _PLACE_ID.fullmatch(value.strip())), None)
    query = google_search_query(station)
    if place_id:
        listing = "https://www.google.com/maps/search/?" + urlencode({
            "api": "1", "query": query, "query_place_id": place_id,
        })
    else:
        primary = station.get("primary_source") or {}
        curated = next((entry.get("url") for entry in _CURATED_LINKS
                        if station.get("id") == entry.get("station_id")
                        or station.get("source_url") == entry.get("source_url")), None)
        listing = next((safe for candidate in (
            curated,
            navigation.get("google_listing"), station.get("google_maps_url"), metadata.get("url"),
            navigation.get("google"), station.get("source_url"), primary.get("url"),
        ) if (safe := google_listing_url(candidate))), None)
    navigation.pop("google_listing", None)
    if listing:
        navigation["google_listing"] = listing
    navigation["google_search"] = "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": query})
    station["navigation"] = navigation
