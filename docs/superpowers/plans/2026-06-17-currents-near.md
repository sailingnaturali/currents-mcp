# currents_near Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `currents_near(lat, lon, radius_nm?)` to currents-mcp — the nearest tidal gates to a position with their current state, one call — so models answer "currents near us" without reasoning position→gate, and re-disambiguate all five currents tool descriptions.

**Architecture:** Extract the per-gate current assembly out of `get_gate_current` into a shared `_gate_current_state` helper; `currents_near` ranks `GATES` by `_haversine_nm` to the position, keeps the nearest ≤3 within `radius_nm` (default 15), and returns each via the shared helper + `distance_nm`. Register it, sharpen every currents tool description to cross-reference it. Then update the navigator prompt + benchmark golden and re-measure. Spec: `docs/superpowers/specs/2026-06-17-currents-near-design.md`.

**Tech Stack:** Python 3.11+, mcp, pytest (asyncio), uv. Repos: `currents-mcp` (tool), `naturali-agents` (consumers), `planning` (ADR).

---

## File Structure
- Modify: `currents-mcp/src/currents_mcp/tools.py` — `_gate_current_state` helper, `get_gate_current` refactor, new `currents_near`.
- Modify: `currents-mcp/src/currents_mcp/server.py` — `TOOL_NAMES`, `dispatch`, `_list_tools` (register `currents_near` + re-disambiguate 5 descriptions).
- Modify/Create tests: `currents-mcp/tests/test_currents_near.py` (new); `currents-mcp/tests/test_server.py` (TOOL_NAMES + disambiguation assertions).
- Modify: `naturali-agents/skills/navigator/body.md` (+ regenerated `prompts/navigator.md`), `naturali-agents/poseidon/bench/golden_asks.json`.
- Modify: `planning/docs/adr/0002-model-strategy.md` (re-measure, Task 3).

All currents-mcp commands run from `~/src/sailingnaturali/currents-mcp`.

---

## Task 1: currents-mcp — `currents_near` + re-disambiguation

Work on branch `feat/currents-near` (`git checkout -b feat/currents-near`). Do not merge; the controller merges after review.

### Step 1: Extract the shared per-gate helper (refactor `get_gate_current`)

In `src/currents_mcp/tools.py`, add a helper and rewrite `get_gate_current` to use it. Replace the existing `get_gate_current` function:
```python
async def get_gate_current(
    currents: CurrentsClient, name: str, date: str | None = None
) -> dict:
    """Return the next 3 slack windows for a single named gate."""
    gate = find_gate(name)
    if gate is None:
        return {"unmatched": True, "suggestions_display": _gate_suggestions()}
    after = _parse_dt_arg(date)
    events = await currents.events_for_station(gate.station_id)
    out = {
        "name": gate.name,
        "slack_windows": _slack_windows(events, 3, after),
        "transit_window_minutes": gate.transit_window_minutes,
        **_gate_sets(await currents.dirs_for_station(gate.station_id)),
    }
    if not events and currents.unreachable:
        # Empty because the service is down, not because there's no data (R1).
        out["service_display"] = (
            "The currents service is unreachable — slack data unavailable right now."
        )
    return out
```
with:
```python
async def _gate_current_state(
    currents: CurrentsClient, gate: Gate, after: datetime
) -> dict:
    """Current state for one gate: slack windows + flood/ebb sets. Shared by
    get_gate_current (named) and currents_near (by position)."""
    events = await currents.events_for_station(gate.station_id)
    out = {
        "name": gate.name,
        "slack_windows": _slack_windows(events, 3, after),
        "transit_window_minutes": gate.transit_window_minutes,
        **_gate_sets(await currents.dirs_for_station(gate.station_id)),
    }
    if not events and currents.unreachable:
        # Empty because the service is down, not because there's no data (R1).
        out["service_display"] = (
            "The currents service is unreachable — slack data unavailable right now."
        )
    return out


async def get_gate_current(
    currents: CurrentsClient, name: str, date: str | None = None
) -> dict:
    """Return the next 3 slack windows for a single named gate."""
    gate = find_gate(name)
    if gate is None:
        return {"unmatched": True, "suggestions_display": _gate_suggestions()}
    return await _gate_current_state(currents, gate, _parse_dt_arg(date))
```
Ensure `Gate` and `datetime` are imported in tools.py (Gate from `currents_mcp.passages`, datetime from `datetime`). Check the existing imports; add if missing.

### Step 2: Run get_gate_current tests (refactor is behavior-identical)

Run: `uv run pytest tests/test_get_gate_current.py -q`
Expected: PASS unchanged — `get_gate_current`'s output is identical (the helper returns exactly the old dict).

### Step 3: Write the failing `currents_near` tests

