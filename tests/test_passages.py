import pytest

from currents_mcp.passages import GATES, _load, find_gate, match_destination, coverage


def test_known_gate_has_chs_station():
    gate = GATES["Dodd Narrows"]
    assert gate.provider == "chs"
    assert gate.station_id == "63aef1866a2b9417c035030f"


def test_find_gate_is_case_insensitive():
    assert find_gate("dodd narrows").name == "Dodd Narrows"
    assert find_gate("DODD NARROWS").name == "Dodd Narrows"
    assert find_gate("nope") is None


def test_match_destination_by_alias():
    p = match_destination("prideaux haven")
    assert p.destination == "Desolation Sound"
    assert p.gate_names == ()  # open-water, no gates


def test_match_destination_with_gates_is_ordered():
    p = match_destination("Cordero Channel")
    assert p.gate_names == ("Gillard Passage", "Dent Rapids")


def test_match_destination_unknown_returns_none():
    assert match_destination("Atlantis") is None


def test_arran_rapids_is_a_known_gate():
    # Added when the Pi station list grew to cover it (CT&CT Vol 6: flood 060/ebb 240).
    gate = GATES["Arran Rapids"]
    assert gate.provider == "chs"
    assert gate.station_id == "63aeff5884e5432cd3b71283"
    assert gate.transit_window_minutes == 20


def test_boundary_pass_is_noaa():
    assert GATES["Boundary Pass"].provider == "noaa"
    assert GATES["Boundary Pass"].noaa_bin == 35


def test_coverage_lists_destinations_and_gates():
    cov = coverage()
    names = {c["destination"] for c in cov}
    assert "Nanaimo" in names and "Friday Harbor" in names


def test_new_destinations_route_through_their_gates():
    assert match_destination("skookumchuck").gate_names == ("Sechelt Rapids",)
    assert match_destination("deep cove").gate_names == ("First Narrows", "Second Narrows")
    assert match_destination("sooke").gate_names == ("Race Passage", "Juan de Fuca - East")


def test_duplicate_alias_across_destinations_is_rejected(tmp_path):
    # match_destination is first-wins, so a shadowed alias would fail silently.
    (tmp_path / "passes").mkdir()
    (tmp_path / "destinations.yaml").write_text(
        "- destination: A\n  aliases: [shared]\n  gates: []\n"
        "- destination: B\n  aliases: [shared]\n  gates: []\n"
    )
    with pytest.raises(ValueError, match="claimed by both"):
        _load(tmp_path)


def test_duplicate_gate_name_across_files_is_rejected(tmp_path):
    # Juan de Fuca and Johnstone Strait each have a Race Passage; keying gates
    # by name means a repeat would silently drop the earlier file.
    passes = tmp_path / "passes"
    passes.mkdir()
    body = ("---\nname: Race Passage\nprovider: chs\nstation_id: {sid}\n"
            "latitude: 48.3\nlongitude: -123.5\ntransit_window_minutes: 30\n"
            "hazards: []\n---\n")
    (passes / "a-race.md").write_text(body.format(sid="aaa"))
    (passes / "b-race.md").write_text(body.format(sid="bbb"))
    (tmp_path / "destinations.yaml").write_text("[]\n")
    with pytest.raises(ValueError, match="already defined by"):
        _load(tmp_path)


def test_destination_routing_through_an_unknown_gate_is_rejected(tmp_path):
    """_load refuses a destination that routes through a gate that does not
    exist. The guard is real (passages.py) but was unpinned, so a refactor
    could have quietly dropped it.

    This matters because destinations.yaml refers to gates by display name, so
    a rename has to be made in two places at once - and a typo in either would
    otherwise only surface at request time, as a passage with a gate nobody
    can look up.
    """
    (tmp_path / "passes").mkdir()
    (tmp_path / "passes" / "real.md").write_text(
        "---\n"
        "name: Real Gate\n"
        "provider: chs\n"
        "station_id: abc123\n"
        "latitude: 49.0\n"
        "longitude: -123.0\n"
        "transit_window_minutes: 30\n"
        "---\n"
        "Body.\n"
    )
    (tmp_path / "destinations.yaml").write_text(
        "- destination: Somewhere\n"
        "  aliases: [somewhere]\n"
        "  gates: [Typo'd Gate]\n"
        "  route_note: n/a\n"
    )

    with pytest.raises(ValueError, match="routes through unknown gate"):
        _load(tmp_path)


def test_a_valid_destination_loads(tmp_path):
    """Companion to the above: the same fixture with the gate name spelled
    correctly must load, so the test above is failing for the reason claimed
    and not because the fixture is malformed."""
    (tmp_path / "passes").mkdir()
    (tmp_path / "passes" / "real.md").write_text(
        "---\n"
        "name: Real Gate\n"
        "provider: chs\n"
        "station_id: abc123\n"
        "latitude: 49.0\n"
        "longitude: -123.0\n"
        "transit_window_minutes: 30\n"
        "---\n"
        "Body.\n"
    )
    (tmp_path / "destinations.yaml").write_text(
        "- destination: Somewhere\n"
        "  aliases: [somewhere]\n"
        "  gates: [Real Gate]\n"
        "  route_note: n/a\n"
    )

    gates, passages = _load(tmp_path)
    assert list(gates) == ["Real Gate"]
    assert passages[0].gate_names == ("Real Gate",)
