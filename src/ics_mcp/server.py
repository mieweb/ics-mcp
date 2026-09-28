"""
ics-mcp — a read-only Model Context Protocol server for iCalendar (.ics) feeds.

Point it at any published .ics URL (for example an Outlook / Exchange Online
"publish calendar" link) via the ICS_URL environment variable, and it exposes
read-only tools to query the calendar. Recurring events are expanded correctly.

Configuration (environment variables):
  ICS_URL          (required)  The https URL (or file:// path) of the .ics feed.
  ICS_CALENDAR_NAME (optional) A friendly name reported in tool output.
  ICS_CACHE_TTL    (optional)  Seconds to cache the fetched feed. Default 300.
  ICS_TIMEZONE     (optional)  IANA tz (e.g. America/New_York) used when a query
                               has no explicit tz. Default: system local time.
  ICS_HTTP_TIMEOUT (optional)  Feed fetch timeout in seconds. Default 30.

This server never writes: there are no tools that modify the calendar.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import recurring_ical_events
from dateutil import parser as dateparser
from icalendar import Calendar
from mcp.server.fastmcp import FastMCP

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback, unreachable given requires-python
    ZoneInfo = None  # type: ignore


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

ICS_URL = os.environ.get("ICS_URL", "").strip()
CALENDAR_NAME = os.environ.get("ICS_CALENDAR_NAME", "Calendar").strip() or "Calendar"
CACHE_TTL = int(os.environ.get("ICS_CACHE_TTL", "300"))
HTTP_TIMEOUT = float(os.environ.get("ICS_HTTP_TIMEOUT", "30"))
_TZ_NAME = os.environ.get("ICS_TIMEZONE", "").strip()


def _local_tz() -> timezone:
    if _TZ_NAME and ZoneInfo is not None:
        try:
            return ZoneInfo(_TZ_NAME)  # type: ignore[return-value]
        except Exception:
            pass
    # Fall back to the system local timezone.
    return datetime.now().astimezone().tzinfo or timezone.utc


LOCAL_TZ = _local_tz()

mcp = FastMCP("ics-mcp")


# --------------------------------------------------------------------------- #
# Feed fetching + caching
# --------------------------------------------------------------------------- #

_cache: dict[str, Any] = {"fetched_at": 0.0, "calendar": None, "raw_len": 0}


def _fetch_raw() -> bytes:
    if not ICS_URL:
        raise RuntimeError(
            "ICS_URL is not set. Configure the environment variable ICS_URL with "
            "your published .ics calendar URL."
        )
    if ICS_URL.startswith("file://"):
        path = ICS_URL[len("file://"):]
        with open(path, "rb") as fh:
            return fh.read()
    headers = {"User-Agent": "ics-mcp/0.1 (+https://github.com/mieweb/ics-mcp)"}
    resp = httpx.get(ICS_URL, headers=headers, timeout=HTTP_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _get_calendar(force: bool = False) -> Calendar:
    now = time.time()
    if (
        not force
        and _cache["calendar"] is not None
        and (now - _cache["fetched_at"]) < CACHE_TTL
    ):
        return _cache["calendar"]
    raw = _fetch_raw()
    cal = Calendar.from_ical(raw)
    _cache.update(fetched_at=now, calendar=cal, raw_len=len(raw))
    return cal


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _as_aware(dt: Any) -> Optional[datetime]:
    """Coerce a date/datetime into a tz-aware datetime in LOCAL_TZ."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(LOCAL_TZ)
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day, tzinfo=LOCAL_TZ)
    return None


def _parse_when(value: str, *, end: bool = False) -> datetime:
    """Parse a user-supplied date/datetime string into an aware datetime."""
    value = (value or "").strip()
    if not value:
        raise ValueError("empty date/time value")
    lowered = value.lower()
    today = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if lowered == "today":
        base = today
    elif lowered == "tomorrow":
        base = today + timedelta(days=1)
    elif lowered == "yesterday":
        base = today - timedelta(days=1)
    else:
        dt = dateparser.parse(value)
        if dt is None:
            raise ValueError(f"could not parse date/time: {value!r}")
        base = _as_aware(dt)  # type: ignore[assignment]
    if end and base.hour == 0 and base.minute == 0 and base.second == 0:
        # A bare date used as an end bound should cover the whole day.
        base = base + timedelta(days=1) - timedelta(seconds=1)
    return base


