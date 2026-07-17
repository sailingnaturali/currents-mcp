"""Static passage database: destinations -> ordered tidal gates -> current stations.

Gate station IDs + coordinates verified against the live CHS IWLS API 2026-05-24
(Salish Sea core gates added 2026-07-17: Race Passage, Juan de Fuca - East,
Tillicum Bridge, Calamity Point, Second Narrows, Sechelt Rapids).
Routing validated against PNW cruising sources (48 North, Waggoner, Canadian
Boating). Open-water destinations have empty gate lists by design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    name: str
    provider: str          # "chs" | "noaa"
    station_id: str
    latitude: float
    longitude: float
    transit_window_minutes: int
    noaa_bin: int | None = None


@dataclass(frozen=True)
class Passage:
    destination: str
    aliases: tuple[str, ...]
    gate_names: tuple[str, ...]
    route_note: str


_GATE_LIST = [
    Gate("Dodd Narrows", "chs", "63aef1866a2b9417c035030f", 49.1344, -123.8171, 30),
    Gate("Active Pass", "chs", "63aef09f84e5432cd3b6c509", 48.8604, -123.3128, 60),
    Gate("Porlier Pass", "chs", "63aef0ed84e5432cd3b6c50b", 49.0150, -123.5850, 30),
    Gate("Gabriola Passage", "chs", "63aef12e84e5432cd3b6db8d", 49.1291, -123.7043, 30),
    Gate("Seymour Narrows", "chs", "63aefc7784e5432cd3b6eb1e", 50.1333, -125.3500, 30),
    Gate("Beazley Passage", "chs", "63aefe506a2b9417c0350720", 50.2263, -125.1420, 20),
    Gate("Hole in the Wall", "chs", "63aefcb26a2b9417c035071e", 50.3001, -125.2083, 20),
    Gate("Gillard Passage", "chs", "5dd3064fe0fdc4b9b4be6978", 50.3933, -125.1567, 20),
    Gate("Dent Rapids", "chs", "63af06d56a2b9417c0353451", 50.4100, -125.2117, 20),
    Gate("Arran Rapids", "chs", "63aeff5884e5432cd3b71283", 50.4200, -125.1400, 20),
    Gate("Race Passage", "chs", "63aeee896a2b9417c034d337", 48.3067, -123.5367, 30),
    Gate("Juan de Fuca - East", "chs", "63aeee1d84e5432cd3b6c500", 48.2317, -123.5300, 60),
    Gate("Tillicum Bridge", "chs", "64960066ebd87908f1fcb787", 48.4464, -123.4002, 20),
    Gate("Calamity Point", "chs", "5cebf1e43d0f4a073c4bc434", 49.3126, -123.1277, 30),
    Gate("Second Narrows", "chs", "5dd30650e0fdc4b9b4be6c2d", 49.2947, -123.0245, 30),
    Gate("Sechelt Rapids", "chs", "63aef40a6a2b9417c0350313", 49.7383, -123.8983, 20),
    Gate("Boundary Pass", "noaa", "PUG1717", 48.6912, -123.2450, 30, noaa_bin=35),
]

GATES: dict[str, Gate] = {g.name: g for g in _GATE_LIST}

# Active Pass, Porlier Pass, Gabriola Passage, Hole in the Wall, and Arran Rapids
# are reachable via get_tidal_gate by name but are intentionally not on any
# Victoria-origin route: the Gulf Islands passes gate crossings from the
# Strait/Vancouver side; Hole in the Wall is an alternate Discovery Islands gate;
# Arran is the Bute Inlet entrance north of Stuart Island (the Cordero route runs
# Gillard→Dent). Wire passages for them if/when a routing direction is confirmed.
# Same holds for the Salish Sea core gates added 2026-07-17 (Race Passage,
# Juan de Fuca - East, Tillicum Bridge, Calamity Point, Second Narrows, Sechelt
# Rapids): each is looked up by name, but no destination in this DB routes
# through one yet (no Sooke/west-coast, Vancouver-harbour, or Sechelt-Inlet
# passage exists). Add PASSAGES entries when a routed destination is added.
PASSAGES: tuple[Passage, ...] = (
    Passage("Nanaimo", ("nanaimo", "newcastle island"),
            ("Dodd Narrows",), "Protected inside route; Dodd is the final gate."),
    Passage("Gulf Islands", ("gulf islands", "salt spring", "salt spring island",
                             "ganges", "montague harbour", "montague"),
            (), "Inside from the south; no significant gate (Sansum is minor)."),
    Passage("Desolation Sound", ("desolation sound", "desolation", "prideaux haven"),
            (), "Open Strait of Georgia; entrance ungated."),
    Passage("Cortes Island", ("cortes island", "cortes", "gorge harbour", "squirrel cove"),
            (), "Open Strait of Georgia approach."),
    Passage("Campbell River", ("campbell river",),
            (), "Open Strait of Georgia; Seymour Narrows is north of town."),
    Passage("Discovery Islands", ("discovery islands", "surge narrows", "octopus islands"),
            ("Beazley Passage",), "Inside via Hoskyn Channel; Beazley is the channel through Surge Narrows."),
    Passage("Johnstone Strait via Discovery Passage",
            ("discovery passage", "johnstone strait", "broughtons"),
            ("Seymour Narrows",), "Discovery Passage north; the gate beyond Campbell River."),
    Passage("Cordero Channel", ("cordero channel", "yuculta rapids", "dent rapids", "blind channel"),
            ("Gillard Passage", "Dent Rapids"), "Inside route beyond Desolation; transit both on one slack."),
    Passage("Friday Harbor", ("friday harbor", "friday harbour", "san juan islands", "san juans"),
            ("Boundary Pass",), "US waters; NOAA provider."),
)


def find_gate(name: str) -> Gate | None:
    """Case-insensitive gate lookup by exact name."""
    key = name.strip().lower()
    for gate in _GATE_LIST:
        if gate.name.lower() == key:
            return gate
    return None


def match_destination(query: str) -> Passage | None:
    """Match a free-form destination against curated aliases (case-insensitive, exact)."""
    key = query.strip().lower()
    for passage in PASSAGES:
        if key == passage.destination.lower() or key in passage.aliases:
            return passage
    return None


def coverage() -> list[dict]:
    """Known destinations and the gates they cover - for list_gates()."""
    return [
        {"destination": p.destination,
         "aliases": list(p.aliases),
         "gates": list(p.gate_names)}
        for p in PASSAGES
    ]
