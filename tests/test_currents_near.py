import pytest

from currents_mcp.currents_source import CurrentsClient
from currents_mcp.passages import GATES
from currents_mcp.tools import currents_near


def _currents(payload):
    return CurrentsClient("http://signalk:3000", getter=lambda url: payload)


_EMPTY = {"stations": []}


async def test_nearest_first_with_distance():
    dodd = GATES["Dodd Narrows"]
    payload = {"stations": [{"stationId": dodd.station_id, "label": dodd.name,
                             "lat": dodd.latitude, "lon": dodd.longitude, "events": [
        {"utc": "2026-05-24T09:14:00Z", "kind": "slack", "speedKn": 0.0},
        {"utc": "2026-05-24T12:14:00Z", "kind": "flood", "speedKn": 6.0},
    ]}]}
    res = await currents_near(_currents(payload),
                              lat=dodd.latitude + 0.01, lon=dodd.longitude + 0.01,
                              radius_nm=15)
    assert res["gates"], "expected at least the adjacent gate"
    assert res["gates"][0]["name"] == "Dodd Narrows"
    assert res["gates"][0]["distance_nm"] < 2
    dists = [g["distance_nm"] for g in res["gates"]]
    assert dists == sorted(dists), "gates must be nearest-first"


async def test_empty_with_message_when_none_in_radius():
    # Mid-ocean — no charted gate within 15 nm.
    res = await currents_near(_currents(_EMPTY), lat=45.0, lon=-140.0, radius_nm=15)
    assert res["gates"] == []
    assert "15 nautical miles" in res["summary_display"]


async def test_radius_excludes_far_gates():
    dodd = GATES["Dodd Narrows"]
    res = await currents_near(_currents(_EMPTY),
                              lat=dodd.latitude, lon=dodd.longitude, radius_nm=0.1)
    # 0.1 nm radius around Dodd: only a gate essentially on the point qualifies.
    assert all(g["distance_nm"] <= 0.1 for g in res["gates"])


async def test_caps_at_three():
    # Huge radius from a central Salish Sea point pulls in many gates; cap is 3.
    res = await currents_near(_currents(_EMPTY), lat=49.0, lon=-123.5, radius_nm=500)
    assert len(res["gates"]) <= 3
    dists = [g["distance_nm"] for g in res["gates"]]
    assert dists == sorted(dists)
