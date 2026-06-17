# currents_near — Near-Position Currents Tool — Design

**Status:** Approved 2026-06-17
**Consumes:** the ADR-0002 benchmark finding — the `currents-nearby` eval ("what
are the currents doing near us right now?") fails for weaker local models (2/4)
because currents-mcp is entirely gate/named-pass based and has no near-position
tool, forcing a position→named-gate reasoning leap. Same shape as the anchoring
orchestration solved by `assess_anchorage`.
**Plan:** `docs/superpowers/plans/2026-06-17-currents-near.md` (to be written)

## Context

currents-mcp exposes `get_gate_current(name)`, `plan_passage`, `list_gates`,
`get_tide_heights(lat,lon)` — currents are addressed by named pass. A "near here"
currents ask has no single tool; the model must resolve its position to a nearby
gate name. Capable models do it (qwen3.6 via a `list_gates` discovery step,
llama3.1 directly); weaker ones don't (qwen3.5 fetched position but never reached
currents; hermes3 punted). Per the determinism-first lever proven by
`assess_anchorage`: **collapse the reasoning into one composed tool.**

Feasibility is confirmed: `passages.py` defines `Gate(name, station_id, latitude,
longitude)` and a `GATES` registry; `tools.py` already has `_haversine_nm(...)`
(used by `plan_passage`) and the per-gate current assembly used by
`get_gate_current`. So `currents_near` is purely internal to currents-mcp — no
shared-lib work.

## Decision

Add `currents_near(lat, lon, radius_nm?)`: the nearest gates within a radius,
nearest-first, each with its current state — one call, no gate name needed.
Re-annotate the `currents-nearby` eval + route the navigator prompt to it, then
re-measure.

## Components

### 1. currents-mcp — `currents_near` (`src/currents_mcp/tools.py` + `server.py`)

- **Extract a shared per-gate-current helper.** Pull the "current state for one
  gate" assembly out of the `get_gate_current` tool into a helper (e.g.
  `async _gate_current(gate: Gate, currents, now) -> dict` returning set/drift,
  ebb/flood state, and the next slack windows via the existing `_slack_windows`/
  `events_for_station`/`dirs_for_station` path). `get_gate_current` is refactored
  to call it. **Refactor freely** — no customers on the MCPs, so the output
  shape/formatting may change if it's cleaner; the only hard requirement is that
  `get_gate_current` still answers the named-pass ask correctly (the
  `current-boundary` eval depends on it). Update its tests to whatever the new
  shape is rather than contorting to keep them byte-identical.
- **`currents_near(lat, lon, radius_nm=15.0)`** (async tool):
  1. For every `gate` in `GATES`, `d = _haversine_nm(lat, lon, gate.latitude, gate.longitude)`.
  2. Keep gates with `d <= radius_nm`, sort ascending by `d`, take the nearest **≤3**.
  3. For each, `_gate_current(gate, currents, now)`; attach `distance_nm` (rounded).
  4. Return `{"gates": [ {name, distance_nm, ...current fields...}, ... ]}` nearest-first.
  5. **None within radius →** `{"gates": [], "summary_display": "No charted tidal
     gate within 15 nautical miles."}` (use the actual radius in the string).
  - Default radius **15 nm**, cap **3 gates** (gates are sparse in these waters).
  - TTS-safe `display` fields per fleet conventions; reuse `get_gate_current`'s
    formatting via the shared helper.
- **Register** the tool in `server.py`'s `tool_list` (inputs: `lat`, `lon`
  required; `radius_nm` optional) with a description that states *when* to use it
  ("the current near a position / 'what's the current doing near us?' — finds the
  nearest tidal gate(s) and their state; for a SPECIFIC named pass use
  `get_gate_current`; for route planning use `plan_passage`"). Async dispatch in
  the `call_tool` handler (it awaits the currents source, like the other gate
  tools).
- **Re-disambiguate ALL currents tool descriptions.** Adding a 5th tool risks
  re-creating the exact ambiguity the earlier disambiguation fixed (small models
  picking the wrong gate tool). Sharpen every currents-mcp tool description with a
  leading "use this for…" + explicit cross-references to its siblings — the proven
  lever: `get_gate_current` (current at a SINGLE named pass — for "near me" use
  `currents_near`), `currents_near` (current near a position / "what's it doing near
  us?" — for a named pass use `get_gate_current`, for a route use `plan_passage`),
  `plan_passage` (route to a destination), `list_gates` (catalog of known gates/
  destinations), `get_tide_heights` (water LEVEL, high/low — not current movement).
  Add/extend a test asserting each description states its use and names its siblings
  (mirror the pilotbook/signalk disambiguation tests).
- **Tests** (`tests/`): nearest-first ordering + `distance_nm`; radius filter
  (a far gate excluded); empty + message when none within radius; cap at 3;
  per-gate current fields present. Mock the currents source (respx/fake) and use a
  position near a known gate (Boundary Pass area) plus an open-water position for
  the empty case. Plus a behavior-preservation check that `get_gate_current` still
  returns its prior shape after the helper extraction.

### 2. Consumers (`naturali-agents`)

- **Navigator prompt** (`skills/navigator/body.md`): add `mcp_currents_near(lat,
  lon, radius_nm?)` to the tool list, and an instruction — for "what are the
  currents doing near us / near here?" read position, then call `currents_near`;
  for a named pass keep using `get_gate_current`. Regenerate the prompt mirror
  (pre-commit hook redeploys).
- **Benchmark golden** (`poseidon/bench/golden_asks.json`): `currents-nearby`
  `expected_tools` → `["mcp__currents__currents_near"]`, add `expected_tools_flat`
  the same. (`current-boundary` stays on `get_gate_current` — the named-pass ask.)

### 3. Re-measure

Re-run the local sweep (qwen3.6, llama3.1, qwen3.5, hermes3) and check
`currents-nearby` flips to ✓ for the models that previously failed the
position→gate leap (esp. qwen3.5, hermes3), confirming the composed-tool lever
again. Record deltas in ADR 0002.

## Out of scope
- **Not** out of scope (per 2026-06-17 direction — no MCP customers, move fast):
  behavior changes to the existing currents tools are permitted where they help.
  The deliberate ones here are the free helper refactor and the all-tool
  re-disambiguation above; don't make gratuitous changes without a benefit. The
  tool *shape* is decided: keep `get_gate_current` and `currents_near` as two
  separate single-purpose tools (not merged/polymorphic).
- Tide near-position — `get_tide_heights(lat, lon)` is already position-based
  (nearest station); untouched.
- The "currents-mcp episode" content beat — already tracked in the phase-2 arc;
  not part of this build.

## Testing
Unit (no network): the `currents_near` ordering/radius/empty/cap tests, the
all-tool description-disambiguation test, and `get_gate_current` still correctly
answering a named pass (its tests updated to whatever shape the refactor lands),
all with a mocked currents source.
Integration: the §3 live sweep is the end-to-end check (Ollama + Pi + MCP servers
running the updated currents-mcp from its checkout). currents-mcp + naturali-agents
suites stay green.
