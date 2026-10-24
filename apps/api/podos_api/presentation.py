"""Validated, bounded result presentations from external agents. No executable UI or HTML."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit


def public_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme != "https" or not host or url.username or url.password or host in {"localhost", "localhost.localdomain"}:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or host.endswith((".local", ".internal", ".localhost")):
                return None
        return value
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if not isinstance(value, bool) and math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def _text(value: Any, limit: int = 250) -> str:
    return value[:limit].strip() if isinstance(value, str) else ""


def _gbp(value: Any) -> float | None:
    # Sportscanner returns both numeric and formatted GBP prices. Accept only an
    # unambiguous amount; never infer a price from prose such as 'from' or 'per hour'.
    if isinstance(value, str) and re.fullmatch(r"£\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?", value.strip()):
        value = value.strip().removeprefix("£").strip().replace(",", "")
    return _number(value)


def court_result(data: Any) -> dict | None:
    """Translate the court_availability A2A artifact, preserving only source-provided values."""
    if not isinstance(data, list):
        return None
    venues = []
    for raw in data[:20]:
        if not isinstance(raw, dict) or not _text(raw.get("venue")):
            continue
        slots = []
        for slot in (raw.get("availability") if isinstance(raw.get("availability"), list) else [])[:40]:
            if not isinstance(slot, dict) or slot.get("available") is False:
                continue
            start, end = _text(slot.get("startingTime"), 40), _text(slot.get("endingTime"), 40)
            if not start or not end:
                continue
            slots.append({"start": start, "end": end, "price_gbp": _gbp(slot.get("price")),
                          "booking_url": public_url(slot.get("bookingUrl"))})
        if not slots:
            continue
        venues.append({
            "name": _text(raw.get("venue")), "operator": _text(raw.get("organization")),
            "address": _text(raw.get("address"), 500), "date": _text(raw.get("date"), 80),
            "distance_miles": _number(raw.get("distance")), "last_refreshed": _text(raw.get("last_refreshed"), 80),
            "price_from_gbp": _gbp(raw.get("price")),
            "stale": raw.get("healthcheck") == "deprecated", "slots": slots,
        })
    # A malformed nonempty response must not turn into a misleading 'no availability' card.
    if data and not venues:
        return None
    result = {"kind": "court_availability", "version": 1, "venues": venues,
              "truncated": len(data) > 20, "source": "Connected court finder"}
    result["id"] = "courts-" + hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:16]
    return result