Create `tests/test_currents_near.py`:
```python
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
```

### Step 4: Run to verify it fails

Run: `uv run pytest tests/test_currents_near.py -q`
Expected: FAIL — `cannot import name 'currents_near'`.

### Step 5: Implement `currents_near`

In `src/currents_mcp/tools.py`, add (after `get_gate_current`):
```python
async def currents_near(
    currents: CurrentsClient, lat: float, lon: float, radius_nm: float = 15.0
) -> dict:
    """Nearest charted tidal gates to a position, nearest-first, each with its
    current state. For a specific named pass use get_gate_current."""
    after = _parse_dt_arg(None)
    ranked = sorted(
        ((_haversine_nm(lat, lon, g.latitude, g.longitude), g) for g in GATES.values()),
        key=lambda pair: pair[0],
    )
    near = [(d, g) for d, g in ranked if d <= radius_nm][:3]
    if not near:
        return {"gates": [],
                "summary_display": f"No charted tidal gate within {radius_nm:.0f} "
                                   "nautical miles."}
    gates = []
    for dist, gate in near:
        state = await _gate_current_state(currents, gate, after)
        state["distance_nm"] = round(dist, 1)
        gates.append(state)
    return {"gates": gates}
```
Confirm `_haversine_nm`, `GATES`, `_parse_dt_arg`, `_gate_current_state` are all in scope (same module).

### Step 6: Run to verify pass

Run: `uv run pytest tests/test_currents_near.py -q`
Expected: PASS (4 tests).

### Step 7: Register the tool + dispatch + TOOL_NAMES

In `src/currents_mcp/server.py`:

(a) `TOOL_NAMES` — add `"currents_near"`:
```python
TOOL_NAMES = ["plan_passage", "get_gate_current", "currents_near", "list_gates", "get_tide_heights"]
```
(b) Import it: ensure the `from currents_mcp.tools import ...` line includes `currents_near`.
(c) `dispatch` — add a branch (after the `get_gate_current` branch):
```python
    if name == "currents_near":
        return await currents_near(
            currents, lat=args["lat"], lon=args["lon"],
            radius_nm=args.get("radius_nm", 15.0),
        )
```
(d) `_list_tools` — add the Tool (after the `get_gate_current` entry):
```python
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
```

### Step 8: Re-disambiguate the other four descriptions

In `_list_tools`, append a `currents_near` cross-reference to each existing description (keep the rest verbatim):

- `get_gate_current` description — append after "…use list_gates instead.":
  ` Do NOT use this for the current near your position when you have no gate name — use currents_near instead.`
- `plan_passage` description — append after "…use list_gates instead.":
  ` For the current near your position right now, use currents_near or get_gate_current.`
- `list_gates` description — append after "…use plan_passage instead.":
  ` For the current near a position, use currents_near.`
- `get_tide_heights` description — append at the end:
  ` This is water LEVEL (high/low water), not current movement — for current speed/direction use get_gate_current (named pass) or currents_near (near a position).`

### Step 9: Update the server tests

In `tests/test_server.py`:
(a) `test_tool_names` — update the expected list:
```python
    assert TOOL_NAMES == ["plan_passage", "get_gate_current", "currents_near", "list_gates", "get_tide_heights"]
```
(b) `test_tool_descriptions_disambiguate` — add assertions (after the existing ones), so the regression guard covers the 5th tool:
```python
    cn = descs["currents_near"]
    assert "get_gate_current" in cn and "plan_passage" in cn  # names its siblings
    assert "currents_near" in descs["get_gate_current"]       # gate tool points to near
    assert "currents_near" in descs["list_gates"]
```

### Step 10: Full suite + commit

