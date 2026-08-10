import pytest
from currents_mcp.currents_source import CACHE_TTL_SECONDS, CurrentsClient
from currents_mcp.providers import CurrentEvent

PAYLOAD = {"stations": [
    {"stationId": "g", "label": "Gillard", "lat": 50.39, "lon": -125.15,
     "floodDir": 160, "ebbDir": 340, "events": [
        {"utc": "2026-06-06T04:14:00.000Z", "kind": "slack", "speedKn": 0},
        {"utc": "2026-06-06T05:40:00.000Z", "kind": "flood", "speedKn": 4.1},
    ]},
]}


@pytest.mark.asyncio
async def test_events_for_station_parses_payload():
    calls = {"n": 0}
    async def fake_get(url):  # injected fetcher
        calls["n"] += 1
        return PAYLOAD
    c = CurrentsClient("http://signalk:3000", getter=fake_get)
    ev = await c.events_for_station("Gillard")
    assert [(e.kind, e.speed_knots) for e in ev] == [("slack", 0.0), ("flood", 4.1)]
    assert isinstance(ev[0], CurrentEvent)
    await c.events_for_station("Gillard")       # cached
    assert calls["n"] == 1                      # fetched once


@pytest.mark.asyncio
async def test_events_for_station_normalizes_case_and_whitespace():
    """A queried name differing only in case/whitespace from the plugin's
    label still resolves — the join key is normalized on both sides."""
    c = CurrentsClient("http://signalk:3000", getter=lambda url: PAYLOAD)
    ev = await c.events_for_station(" gillard  ")
    assert [(e.kind, e.speed_knots) for e in ev] == [("slack", 0.0), ("flood", 4.1)]


@pytest.mark.asyncio
async def test_events_carry_station_set_directions():
    """Station-level floodDir/ebbDir (plugin >= 0.3.0) land on every event."""
    c = CurrentsClient("http://signalk:3000", getter=lambda url: PAYLOAD)
    ev = await c.events_for_station("Gillard")
    assert all((e.flood_dir, e.ebb_dir) == (160, 340) for e in ev)


@pytest.mark.asyncio
async def test_dirs_for_station_exposes_provenance():
    """Station-level direction metadata: values, source, estimated flags."""
    payload = {"stations": [
        {"stationId": "g", "label": "Gillard", "lat": 50.39, "lon": -125.15,
         "floodDir": 95, "ebbDir": 275, "dirsSource": "config",
         "ebbDirEstimated": True, "events": []},
    ]}
    c = CurrentsClient("http://signalk:3000", getter=lambda url: payload)
    d = await c.dirs_for_station("Gillard")
    assert d == {"flood_dir": 95, "ebb_dir": 275, "source": "config",
                 "flood_dir_estimated": False, "ebb_dir_estimated": True}


@pytest.mark.asyncio
async def test_dirs_for_unknown_station_is_empty():
    c = CurrentsClient("http://signalk:3000", getter=lambda url: PAYLOAD)
    assert await c.dirs_for_station("missing") == {}


@pytest.mark.asyncio
async def test_missing_set_directions_default_to_none():
    """Older plugin payloads without floodDir/ebbDir still parse; dirs are None."""
    legacy = {"stations": [
        {"stationId": "g", "label": "Gillard", "lat": 50.39, "lon": -125.15, "events": [
            {"utc": "2026-06-06T04:14:00.000Z", "kind": "slack", "speedKn": 0},
        ]},
    ]}
    c = CurrentsClient("http://signalk:3000", getter=lambda url: legacy)
    ev = await c.events_for_station("Gillard")
    assert (ev[0].flood_dir, ev[0].ebb_dir) == (None, None)


@pytest.mark.asyncio
async def test_unknown_station_returns_empty():
    c = CurrentsClient("http://signalk:3000", getter=lambda url: PAYLOAD)
    assert await c.events_for_station("missing") == []


@pytest.mark.asyncio
async def test_unreachable_degrades_to_empty():
    """A down/unreachable signalk-currents yields [] (no crash), not an exception."""
    def boom(url):
        raise RuntimeError("plugin down")
    c = CurrentsClient("http://signalk:3000", getter=boom)
    assert await c.events_for_station("Gillard") == []


@pytest.mark.asyncio
async def test_unreachable_is_distinguishable_from_no_data():
    """The agent must be able to say 'service unreachable' vs 'no data here'."""
    def boom(url):
        raise RuntimeError("plugin down")
    down = CurrentsClient("http://signalk:3000", getter=boom)
    await down.events_for_station("Gillard")
    assert down.unreachable is True

    up = CurrentsClient("http://signalk:3000", getter=lambda url: PAYLOAD)
    await up.events_for_station("missing")
    assert up.unreachable is False


