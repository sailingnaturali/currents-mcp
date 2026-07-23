"""Reads tidal-current predictions from the signalk-currents plugin's /currents
resource, replacing the MCP's old direct CHS/NOAA fetching."""
from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Awaitable, Callable

import httpx

from currents_mcp.providers import CurrentEvent, _parse_dt

CURRENTS_PATH = "/signalk/v2/api/resources/currents"


def _norm(name: str) -> str:
    """Join key for correlating a gate to a plugin reading: fold case and trim
    so a label/name that differs only in casing or spacing still matches."""
    return name.strip().casefold()


def _event_from_plugin(d: dict, flood_dir: int | None, ebb_dir: int | None) -> CurrentEvent:
    """Map a plugin event; flood/ebb set (°true) is station-level config carried
    onto every event (absent from plugin < 0.3.0 payloads -> None)."""
    return CurrentEvent(
        utc=_parse_dt(d["utc"]), kind=d["kind"], speed_knots=float(d["speedKn"]),
        flood_dir=flood_dir, ebb_dir=ebb_dir,
    )


def _dirs_from_station(s: dict) -> dict:
    """Station-level direction metadata for provenance-aware displays."""
    if s.get("floodDir") is None and s.get("ebbDir") is None:
        return {}
    return {
        "flood_dir": s.get("floodDir"),
        "ebb_dir": s.get("ebbDir"),
        "source": s.get("dirsSource"),
        "flood_dir_estimated": bool(s.get("floodDirEstimated")),
        "ebb_dir_estimated": bool(s.get("ebbDirEstimated")),
    }


class CurrentsClient:
    """Fetches /currents once per process lifetime cheaply (in-memory), maps
    station name -> events (+ direction metadata). `getter` is injectable for tests."""

    def __init__(
        self, signalk_url: str,
        getter: Callable[[str], Awaitable[dict] | dict] | None = None,
    ) -> None:
        self._url = signalk_url.rstrip("/") + CURRENTS_PATH
        self._getter = getter or self._http_get
        self._cache: dict[str, list[CurrentEvent]] | None = None
        self._dirs: dict[str, dict] = {}
        self._derived: dict[str, bool] = {}
        self._lock = asyncio.Lock()
        # Distinguishes "service unreachable" from "no data for this station"
        # so the agent can say which one happened (R1).
        self.unreachable = False

    async def _http_get(self, url: str) -> dict:
        # /currents is a SignalK resource (/signalk/v2/api/resources/currents),
        # anonymously readable under allow_readonly — no token needed.
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def _load(self) -> dict[str, list[CurrentEvent]]:
        if self._cache is not None:
            return self._cache
        async with self._lock:                  # two tool calls -> one fetch (R6)
            if self._cache is not None:
                return self._cache
            try:
                result = self._getter(self._url)
                payload = await result if inspect.isawaitable(result) else result
            except Exception as e:
                # signalk-currents down/unreachable: degrade to no data (gate
                # tools show empty windows) rather than crashing the tool. Not
                # cached, so a later call retries. Logged to stderr (stdio MCP).
                print(f"currents-mcp: /currents fetch failed: {e}", file=sys.stderr)
                self.unreachable = True
                return {}
            self.unreachable = False
            # Per-record degradation (R3): one malformed station or event must
            # not blank the dataset — skip it, warn, keep serving the rest.
            cache: dict[str, list[CurrentEvent]] = {}
            dirs: dict[str, dict] = {}
            derived: dict[str, bool] = {}
            for s in payload.get("stations", []):
                name = s.get("label")
                if not name:
                    print(f"currents-mcp: skipping station without label: "
                          f"{s.get('stationId')!r}", file=sys.stderr)
                    continue
                key = _norm(name)
                events: list[CurrentEvent] = []
                for e in s.get("events", []):
                    try:
                        events.append(_event_from_plugin(
                            e, s.get("floodDir"), s.get("ebbDir")))
                    except (KeyError, TypeError, ValueError) as exc:
                        print(f"currents-mcp: skipping malformed event for {name!r}: "
                              f"{exc!r}", file=sys.stderr)
                events.sort(key=lambda e: e.utc)
                cache[key] = events
                dirs[key] = _dirs_from_station(s)
                # A derived gate (Malibu) carries slack timing only — no speed and
                # no flood/ebb axis. Consumers must not imply a current vector.
                derived[key] = bool(s.get("derived"))
            self._cache, self._dirs, self._derived = cache, dirs, derived
            return self._cache

    async def events_for_station(self, name: str) -> list[CurrentEvent]:
        return (await self._load()).get(_norm(name), [])

    async def dirs_for_station(self, name: str) -> dict:
        await self._load()
        return self._dirs.get(_norm(name), {})

    async def derived_for_station(self, name: str) -> bool:
        """True when the gate is a derived slack (timing only, no speed/set)."""
        await self._load()
        return self._derived.get(_norm(name), False)
