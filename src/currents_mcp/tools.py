"""MCP tool implementations + formatting/math helpers.

Display fields are pre-formatted and TTS-safe per docs/agent-lessons.md:
the model never sees raw UTC or SI values to reformat.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from currents_mcp.currents_source import CurrentsClient
from currents_mcp.passages import GATES, Gate, PASSAGES, coverage, find_gate, match_destination
from currents_mcp.providers import CurrentEvent, _iso_z, _parse_dt
from currents_mcp.tides_source import TidesClient

VICTORIA = (48.42, -123.37)
DEFAULT_SPEED_KNOTS = 6.0
DISPLAY_TZ = ZoneInfo("America/Vancouver")
_OPEN_WATER_SUMMARY = (
    "No tidal gates on the direct route - open-water passage; "
    "wind and weather are the constraint."
)


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


_OPPOSITE = {"flood": "ebb", "ebb": "flood"}

# 16-point compass, full words: speakable as-is (TTS reads "SSE" as letters).
_COMPASS_POINTS = [
    "north", "north-northeast", "northeast", "east-northeast",
    "east", "east-southeast", "southeast", "south-southeast",
    "south", "south-southwest", "southwest", "west-southwest",
    "west", "west-northwest", "northwest", "north-northwest",
]


def _compass(degrees_true: float | None) -> str | None:
    """Degrees true -> 16-point compass word, e.g. 340 -> 'north-northwest'."""
    if degrees_true is None:
        return None
    return _COMPASS_POINTS[round(degrees_true / 22.5) % 16]


def _direction_label(prev_kind: str | None, next_kind: str | None) -> str:
    """Turn the surrounding extrema into a 'ebb→flood' style slack label.

    If only one neighbor is known, infer the other as the opposite phase.
    """
    if prev_kind and next_kind:
        return f"{prev_kind}→{next_kind}"
    if prev_kind:
        return f"{prev_kind}→{_OPPOSITE[prev_kind]}"
    if next_kind:
        return f"{_OPPOSITE[next_kind]}→{next_kind}"
    return "slack"


def _fmt_slack(utc: datetime, direction: str) -> str:
    """e.g. 'Sun 21:14 PDT (slack, ebb→flood)'. When the direction is unknown
    (a derived gate has no flood/ebb neighbours) it reads just '(slack)'."""
    local = utc.astimezone(DISPLAY_TZ)
    if direction == "slack":
        return f"{local:%a %H:%M} {local:%Z} (slack)"
    return f"{local:%a %H:%M} {local:%Z} (slack, {direction})"


def _incoming_set(event: CurrentEvent, prev_kind: str | None, next_kind: str | None) -> str | None:
    """Which way the stream after this slack flows, e.g. 'flood sets north-northwest'.

    None when the station's set directions are unknown (plugin < 0.3.0).
    """
    incoming = next_kind or (_OPPOSITE[prev_kind] if prev_kind else None)
    if incoming is None:
        return None
    word = _compass(event.flood_dir if incoming == "flood" else event.ebb_dir)
    return f"{incoming} sets {word}" if word else None


def _fmt_height(utc: datetime, kind: str, height_m: float) -> str:
    """e.g. 'Low 09:31 PDT — 1.2 m'."""
    local = utc.astimezone(DISPLAY_TZ)
    return f"{kind.capitalize()} {local:%H:%M} {local:%Z} — {height_m:.1f} m"


def _slack_windows(events: list[CurrentEvent], limit: int, after: datetime) -> list[dict]:
    """Assemble up to `limit` slack windows at/after `after`, labeling direction
    from the nearest flood/ebb extrema on either side."""
    windows: list[dict] = []
    for i, e in enumerate(events):
        if e.kind != "slack" or e.utc < after:
            continue
        prev_kind = next((events[j].kind for j in range(i - 1, -1, -1)
                          if events[j].kind in ("flood", "ebb")), None)
        next_kind = next((events[j].kind for j in range(i + 1, len(events))
                          if events[j].kind in ("flood", "ebb")), None)
        label = _direction_label(prev_kind, next_kind)
        set_clause = _incoming_set(e, prev_kind, next_kind)
        if set_clause:
            label = f"{label} — {set_clause}"
        windows.append({
            "display": _fmt_slack(e.utc, label),
            "utc": _iso_z(e.utc),
        })
        if len(windows) >= limit:
            break
    return windows


def _recommended_depart(
    gate: Gate, events: list[CurrentEvent], depart_time: datetime, origin: tuple[float, float]
) -> str | None:
    """Recommend a departure time to hit the FIRST gate at its next reachable slack.

    v1 estimate: great-circle distance from origin at a fixed 6 knots.
    """
    dist_nm = _haversine_nm(origin[0], origin[1], gate.latitude, gate.longitude)
    travel = timedelta(hours=dist_nm / DEFAULT_SPEED_KNOTS)
    earliest_arrival = depart_time + travel
    slack = next((e for e in events if e.kind == "slack" and e.utc >= earliest_arrival), None)
    if slack is None:
        return None
    depart_by = (slack.utc - travel).astimezone(DISPLAY_TZ)
    return f"Depart by {depart_by:%H:%M} {depart_by:%Z} to hit {gate.name} at slack."


def _parse_dt_arg(value: str | None) -> datetime:
    """Parse an optional ISO date/datetime arg; default now (UTC).

    A date-only arg means that calendar day where the vessel is (DISPLAY_TZ),
    anchored at local midnight then converted to UTC — anchoring at 00:00Z
    would start "today" at ~16:00 the previous local afternoon (R2)."""
    if not value:
        return datetime.now(timezone.utc)
    v = value.strip()
    if "T" not in v and " " not in v:
        d = datetime.fromisoformat(v)          # naive midnight, date-only input
        return d.replace(tzinfo=DISPLAY_TZ).astimezone(timezone.utc)
    return _parse_dt(v)


_SETS_UNAVAILABLE = "Flood and ebb set directions are not available for this station."


def _gate_sets(dirs: dict) -> dict:
    """Station-level flood/ebb set, in words and °true — the 'which way does it
    flow' answer. Provenance is spoken: assumed (estimated) directions are
    qualified, and a missing direction is stated rather than silently omitted."""
    flood_dir, ebb_dir = dirs.get("flood_dir"), dirs.get("ebb_dir")
    flood_word, ebb_word = _compass(flood_dir), _compass(ebb_dir)
    if flood_word and ebb_word:
        flood_q = " (estimated)" if dirs.get("flood_dir_estimated") else ""
        ebb_q = " (estimated)" if dirs.get("ebb_dir_estimated") else ""
        display = f"Flood sets {flood_word}{flood_q}; ebb sets {ebb_word}{ebb_q}."
    else:
        display = _SETS_UNAVAILABLE
    return {"sets_display": display, "flood_dir_true": flood_dir, "ebb_dir_true": ebb_dir}


def _gate_suggestions() -> str:
    # GATES is keyed by registry key ("chs-dodd-narrows"); a person is shown names.
    return "Known gates: " + ", ".join(g.name for g in GATES.values()) + "."


_HAZARD_LABEL = {"flood": "On the flood", "ebb": "On the ebb", "any": "Any tide"}


def _gate_hazards(gate: Gate) -> dict:
    """Flood/ebb hazard notes for a gate — whirlpools, holes, sets, deflections,
    each labelled with the current state it applies to. Empty for gates with no
    curated notes yet, so callers can spread it unconditionally."""
    if not gate.hazards:
        return {}
    return {
        "hazards": [{"state": h.state, "text": h.text} for h in gate.hazards],
        "hazards_display": " ".join(
            f"{_HAZARD_LABEL[h.state]}: {h.text}" for h in gate.hazards
        ),
    }


async def _gate_current_state(
    currents: CurrentsClient, gate: Gate, after: datetime
) -> dict:
    """Current state for one gate: slack windows + flood/ebb sets. Shared by
    get_gate_current (named) and currents_near (by position)."""
    events = await currents.events_for_station(gate.name)
    out = {
        "name": gate.name,
        "slack_windows": _slack_windows(events, 3, after),
        "transit_window_minutes": gate.transit_window_minutes,
        **_gate_sets(await currents.dirs_for_station(gate.name)),
        **_gate_hazards(gate),
    }
    # A derived gate (Malibu) has no current station: slack timing only, no speed
    # and no flood/ebb set. Mark it so the agent presents windows honestly and
    # never implies a current vector.
    if await currents.derived_for_station(gate.name):
        out["derived"] = True
        out["derived_display"] = (
            "Slack timing only — this pass has no current station, so its slack is "
            "derived from a reference tide port's high/low water. No current speed "
            "or set is predicted."
        )
    if not events and currents.unreachable:
        # Empty because the service is down, not because there's no data (R1).
        out["service_display"] = (
            "The currents service is unreachable — slack data unavailable right now."
        )
    return out


async def get_gate_current(
    currents: CurrentsClient, name: str, date: str | None = None
) -> dict:
    """Return the next 3 slack windows for a single named gate."""
    gate = find_gate(name)
    if gate is None:
        return {"unmatched": True, "suggestions_display": _gate_suggestions()}
    return await _gate_current_state(currents, gate, _parse_dt_arg(date))


async def currents_near(
    currents: CurrentsClient, lat: float, lon: float, radius_nm: float = 15.0
) -> dict:
    """Nearest charted tidal gates to a position, nearest-first, each with its
    current state. For a specific named pass use get_gate_current."""
    after = _parse_dt_arg(None)
    ranked = sorted(
        ((_haversine_nm(lat, lon, g.latitude, g.longitude), g) for g in GATES.values()),
        key=lambda pair: pair[0],
    )
    near = [(d, g) for d, g in ranked if d <= radius_nm][:3]
    if not near:
        return {"gates": [],
                "summary_display": f"No charted tidal gate within {radius_nm:.0f} "
                                   "nautical miles."}
    gates = []
    for dist, gate in near:
        state = await _gate_current_state(currents, gate, after)
        state["distance_nm"] = round(dist, 1)
        gates.append(state)
    return {"gates": gates}


def _destination_suggestions() -> str:
    return "Known destinations: " + ", ".join(p.destination for p in PASSAGES) + "."


async def plan_passage(
    currents: CurrentsClient,
    destination: str,
    depart_time: str | None = None,
    from_lat: float | None = None,
    from_lon: float | None = None,
) -> dict:
    """Map a destination to its ordered gates, fetch slack windows, recommend a
    departure for the first gate."""
    passage = match_destination(destination)
    if passage is None:
        return {"unmatched": True, "suggestions_display": _destination_suggestions()}

    depart = _parse_dt_arg(depart_time)
    origin = (from_lat, from_lon) if from_lat is not None and from_lon is not None else VICTORIA

    if not passage.gate_keys:
        out = {
            "destination": passage.destination,
            "gates": [],
            "summary_display": _OPEN_WATER_SUMMARY,
        }
        if passage.route_note:
            out["route_note"] = passage.route_note
        return out

    gates_out: list[dict] = []
    prev_point = origin
    travel = timedelta(0)
    for idx, gkey in enumerate(passage.gate_keys):
        gate = GATES[gkey]
        # Filter each gate's windows by estimated arrival THERE, not by the
        # departure time — a downstream gate's slack an hour after departure
        # is unreachable and must not read as actionable.
        travel += timedelta(hours=_haversine_nm(
            prev_point[0], prev_point[1], gate.latitude, gate.longitude
        ) / DEFAULT_SPEED_KNOTS)
        prev_point = (gate.latitude, gate.longitude)
        eta = depart + travel
        events = await currents.events_for_station(gate.name)
        entry = {
            "name": gate.name,
            "slack_windows": _slack_windows(events, 3, eta if idx else depart),
            "transit_window_minutes": gate.transit_window_minutes,
            **_gate_sets(await currents.dirs_for_station(gate.name)),
            **_gate_hazards(gate),
        }
        if idx == 0:
            entry["recommended_depart_display"] = (
                _recommended_depart(gate, events, depart, origin)
                or f"No reachable slack at {gate.name} within the forecast window."
            )
        else:
            entry["recommended_depart_display"] = None
            entry["note_display"] = (
                "Slack windows shown from your estimated arrival at this gate "
                f"(~{travel.total_seconds() / 3600:.0f}h out at {DEFAULT_SPEED_KNOTS:.0f} kn); "
                "the recommended departure covers the first gate only."
            )
        gates_out.append(entry)

    first = gates_out[0]
    lead = (
        first.get("recommended_depart_display")
        or first.get("note_display")
        or "Check the first gate's slack windows."
    )
    if currents.unreachable and not any(g["slack_windows"] for g in gates_out):
        lead = "The currents service is unreachable — slack data unavailable right now."
    summary = f"{len(gates_out)} tidal gate(s). {lead}"
    out = {"destination": passage.destination, "gates": gates_out, "summary_display": summary}
    if passage.route_note:
        out["route_note"] = passage.route_note
    return out


def list_gates() -> dict:
    """Return known destinations and the gates they cover."""
    cov = coverage()
    display = "Covered destinations: " + "; ".join(
        f"{c['destination']} ({', '.join(c['gates']) or 'no gates'})" for c in cov
    )
    return {"coverage": cov, "display": display}


async def get_tide_heights(
    tides: TidesClient,
    lat: float,
    lon: float,
    date: str | None = None,
) -> dict:
    """Return high/low tide heights for the nearest tide station, starting at
    `date` (or now) and covering ~one local-day tide cycle.

    Heights come from the boat server's offline Neaps engine (signalk-tides),
    relative to LAT. CHS chart datum sits above LAT by a fixed per-station
    amount (up to ~0.4 m), so these read systematically higher than official
    tide tables; residual prediction differences are ~1 dm, timing within
    minutes. See the README datum caveat."""
    after = _parse_dt_arg(date)
    # 36 h window so we don't drop the local-day tail when `after` is late in
    # a UTC day (e.g. evening PDT pushes that night's events into tomorrow UTC).
    # Deliberately wall-clock-naive: over-provisioned to swallow DST days too.
    info, events = await tides.extremes(lat, lon, after, after + timedelta(hours=36))

    if info is None:
        return {
            "station_name": None,
            "distance_km": None,
            "events": [],
            "summary_display": ("Tide heights unavailable — the boat server is "
                                "unreachable; verify tide timing manually."),
        }

    out_events = [
        {
            "display": _fmt_height(e.utc, e.kind, e.height_m),
            "type": e.kind,
            "height_m": round(e.height_m, 1),
            "utc": _iso_z(e.utc),
        }
        for e in events if e.utc >= after
    ][:4]

    if not out_events:
        summary = (
            f"Tide heights unavailable for this position "
            f"(nearest station: {info['station_name']})."
        )
        return {
            "station_name": info["station_name"],
            "distance_km": info["distance_km"],
            "events": [],
            "summary_display": summary,
        }

    nxt = out_events[0]                # the next extreme in time, whichever kind
    label = nxt["type"]
    when_height = nxt["display"].split(" ", 1)[1]  # drop the "Low "/"High " prefix
    summary = (
        f"Nearest tide station: {info['station_name']}, "
        f"{info['distance_km']} km from you. Next {label} is {when_height}."
    )
    return {
        "station_name": info["station_name"],
        "distance_km": info["distance_km"],
        "events": out_events,
        "summary_display": summary,
    }
