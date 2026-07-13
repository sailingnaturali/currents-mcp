# currents-mcp

MCP server exposing Pacific Northwest tidal-gate slack windows to agents. Tidal-current predictions are read from the [`signalk-currents`](https://github.com/sailingnaturali) plugin's `/currents` resource (the plugin owns CHS/NOAA fetching and caching); this server maps named gates and destination passages onto those predictions and reports next slack windows, transit windows, and recommended departure times. Tide heights come from the same SignalK server's [`signalk-tides`](https://github.com/openwatersio/signalk-tides) plugin (offline Neaps predictions) — this server makes no direct internet fetches.


## Tools

### `plan_passage(destination, depart_time?, from_lat?, from_lon?)`

Maps a destination string to its ordered tidal gates, returns next slack windows for each gate, and a recommended departure time for the first gate.

- Unknown destination: returns `{"unmatched": true, "suggestions_display": ...}`
- Open-water destination (no gates): returns `{"destination", "gates": [], "summary_display": "No tidal gates on the direct route - open-water passage; wind and weather are the constraint."}`

Example response:

```json
{
  "destination": "Cordero Channel",
  "gates": [
    {
      "name": "Gillard Passage",
      "slack_windows": [
        {"display": "Sun 21:14 PDT (slack, ebb→flood — flood sets south-southeast)", "utc": "2026-05-25T04:14:00Z"}
      ],
      "transit_window_minutes": 20,
      "sets_display": "Flood sets south-southeast; ebb sets north-northwest.",
      "flood_dir_true": 160,
      "ebb_dir_true": 340,
      "recommended_depart_display": "Depart by 18:45 PDT to hit Gillard Passage at slack."
    },
    {
      "name": "Dent Rapids",
      "slack_windows": [
        {"display": "Mon 02:10 PDT (slack, ebb→flood)", "utc": "2026-05-25T09:10:00Z"}
      ],
      "transit_window_minutes": 20,
      "recommended_depart_display": null,
      "note_display": "Slack windows shown for planning; the recommended departure covers the first gate only."
    }
  ],
  "summary_display": "2 tidal gate(s). Depart by 18:45 PDT to hit Gillard Passage at slack."
}
```

### `get_gate_current(name, date?)`

Returns the next 3 slack windows for a single named gate: `{"name", "slack_windows", "transit_window_minutes", "sets_display", "flood_dir_true", "ebb_dir_true"}`. Unknown gate name returns `{"unmatched": true, "suggestions_display": ...}`.

Flood/ebb set comes from the signalk-currents payload (>= 0.3.0: directions;
>= 0.5.0: provenance). `sets_display` speaks the trust level: assumed directions
are qualified — `"…; ebb sets northwest (estimated)."` — and when no direction is
known at all it says `"Flood and ebb set directions are not available for this
station."` rather than omitting it. The `*_dir_true` fields are `null` when unknown,
and slack windows fall back to the plain `(slack, ebb→flood)` form.

### `list_gates()`

Returns all covered destinations and the gates they route through: `{"coverage": [{"destination", "aliases", "gates"}, ...], "display": "..."}`.

### `get_tide_heights(lat, lon, date?)`

Returns high/low tide height events from the nearest tide station to the
position. Use for anchor planning ("when is low tide here?") and any question
about tidal range.

- `lat`, `lon`: vessel or target position in decimal degrees
- `date`: optional ISO date string (defaults to today)

Example response:

```json
{
  "station_name": "Montague Harbour BC",
  "distance_km": 1,
  "events": [
    {"display": "Low 06:14 PDT — 0.9 m", "type": "low", "height_m": 0.9, "utc": "2026-05-26T13:14:00Z"},
    {"display": "High 12:38 PDT — 3.8 m", "type": "high", "height_m": 3.8, "utc": "2026-05-26T19:38:00Z"},
    {"display": "Low 18:47 PDT — 1.0 m", "type": "low", "height_m": 1.0, "utc": "2026-05-27T01:47:00Z"}
  ],
  "summary_display": "Nearest tide station: Montague Harbour BC, 1 km from you. Next low is 06:14 PDT — 0.9 m."
}
```

Data source: the `signalk-tides` plugin (≥ 2.0.0-beta.1, plugin id `tides`)
serving offline Neaps harmonic predictions at
`/signalk/v2/api/tides/extremes` on `SIGNALK_URL`. A 36-hour window is
queried so the local-day tail isn't dropped when called late in a PDT day;
the next ~4 events at/after the query time are returned.

> **Datum caveat.** Heights are relative to **LAT**; official CHS predictions
> (and Canadian charted depths) are relative to **chart datum**, which sits
> *above* LAT by a fixed, per-station amount — 0.43 m at Sidney BC, 0.08 m at
> Point Atkinson, 0.09 m at Halifax (CHS station metadata, `LAT` offset). So
> these heights read systematically higher than tide tables by exactly that
> offset — an optimistic bias for charted-depth math. After datum correction
> the residual prediction difference is ~1 dm (timing within minutes; see
> [openwatersio/neaps#223](https://github.com/openwatersio/neaps/issues/223)).
> Pad under-keel margins accordingly.

## Coverage

**Destinations** (resolvable via `plan_passage`):

- Gated: Nanaimo, Discovery Islands, Johnstone Strait via Discovery Passage, Cordero Channel, Friday Harbor.
- Open water (no gates): Gulf Islands, Desolation Sound, Cortes Island, Campbell River.

**Named tidal gates** (resolvable via `get_gate_current`):

- BC: Dodd Narrows, Active Pass, Porlier Pass, Gabriola Passage, Seymour Narrows, Beazley Passage (Surge Narrows), Hole in the Wall, Gillard Passage, Dent Rapids.
- US: Boundary Pass (San Juans / Friday Harbor).

Active Pass, Porlier Pass, Gabriola Passage, and Hole in the Wall are addressable by name but are not yet wired into any Victoria-origin passage.

The gate-to-station mapping lives in `passages.py`; the `signalk-currents` plugin must be configured with the matching station list so every gate's `station_id` is present in `/currents`. A gate whose station is missing from the payload returns no slack windows.

## Data sources

- `signalk-currents` plugin `/currents` resource (CHS `wcp1-events` BC + NOAA CO-OPS US, fetched/cached by the plugin) — tidal gate slack windows
- `signalk-tides` plugin `/signalk/v2/api/tides/extremes` (offline Neaps engine, no internet) — tide heights

Current speeds in knots. Heights in metres. Times rendered in America/Vancouver (PDT/PST).

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SIGNALK_URL` | `http://naturalaspi:3000` | Base URL of the SignalK server running `signalk-currents` and `signalk-tides`; the gate tools GET `/signalk/v2/api/resources/currents` and the height tool GETs `/signalk/v2/api/tides/extremes` from here |

## Run

    SIGNALK_URL=http://naturalaspi:3000 uv run currents-mcp

## Test

    uv run pytest -v
