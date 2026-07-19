"""Passage database loaded from the currents-vault: destinations -> ordered
tidal gates -> current stations, plus per-gate flood/ebb hazard notes.

The data lives in a separate public repo, `currents-vault` (one markdown file
per gate under `passes/`, a `destinations.yaml` routing table), so it is usable
beyond this MCP. Its location comes from `CURRENTS_VAULT_PATH` (default
`~/.currents-vault`, with a sibling-repo fallback for in-tree development). The
loader validates on import and fails loudly on a malformed file or a destination
that routes through an unknown gate.

Station IDs + coordinates were verified against the live CHS IWLS API; hazards
are paraphrased from cruising references (see the vault's `manifest.yaml`).
Open-water destinations have empty gate lists by design.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_HAZARD_STATES = {"flood", "ebb", "any"}


@dataclass(frozen=True)
class Hazard:
    state: str             # "flood" | "ebb" | "any" — when the danger applies
    text: str


@dataclass(frozen=True)
class Gate:
    name: str
    provider: str          # "chs" | "noaa"
    station_id: str
    latitude: float
    longitude: float
    transit_window_minutes: int
    noaa_bin: int | None = None
    hazards: tuple[Hazard, ...] = ()


@dataclass(frozen=True)
class Passage:
    destination: str
    aliases: tuple[str, ...]
    gate_names: tuple[str, ...]
    route_note: str


def vault_path() -> Path:
    """Resolve the currents-vault directory, preferring the canonical external
    copy so live edits take effect: CURRENTS_VAULT_PATH, else ~/.currents-vault,
    else the sibling repo (in-tree dev), else the snapshot bundled with this
    package. The bundle guarantees the server runs standalone; keep it in sync
    with github.com/sailingnaturali/currents-vault (enforced by the drift test)."""
    env = os.environ.get("CURRENTS_VAULT_PATH")
    if env:
        return Path(env).expanduser()
    home = Path("~/.currents-vault").expanduser()
    if home.is_dir():
        return home
    sibling = Path(__file__).resolve().parents[3] / "currents-vault"
    if sibling.is_dir():
        return sibling
    return Path(__file__).parent / "_vault"


def _parse_frontmatter(path: Path) -> dict:
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path.name}: missing YAML frontmatter")
    _, fm, _body = text.split("---", 2)
    data = yaml.safe_load(fm)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: frontmatter is not a mapping")
    return data


def _gate_from(path: Path) -> Gate:
    d = _parse_frontmatter(path)
    try:
        hazards = tuple(
            Hazard(h["state"], h["text"]) for h in (d.get("hazards") or [])
        )
    except (TypeError, KeyError) as exc:
        raise ValueError(f"{path.name}: each hazard needs 'state' and 'text'") from exc
    bad = [h.state for h in hazards if h.state not in _HAZARD_STATES]
    if bad:
        raise ValueError(f"{path.name}: unknown hazard state(s) {bad}; "
                         f"allowed: {sorted(_HAZARD_STATES)}")
    try:
        return Gate(
            name=d["name"],
            provider=d["provider"],
            station_id=str(d["station_id"]),
            latitude=float(d["latitude"]),
            longitude=float(d["longitude"]),
            transit_window_minutes=int(d["transit_window_minutes"]),
            noaa_bin=d.get("noaa_bin"),
            hazards=hazards,
        )
    except KeyError as exc:
        raise ValueError(f"{path.name}: missing required field {exc}") from exc


def _load(root: Path) -> tuple[dict[str, Gate], tuple[Passage, ...]]:
    passes_dir = root / "passes"
    if not passes_dir.is_dir():
        raise FileNotFoundError(f"currents-vault at {root} has no passes/ directory")
    gates: dict[str, Gate] = {}
    for path in sorted(passes_dir.glob("*.md")):
        gate = _gate_from(path)
        gates[gate.name] = gate

    raw = yaml.safe_load((root / "destinations.yaml").read_text()) or []
    passages: list[Passage] = []
    for entry in raw:
        names = tuple(entry.get("gates") or ())
        unknown = [n for n in names if n not in gates]
        if unknown:
            raise ValueError(f"destinations.yaml: {entry.get('destination')!r} "
                             f"routes through unknown gate(s) {unknown}")
        passages.append(Passage(
            destination=entry["destination"],
            aliases=tuple(entry.get("aliases") or ()),
            gate_names=names,
            route_note=entry.get("route_note", ""),
        ))
    return gates, tuple(passages)


GATES, PASSAGES = _load(vault_path())


def find_gate(name: str) -> Gate | None:
    """Case-insensitive gate lookup by exact name."""
    key = name.strip().lower()
    for gate in GATES.values():
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