def _event_to_dict(component: Any) -> dict[str, Any]:
    start = _as_aware(getattr(component.get("dtstart"), "dt", None))
    end = _as_aware(getattr(component.get("dtend"), "dt", None))
    all_day = False
    raw_start = getattr(component.get("dtstart"), "dt", None)
    if isinstance(raw_start, date) and not isinstance(raw_start, datetime):
        all_day = True

    def _s(field: str) -> str:
        val = component.get(field)
        return str(val).strip() if val is not None else ""

    organizer = _s("organizer")
    if organizer.upper().startswith("MAILTO:"):
        organizer = organizer[len("MAILTO:"):]

    attendees = []
    raw_att = component.get("attendee")
    if raw_att:
        items = raw_att if isinstance(raw_att, list) else [raw_att]
        for a in items:
            s = str(a).strip()
            if s.upper().startswith("MAILTO:"):
                s = s[len("MAILTO:"):]
            attendees.append(s)

    return {
        "uid": _s("uid"),
        "summary": _s("summary") or "(no title)",
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "all_day": all_day,
        "location": _s("location"),
        "organizer": organizer,
        "attendees": attendees,
        "status": _s("status"),
        "description": _s("description"),
    }


def _events_between(start: datetime, end: datetime) -> list[dict[str, Any]]:
    cal = _get_calendar()
    occurrences = recurring_ical_events.of(cal).between(start, end)
    events = [_event_to_dict(c) for c in occurrences]
    events.sort(key=lambda e: (e["start"] or "", e["summary"]))
    return events


def _summarize(events: list[dict[str, Any]], *, include_description: bool) -> dict[str, Any]:
    if not include_description:
        for e in events:
            e.pop("description", None)
    return {
        "calendar": CALENDAR_NAME,
        "count": len(events),
        "events": events,
    }


# --------------------------------------------------------------------------- #
# Tools (all read-only)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_events(
    start: str,
    end: str,
    include_description: bool = False,
) -> dict[str, Any]:
    """List calendar events between two dates/times (inclusive).

    Recurring events are expanded into individual occurrences. Accepts natural
    values like "today"/"tomorrow", ISO dates ("2026-01-15"), or full
    timestamps. A bare end date covers the entire day.

    Args:
        start: Range start (e.g. "today", "2026-01-15", "2026-01-15T09:00").
        end: Range end (e.g. "2026-01-22").
        include_description: Include the full event body text. Default False.
    """
    s = _parse_when(start)
    e = _parse_when(end, end=True)
    if e < s:
        raise ValueError("end must be on or after start")
    return _summarize(_events_between(s, e), include_description=include_description)


@mcp.tool()
def list_today(include_description: bool = False) -> dict[str, Any]:
    """List all events occurring today (in the calendar's local timezone)."""
    start = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return _summarize(_events_between(start, end), include_description=include_description)


@mcp.tool()
def list_upcoming(days: int = 7, include_description: bool = False) -> dict[str, Any]:
    """List events from now through the next N days.

    Args:
        days: How many days ahead to include (1-90). Default 7.
        include_description: Include the full event body text. Default False.
    """
    days = max(1, min(int(days), 90))
    start = datetime.now(LOCAL_TZ)
    end = start + timedelta(days=days)
    return _summarize(_events_between(start, end), include_description=include_description)


@mcp.tool()
def search_events(
    query: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    include_description: bool = False,
) -> dict[str, Any]:
    """Search events whose title, location, organizer, or description matches a
    case-insensitive substring.

    Args:
        query: Text to look for.
        start: Optional range start. Defaults to 30 days ago.
        end: Optional range end. Defaults to 180 days ahead.
        include_description: Include full body text in results. Default False.
    """
    q = (query or "").strip().lower()
    if not q:
        raise ValueError("query must not be empty")
    s = _parse_when(start) if start else datetime.now(LOCAL_TZ) - timedelta(days=30)
    e = _parse_when(end, end=True) if end else datetime.now(LOCAL_TZ) + timedelta(days=180)
    matches = []
    for ev in _events_between(s, e):
        haystack = " ".join(
            [
                ev.get("summary", ""),
                ev.get("location", ""),
                ev.get("organizer", ""),
                ev.get("description", ""),
                " ".join(ev.get("attendees", [])),
            ]
        ).lower()
        if q in haystack:
            matches.append(ev)
    return _summarize(matches, include_description=include_description)


@mcp.tool()
def get_calendar_info() -> dict[str, Any]:
    """Report basic information about the configured calendar feed."""
    cal = _get_calendar()
    total = sum(1 for _ in cal.walk("VEVENT"))
    name = str(cal.get("X-WR-CALNAME", CALENDAR_NAME)).strip() or CALENDAR_NAME
    return {
        "calendar": CALENDAR_NAME,
        "feed_name": name,
        "timezone": _TZ_NAME or str(LOCAL_TZ),
        "master_event_count": total,
        "cache_ttl_seconds": CACHE_TTL,
        "note": "Read-only. Recurring events are expanded on query.",
    }


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
