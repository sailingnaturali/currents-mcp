"""currents-mcp server. Exposes tidal-gate tools to any MCP client over stdio."""

from __future__ import annotations

import asyncio
import json
import logging
import os

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from naturali_mcp_netutil import resolve_local_host

from currents_mcp.currents_source import CurrentsClient
from currents_mcp.tides_source import TidesClient
from currents_mcp.tools import currents_near, get_gate_current, get_tide_heights, list_gates, plan_passage

logger = logging.getLogger(__name__)

TOOL_NAMES = ["plan_passage", "get_gate_current", "currents_near", "list_gates", "get_tide_heights"]

# The boat's SignalK server (Pi 5). Not localhost: the mac-dev rig is retired,
# and an unreachable /currents degrades silently to empty slack windows.
DEFAULT_SIGNALK_URL = "http://naturalaspi:3000"


async def dispatch(
    currents: CurrentsClient, tides: TidesClient, name: str, args: dict
) -> dict:
    """Route a tool call to its implementation. Shared by the server and tests."""
    if name == "plan_passage":
        return await plan_passage(
            currents,
            destination=args["destination"],
            depart_time=args.get("depart_time"),
            from_lat=args.get("from_lat"),
            from_lon=args.get("from_lon"),
        )
    if name == "get_gate_current":
        return await get_gate_current(currents, name=args["name"], date=args.get("date"))
    if name == "currents_near":
        return await currents_near(
            currents, lat=args["lat"], lon=args["lon"],
            radius_nm=args.get("radius_nm", 15.0),
        )
    if name == "list_gates":
        return list_gates()
    if name == "get_tide_heights":
        return await get_tide_heights(
            tides, lat=args["lat"], lon=args["lon"], date=args.get("date")
        )
    raise ValueError(f"Unknown tool: {name}")


def build_server(currents: CurrentsClient, tides: TidesClient) -> Server:
    server = Server("currents-mcp")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="plan_passage",
                description=(
                    "Use this when planning a ROUTE to a destination — it returns every "
                    "tidal gate along that route with slack windows and a recommended "
                    "departure time. Input is a destination name (e.g. 'Desolation Sound', "
                    "'Nanaimo'), not a gate name. "
                    "Do NOT use this for the current at a single named gate or pass — "
                    "use get_gate_current instead. "
                    "Do NOT use this to find out which gates exist — use list_gates instead. "
                    "For the current near your position right now, use currents_near or get_gate_current."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "destination": {"type": "string", "description": "e.g. 'Desolation Sound', 'Nanaimo'."},
                        "depart_time": {"type": "string", "description": "ISO8601; defaults to now."},
                        "from_lat": {"type": "number", "description": "Current latitude (decimal degrees)."},
                        "from_lon": {"type": "number", "description": "Current longitude (decimal degrees)."},
                    },
                    "required": ["destination"],
                },
            ),
            types.Tool(
                name="get_gate_current",
                description=(
                    "Use this for the current (speed, direction, slack windows) at a "
                    "SINGLE named tidal gate or pass, e.g. 'current at Boundary Pass', "
                    "'what is Dodd Narrows doing', 'when is Seymour Narrows slack'. "
                    "Input is the gate name. Returns the next 3 slack windows and flood/ebb "
                    "set directions for that gate. "
                    "Do NOT use this for route planning to a destination — use plan_passage instead. "
                    "Do NOT use this to enumerate available gates — use list_gates instead. "
                    "Do NOT use this for the current near your position when you have no gate name — use currents_near instead."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Gate name, e.g. 'Dodd Narrows', 'Boundary Pass'."},
                        "date": {"type": "string", "description": "ISO date; defaults to today."},
                    },
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="currents_near",
                description=(
                    "Use this for the current near a POSITION when you don't have a "
                    "specific gate name — e.g. 'what are the currents doing near us?', "
                    "'current near here'. Input is lat/lon; returns the nearest tidal "
                    "gate(s) within a radius (default 15 nm), nearest first, each with "
                    "slack windows and flood/ebb set. "
                    "Do NOT use this for a SPECIFIC named pass — use get_gate_current. "
                    "Do NOT use this for route planning to a destination — use plan_passage. "
                    "For tide HEIGHT (high/low water) rather than current movement, use "
                    "get_tide_heights."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "description": "Latitude (decimal degrees)."},
                        "lon": {"type": "number", "description": "Longitude (decimal degrees)."},
                        "radius_nm": {"type": "number", "description": "Search radius in NM (default 15)."},
                    },
                    "required": ["lat", "lon"],
                },
            ),
            types.Tool(
                name="list_gates",
                description=(
                    "Use this to discover which tidal gates and destinations are available "
                    "— returns a catalog of known destinations and the gates that cover them. "
                    "No live current data; use this when the user asks what gates or "
                    "destinations are supported. "
                    "Do NOT use this for the current at a specific gate — use get_gate_current instead. "
                    "Do NOT use this for route planning — use plan_passage instead. "
                    "For the current near a position, use currents_near."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="get_tide_heights",
                description=(
                    "High/low tide heights for the nearest tide station to a position. "
                    "Offline predictions from the boat server (signalk-tides/Neaps), "
                    "relative to LAT — chart datum sits above LAT by up to ~0.4 m at "
                    "some stations, so these read higher than official tide tables by "
                    "that fixed offset. "
                    "This is water LEVEL (high/low water), not current movement — for current speed/direction use get_gate_current (named pass) or currents_near (near a position)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "description": "Latitude (decimal degrees)."},
                        "lon": {"type": "number", "description": "Longitude (decimal degrees)."},
                        "date": {"type": "string", "description": "ISO date; defaults to today."},
                    },
                    "required": ["lat", "lon"],
                },
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, args: dict | None) -> list[types.TextContent]:
        result = await dispatch(currents, tides, name, args or {})
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


def main() -> None:
    signalk_url = resolve_local_host(os.environ.get("SIGNALK_URL", DEFAULT_SIGNALK_URL))
    currents = CurrentsClient(signalk_url)
    tides = TidesClient(signalk_url)
    server = build_server(currents, tides)

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
