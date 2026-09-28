# ics-mcp

A tiny, **read-only** [Model Context Protocol](https://modelcontextprotocol.io)
(MCP) server that exposes an iCalendar (`.ics`) feed as calendar-query tools.

It is designed for **published calendar URLs** — for example an Outlook /
Exchange Online calendar shared via *Publish calendar → ICS link*, or any other
`webcal`/`https` `.ics` feed. Because it consumes an anonymous published feed,
it needs **no OAuth, no app registration, and no admin consent** — which makes
it a practical way to give an AI assistant read access to a Microsoft 365
calendar when Microsoft Graph admin consent is not available.

Recurring events are expanded into individual occurrences on query.

> **Read-only by design.** There are no tools that create, modify, or delete
> events. The server only ever performs HTTP GETs against the feed URL.

## Tools

| Tool | Description |
| --- | --- |
| `list_events(start, end, include_description=False)` | Events between two dates/times. Accepts `today`/`tomorrow`, ISO dates, or full timestamps. |
| `list_today(include_description=False)` | Everything happening today. |
| `list_upcoming(days=7, include_description=False)` | Events from now through the next N days (1–90). |
| `search_events(query, start=None, end=None, include_description=False)` | Case-insensitive substring match over title, location, organizer, attendees, and description. |
| `get_calendar_info()` | Feed name, timezone, master event count, cache TTL. |

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `ICS_URL` | **yes** | — | `https://…/calendar.ics` feed URL (or a `file://` path). |
| `ICS_CALENDAR_NAME` | no | `Calendar` | Friendly name reported in tool output. |
| `ICS_TIMEZONE` | no | system local | IANA tz (e.g. `America/New_York`) for queries without an explicit zone. |
| `ICS_CACHE_TTL` | no | `300` | Seconds to cache the fetched feed between refreshes. |
| `ICS_HTTP_TIMEOUT` | no | `30` | Feed fetch timeout, in seconds. |

## Usage

### Run with `uvx` (no install)

```bash
ICS_URL="https://outlook.office365.com/owa/calendar/<id>/calendar.ics" \
  uvx --from git+https://github.com/mieweb/ics-mcp ics-mcp
```

### opencode / Claude Desktop MCP config

```jsonc
{
  "mcp": {
    "ics_calendar": {
      "type": "local",
      "command": [
        "uvx",
        "--from",
        "git+https://github.com/mieweb/ics-mcp",
        "ics-mcp"
      ],
      "environment": {
        "ICS_URL": "https://outlook.office365.com/owa/calendar/<id>/calendar.ics",
        "ICS_CALENDAR_NAME": "My Calendar",
        "ICS_TIMEZONE": "America/New_York"
      }
    }
  }
}
```

(Claude Desktop uses the same shape under `mcpServers` with `command` +
`args` split out.)

## Getting a published ICS URL from Outlook / Microsoft 365

1. Open **Outlook on the web** → **Settings** → **Calendar** → **Shared
   calendars**.
2. Under **Publish a calendar**, pick the calendar and a permission level
   (*Can view all details* for full event info).
3. Click **Publish**, then copy the **ICS** link (not the HTML link).
4. Use that URL as `ICS_URL`.

Anyone with the published ICS link can read the calendar, so treat the URL as a
secret and keep it out of source control (pass it via `environment`, as above).

## Development

```bash
git clone https://github.com/mieweb/ics-mcp
cd ics-mcp
ICS_URL="file:///path/to/calendar.ics" uv run ics-mcp
```

## License

MIT © Medical Informatics Engineering
