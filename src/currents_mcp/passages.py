"""Passage database loaded from the currents-vault: destinations -> ordered
tidal gates -> current stations, plus per-gate flood/ebb hazard notes.

The data lives in a separate public repo, `currents-vault` (one markdown file
per gate under `passes/`, a `destinations.yaml` routing table), so it is usable
beyond this MCP. Its location comes from `CURRENTS_VAULT_PATH` (default
`~/.currents-vault`, with a sibling-repo fallback for in-tree development). The
loader validates on import and fails loudly on a malformed file or a destination
that routes through an unknown gate.

Identity is split from knowledge. The vault holds only what it knows about a
passage — transit windows and hazards, the latter paraphrased from cruising
references (see the vault's `manifest.yaml`). A gate's name, position and
provider come from the station registry, keyed by the vault's `station` field,
so a curated name or position is written once. Gates are
therefore keyed internally by registry key; display names are presentation, and
`find_gate`, `coverage` and the tools' suggestion strings are what a person sees.

Open-water destinations have empty gate lists by design.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from currents_mcp.currents_source import _norm

_HAZARD_STATES = {"flood", "ebb", "any"}

# Station identity (name, position, provider) lives in
# @openwaters/station-metadata, which publishes it as a plain JSON artifact on
# ./data/* precisely so non-JavaScript consumers can read it without npm.
# Vendored here; test_registry_drift.py fails when it lags that repo.
_REGISTRY: dict[str, dict] = json.loads(
    (Path(__file__).parent / "_registry.json").read_text()
)


@dataclass(frozen=True)
class Hazard:
    state: str             # "flood" | "ebb" | "any" — when the danger applies
    text: str


@dataclass(frozen=True)
class Gate:
    key: str               # registry key, e.g. "chs-dodd-narrows" — never shown to a user
    name: str
    provider: str          # "chs" | "noaa"
    latitude: float
    longitude: float
    transit_window_minutes: int
    hazards: tuple[Hazard, ...] = ()


@dataclass(frozen=True)
class Passage:
    destination: str
    aliases: tuple[str, ...]
    gate_keys: tuple[str, ...]   # registry keys — map through GATES for display
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


def _gate_from(path: Path, registry: dict[str, dict]) -> Gate:
    """Build a gate from vault frontmatter: `station` names a registry entry that
    supplies identity; the vault supplies only its own knowledge (transit window,
    hazards)."""
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
    key = d.get("station")
    station = registry.get(key)
    if station is None:
        raise ValueError(f"{path.name}: station {key!r} is not in the station registry")
    lat, lon = station["position"]
    try:
        return Gate(
            key=key,
            name=station["name"],
            provider=station["provider"],
            latitude=float(lat),
            longitude=float(lon),
            transit_window_minutes=int(d["transit_window_minutes"]),
            hazards=hazards,
        )
    except KeyError as exc:
        raise ValueError(f"{path.name}: missing required field {exc}") from exc


def _load(root: Path,
          registry: dict[str, dict] | None = None,
          ) -> tuple[dict[str, Gate], tuple[Passage, ...]]:
    """Load the vault, resolving each gate's identity through the station
    registry. `registry` is injectable so the validation branches below can be
    tested against fixtures instead of the twenty real stations."""
    registry = _REGISTRY if registry is None else registry
    passes_dir = root / "passes"
    if not passes_dir.is_dir():
        raise FileNotFoundError(f"currents-vault at {root} has no passes/ directory")
    gates: dict[str, Gate] = {}
    origin: dict[str, str] = {}
    for path in sorted(passes_dir.glob("*.md")):
        gate = _gate_from(path, registry)
        if gate.key in gates:
            raise ValueError(f"{path.name}: station {gate.key!r} already "
                             f"defined by {origin[gate.key]}")
        # ponytail: display names are also required to be unique, so find_gate
        # stays unambiguous. Keys allow collisions (Juan de Fuca and Johnstone
        # Strait each have a Race Passage); when one lands, disambiguate with
        # the registry's `context` rather than relaxing this. Normalized the
        # same way as the plugin-correlation join key (_norm), so names that
        # differ only by case/whitespace don't slip past this guard and
        # collapse to one correlation-cache entry.
        clash = next((g for g in gates.values() if _norm(g.name) == _norm(gate.name)), None)
        if clash:
            raise ValueError(f"{path.name}: gate name {gate.name!r} already "
                             f"defined by {origin[clash.key]}")
        origin[gate.key] = path.name
        gates[gate.key] = gate

    raw = yaml.safe_load((root / "destinations.yaml").read_text()) or []
    passages: list[Passage] = []
    for entry in raw:
        keys = tuple(entry.get("gates") or ())
        unknown = [k for k in keys if k not in gates]
        if unknown:
            raise ValueError(f"destinations.yaml: {entry.get('destination')!r} "
                             f"routes through unknown gate(s) {unknown}")
        passages.append(Passage(
            destination=entry["destination"],
            aliases=tuple(entry.get("aliases") or ()),
            gate_keys=keys,
            route_note=entry.get("route_note", ""),
        ))

    # match_destination is first-wins, so a duplicate alias silently shadows a
    # later destination. Fail loudly instead.
    seen: dict[str, str] = {}
    for p in passages:
        for key in {p.destination.lower(), *p.aliases}:
            if key in seen:
                raise ValueError(f"destinations.yaml: {key!r} claimed by both "
                                 f"{seen[key]!r} and {p.destination!r}")
            seen[key] = p.destination
    return gates, tuple(passages)


GATES, PASSAGES = _load(vault_path())


def find_gate(name: str) -> Gate | None:
    """Case-insensitive gate lookup by exact display name. This is the
    user-facing entry point — people say "Dodd Narrows", not "chs-dodd-narrows"
    — so it matches names, never registry keys. _load guarantees names are
    unique, so the match is unambiguous."""
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
    """Known destinations and the gates they cover - for list_gates().
    Gates render as display names; registry keys never reach a reader."""
    return [
        {"destination": p.destination,
         "aliases": list(p.aliases),
         "gates": [GATES[k].name for k in p.gate_keys]}
        for p in PASSAGES
    ]
