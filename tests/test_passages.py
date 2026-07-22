import pytest

from currents_mcp.passages import GATES, _load, find_gate, match_destination, coverage


def test_known_gate_has_chs_station():
    gate = GATES["chs-dodd-narrows"]
    assert gate.provider == "chs"


def test_find_gate_is_case_insensitive():
    assert find_gate("dodd narrows").name == "Dodd Narrows"
    assert find_gate("DODD NARROWS").name == "Dodd Narrows"
    assert find_gate("nope") is None


def test_match_destination_by_alias():
    p = match_destination("prideaux haven")
    assert p.destination == "Desolation Sound"
    assert p.gate_keys == ()  # open-water, no gates


def test_match_destination_with_gates_is_ordered():
    p = match_destination("Cordero Channel")
    assert p.gate_keys == ("chs-gillard-passage", "chs-dent-rapids")


def test_match_destination_unknown_returns_none():
    assert match_destination("Atlantis") is None


def test_arran_rapids_is_a_known_gate():
    # Added when the Pi station list grew to cover it (CT&CT Vol 6: flood 060/ebb 240).
    gate = GATES["chs-arran-rapids"]
    assert gate.provider == "chs"
    assert gate.transit_window_minutes == 20


def test_boundary_pass_is_noaa():
    assert GATES["noaa-boundary-pass"].provider == "noaa"


def test_coverage_lists_destinations_and_gates():
    cov = coverage()
    names = {c["destination"] for c in cov}
    assert "Nanaimo" in names and "Friday Harbor" in names


def test_new_destinations_route_through_their_gates():
    assert match_destination("skookumchuck").gate_keys == ("chs-sechelt-rapids",)
    assert match_destination("deep cove").gate_keys == (
        "chs-first-narrows", "chs-second-narrows")
    assert match_destination("sooke").gate_keys == (
        "chs-race-passage", "chs-juan-de-fuca-east")


def test_duplicate_alias_across_destinations_is_rejected(tmp_path):
    # match_destination is first-wins, so a shadowed alias would fail silently.
    (tmp_path / "passes").mkdir()
    (tmp_path / "destinations.yaml").write_text(
        "- destination: A\n  aliases: [shared]\n  gates: []\n"
        "- destination: B\n  aliases: [shared]\n  gates: []\n"
    )
    with pytest.raises(ValueError, match="claimed by both"):
        _load(tmp_path)


# A stand-in station registry, so the loader's validation branches are testable
# without depending on which twenty stations the real registry happens to hold.
_FAKE_REGISTRY = {
    "chs-real-gate": {"name": "Real Gate", "position": [49.0, -123.0],
                      "provider": "chs"},
    # Two keys, one display name — representable in the registry, and exactly
    # what the display-name guard in _load exists to reject.
    "chs-race-passage": {"name": "Race Passage", "position": [48.3, -123.5],
                         "provider": "chs"},
    "chs-johnstone-race-passage": {"name": "Race Passage", "position": [50.5, -126.5],
                                   "provider": "chs"},
}


def _write_gate(tmp_path, filename, station):
    passes = tmp_path / "passes"
    passes.mkdir(exist_ok=True)
    (passes / filename).write_text(
        f"---\nstation: {station}\ntransit_window_minutes: 30\n---\nBody.\n"
    )


def test_duplicate_gate_name_across_files_is_rejected(tmp_path):
    # Keys are unique, but find_gate resolves display names — so two gates
    # sharing one would make a lookup ambiguous. Reject at load instead.
    _write_gate(tmp_path, "a-race.md", "chs-race-passage")
    _write_gate(tmp_path, "b-race.md", "chs-johnstone-race-passage")
    (tmp_path / "destinations.yaml").write_text("[]\n")
    with pytest.raises(ValueError, match="already defined by"):
        _load(tmp_path, _FAKE_REGISTRY)


def test_duplicate_gate_name_differing_only_by_case_is_rejected(tmp_path):
    # The uniqueness guard must use the same normalization (_norm: casefold +
    # strip) as the plugin-correlation join key, or two registry entries whose
    # names differ only by case/whitespace would pass this guard yet collapse
    # to one correlation-cache entry, silently sharing plugin events.
    registry = {
        "chs-race-passage": {"name": "Race Passage", "position": [48.3, -123.5],
                             "provider": "chs"},
        "chs-race-passage-2": {"name": " race passage ", "position": [48.3, -123.5],
                               "provider": "chs"},
    }
    _write_gate(tmp_path, "a-race.md", "chs-race-passage")
    _write_gate(tmp_path, "b-race.md", "chs-race-passage-2")
    (tmp_path / "destinations.yaml").write_text("[]\n")
    with pytest.raises(ValueError, match="already defined by"):
        _load(tmp_path, registry)


def test_gate_with_no_registry_entry_is_rejected(tmp_path):
    # The twenty-first gate: a station key that nothing in the registry defines
    # must fail loudly at load, naming the key — not load as a partial gate.
    _write_gate(tmp_path, "ghost.md", "chs-nowhere")
    (tmp_path / "destinations.yaml").write_text("[]\n")
    with pytest.raises(ValueError, match="chs-nowhere.*not in the station registry"):
        _load(tmp_path, _FAKE_REGISTRY)


def test_destination_routing_through_an_unknown_gate_is_rejected(tmp_path):
    """_load refuses a destination that routes through a gate that does not
    exist. The guard is real (passages.py) but was unpinned, so a refactor
    could have quietly dropped it.

    Routing is by registry key, so a typo cannot be masked by a display-name
    match; without this guard it would only surface at request time, as a
    passage carrying a gate nobody can look up.
    """
    _write_gate(tmp_path, "real.md", "chs-real-gate")
    (tmp_path / "destinations.yaml").write_text(
        "- destination: Somewhere\n"
        "  aliases: [somewhere]\n"
        "  gates: [chs-typod-gate]\n"
        "  route_note: n/a\n"
    )

    with pytest.raises(ValueError, match="routes through unknown gate"):
        _load(tmp_path, _FAKE_REGISTRY)


def test_a_valid_destination_loads(tmp_path):
    """Companion to the above: the same fixture with the gate key spelled
    correctly must load, so the test above is failing for the reason claimed
    and not because the fixture is malformed."""
    _write_gate(tmp_path, "real.md", "chs-real-gate")
    (tmp_path / "destinations.yaml").write_text(
        "- destination: Somewhere\n"
        "  aliases: [somewhere]\n"
        "  gates: [chs-real-gate]\n"
        "  route_note: n/a\n"
    )

    gates, passages = _load(tmp_path, _FAKE_REGISTRY)
    assert list(gates) == ["chs-real-gate"]
    assert gates["chs-real-gate"].name == "Real Gate"   # identity came from the registry
    assert passages[0].gate_keys == ("chs-real-gate",)