@pytest.mark.asyncio
async def test_one_malformed_station_does_not_blank_the_rest(capsys):
    """Per-record degradation (R3): a station missing a label, and a station
    with one malformed event, must not take down the good data."""
    payload = {"stations": [
        {"stationId": "no-label", "events": []},                        # bad station
        {"stationId": "broken", "label": "Bad Events", "events": [
            {"utc": "2026-06-06T04:14:00.000Z", "kind": "slack"},       # no speedKn
            {"utc": "2026-06-06T05:40:00.000Z", "kind": "flood", "speedKn": 4.1},
        ]},
        PAYLOAD["stations"][0],                                          # good station
    ]}
    c = CurrentsClient("http://signalk:3000", getter=lambda url: payload)
    good = await c.events_for_station("Gillard")
    assert [(e.kind, e.speed_knots) for e in good] == [("slack", 0.0), ("flood", 4.1)]
    # the malformed event is skipped; the station's good event survives
    broken = await c.events_for_station("Bad Events")
    assert [(e.kind, e.speed_knots) for e in broken] == [("flood", 4.1)]
    assert "skipping" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_concurrent_loads_fetch_once():
    """Two simultaneous tool calls must not double-fetch /currents (R6)."""
    import asyncio
    calls = {"n": 0}

    async def slow_get(url):
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return PAYLOAD

    c = CurrentsClient("http://signalk:3000", getter=slow_get)
    await asyncio.gather(c.events_for_station("Gillard"), c.events_for_station("Gillard"))
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_derived_flag_captured_per_station():
    """A derived gate (Malibu) is marked derived:true in the payload; its slack
    events still parse, but consumers must know it carries no speed/direction."""
    payload = {"stations": [
        {"stationId": "chs-malibu-rapids", "label": "Malibu Rapids", "lat": 50.16, "lon": -123.85,
         "derived": True, "events": [
            {"utc": "2026-03-11T05:30:00.000Z", "kind": "slack", "speedKn": 0}]},
        {"stationId": "g", "label": "Gillard", "lat": 50.39, "lon": -125.15,
         "floodDir": 160, "ebbDir": 340, "events": []},
    ]}
    c = CurrentsClient("http://signalk:3000", getter=lambda url: payload)
    assert await c.derived_for_station("Malibu Rapids") is True
    assert await c.derived_for_station("Gillard") is False
    # slack events still available
    ev = await c.events_for_station("Malibu Rapids")
    assert [(e.kind, e.speed_knots) for e in ev] == [("slack", 0.0)]


@pytest.mark.asyncio
async def test_cache_expires_so_a_long_lived_process_stops_serving_yesterday():
    """Without a TTL the payload was cached for the life of the process.

    These are tide/current predictions: an MCP server running past midnight
    would keep answering with yesterday's slack windows, at full confidence and
    with no way for the agent to tell. The plugin refreshes hourly, so a TTL
    well inside that bounds staleness without adding meaningful fetch cost.
    """
    now = {"t": 1000.0}
    calls = {"n": 0}

    async def fake_get(url):
        calls["n"] += 1
        return PAYLOAD

    c = CurrentsClient("http://signalk:3000", getter=fake_get, clock=lambda: now["t"])
    await c.events_for_station("Gillard")
    now["t"] += 60                                   # a minute later: still fresh
    await c.events_for_station("Gillard")
    assert calls["n"] == 1

    now["t"] += CACHE_TTL_SECONDS                    # past the window
    await c.events_for_station("Gillard")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_expired_cache_survives_a_failed_refresh():
    """Stale predictions beat none when the boat loses the currents service.

    Before the TTL existed, a loaded cache served forever, so losing SignalK
    left the agent with usable (if ageing) data. A naive TTL would throw that
    away and start answering empty the moment a refresh failed - strictly worse
    underway. Keep serving what we have and flag `unreachable`; the passage
    lead only reports the service as down when there are no windows to show.
    """
    now = {"t": 1000.0}
    state = {"fail": False}

    async def flaky_get(url):
        if state["fail"]:
            raise RuntimeError("connection refused")
        return PAYLOAD

    c = CurrentsClient("http://signalk:3000", getter=flaky_get, clock=lambda: now["t"])
    assert await c.events_for_station("Gillard")     # warm it

    state["fail"] = True
    now["t"] += CACHE_TTL_SECONDS + 1
    ev = await c.events_for_station("Gillard")

    assert [e.kind for e in ev] == ["slack", "flood"]   # stale, still served
    assert c.unreachable is True