Run: `uv run pytest -q`
Expected: PASS (existing + new; if a description-substring assertion is too strict for the exact wording, align the assertion to the wording you shipped — don't weaken the "names its siblings" intent).
```bash
git add src/currents_mcp/tools.py src/currents_mcp/server.py tests/test_currents_near.py tests/test_server.py
git commit -m "feat: currents_near (nearest gates to a position) + re-disambiguate currents tools"
```

---

## Task 2: naturali-agents consumers

Work on branch `feat/currents-near` in `~/src/sailingnaturali/naturali-agents` (`git checkout -b feat/currents-near`). Requires Task 1 merged to currents-mcp main first (the benchmark + agent run currents-mcp from its checkout). Do not merge; controller merges after review.

### Step 1: Navigator prompt — tool + instruction

In `naturali-agents/skills/navigator/body.md`, add to the "Available MCP tools" list (after the existing `mcp_currents`/tide lines):
```
- `mcp_currents_near(lat, lon, radius_nm?)` — the current near a position: nearest tidal gate(s) with slack windows + flood/ebb set. Use for "what are the currents doing near us?" / "current near here" (read position first). For a SPECIFIC named pass use `mcp_currents_get_gate_current`.
```
And in the currents/passage guidance area, add: "For 'what are the currents doing near us?' read `mcp_signalk_read_sensor("navigation.position")` then call `mcp_currents_near(lat, lon)`; for a named pass use `mcp_currents_get_gate_current`."

(Note: body.md uses the `mcp_currents_*` / `mcp_tide_*` logical prefix that the assembly modernizes to `mcp__currents__*`; match whatever prefix the existing currents lines in body.md use.)

### Step 2: Regenerate the prompt mirror + tests

Run (from naturali-agents): `bash scripts/deploy-navigator.sh >/dev/null 2>&1 && grep -q "currents_near" prompts/navigator.md && echo "mirror ok" && uv run pytest -q`
Expected: `mirror ok`, full suite PASS.

### Step 3: Benchmark golden — currents-nearby → currents_near

In `naturali-agents/poseidon/bench/golden_asks.json`, the `currents-nearby` object: change `expected_tools` to `["mcp__currents__currents_near"]` and add `"expected_tools_flat": ["mcp__currents__currents_near"]`. (`current-boundary` stays on `get_gate_current`.)

Run: `uv run pytest tests/test_bench_golden.py -q`
Expected: PASS.

### Step 4: Commit

```bash
git add skills/navigator/body.md prompts/navigator.md poseidon/bench/golden_asks.json
git commit -m "feat(bench/agent): route 'currents near us' to currents_near"
```

---

## Task 3: Re-measure + ADR (controller, live)

Requires Task 1 merged to currents-mcp `main` and Task 2 merged to naturali-agents `main` (the benchmark runs both from their checkouts).

- [ ] **Step 1: Confirm currents_near is live in the benchmark's currents-mcp**

Run (from naturali-agents): `uv run --project ~/src/sailingnaturali/currents-mcp python -c "from currents_mcp.server import TOOL_NAMES; print('currents_near' in TOOL_NAMES)"`
Expected: `True`.

- [ ] **Step 2: Re-run the local sweep**

Prereqs: Ollama up + Pi SignalK reachable. Run: `for m in qwen3.6:latest llama3.1:latest qwen3.5:latest hermes3:8b; do echo "### $m"; uv run python -m poseidon.bench --backend openai --model "$m" 2>&1 | grep -E "model=|correctness"; done`
Inspect `currents-nearby` per model: `python3 -c "import json,glob,os; [print(os.path.basename(p), [x for x in json.load(open(p))['per_ask'] if x['id']=='currents-nearby'][0]['observed']) for p in sorted(glob.glob('dev/bench-results/*.json'), key=os.path.getmtime)[-4:]]"`. Expectation: models now call `mcp__currents__currents_near` → `currents-nearby` flips ✓ for the previously-failing models (qwen3.5, hermes3).

- [ ] **Step 3: Commit artifacts + ADR note**

```bash
cd ~/src/sailingnaturali/naturali-agents && git add dev/bench-results/ && git commit -m "bench: re-run currents-nearby as the composed currents_near tool" && git push
```
Add a short subsection to `planning/docs/adr/0002-model-strategy.md` § Benchmark results: the `currents-nearby` before/after per model, noting this is the same composed-tool lever as assess_anchorage applied to currents (the 4th surface fix). Commit + push.

---

## Self-Review

**Spec coverage:** shared `_gate_current_state` helper (free refactor, get_gate_current correct) → T1 S1–S2; `currents_near` nearest-≤3 / radius-15 / honest-empty → T1 S3–S6; register + dispatch + TOOL_NAMES → T1 S7; re-disambiguate all 5 descriptions + test → T1 S8–S9; navigator prompt + golden re-annotation → T2; re-measure + ADR → T3. ✓

**Placeholder scan:** No TBD/TODO; complete code for the helper, `currents_near`, the Tool registration, the description appends, and real tests using the existing `_currents(payload)` fake; every run step has command + expected. ✓

**Type/consistency:** `_gate_current_state(currents, gate, after)` defined in T1 S1 is called by both `get_gate_current` and `currents_near` (S5). `currents_near(currents, lat, lon, radius_nm=15.0)` signature matches the dispatch call (S7) and the tests (S3). `_haversine_nm`/`GATES`/`_parse_dt_arg` are confirmed present in tools.py. `TOOL_NAMES` list updated consistently in server (S7) and the test (S9). Golden tool name `mcp__currents__currents_near` (T2) matches the registered `currents_near`. ✓

**Sequencing:** T1 merges to currents-mcp main before T2/T3 (benchmark/agent run currents-mcp from its checkout); T2 merges before T3's live run. Flagged in each task.
