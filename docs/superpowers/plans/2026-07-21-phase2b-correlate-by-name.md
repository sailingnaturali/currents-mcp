# Phase 2B: Correlate currents by name, drop the vendored id Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `currents-mcp` correlate SignalK current readings to gates by station **name** instead of by the CHS/NOAA station id, then re-vendor the id-less `station-corrections` registry — so `currents-mcp` no longer distributes any provider-minted identifier.

**Architecture:** `currents-mcp` reads pre-computed currents from the `signalk-currents` plugin's `/resources/currents` resource. That payload carries both `stationId` and `label` per station, and the `label` equals the registry `name`. Today `currents_source` caches events keyed by `stationId` and `passages` builds `Gate.station_id` from the registry's `providerId`, joining the two by id. This plan switches the join to `label`↔`name` (normalized), removes `Gate.station_id`/`Gate.noaa_bin`, stops reading `providerId`/`providerBin`, and re-vendors the `_registry.json` copy from the now id-less `station-corrections@2.0.0`.

**Tech Stack:** Python, `uv run pytest` (asyncio auto mode). The SignalK payload shape: `{"stations": [{"stationId": str, "label": str, "events": [...], "floodDir"?, "ebbDir"?, ...}]}`.

## Global Constraints

- **Run tests with `uv run pytest`** (bare `pytest`/`python -m pytest` misses the venv and errors on collection). Baseline before this plan: **80 passed, 1 failed** — the 1 failure is `tests/test_registry_drift.py`, an EXPECTED red because Phase 2A already dropped `providerId` from `station-corrections/data/registry.json` while the vendored copy still has it. Task 2 fixes it.
- **After this plan, no `providerId`, `providerBin`, `station_id`, `noaa_bin`, or `stationId`-keyed correlation may remain in `src/currents_mcp/`** (the payload dict may still *contain* a `stationId` field we ignore — we just must not key on it). Verify with `grep -rn -e providerId -e providerBin -e station_id -e noaa_bin src/currents_mcp`.
- The correlation join is `label`↔`name`, normalized with `strip().casefold()` on both sides so a case/whitespace difference between the plugin's label and the registry name does not silently drop a station.
- `Gate` keeps `name`, `provider`, `latitude`, `longitude`, `transit_window_minutes`, `hazards`, `key`. It loses `station_id` and `noaa_bin`.
- The vendored `_registry.json` must end **byte-identical** to `station-corrections/data/registry.json` (the drift test asserts `==`).

---

### Task 1: Correlate by name instead of station id

**Files:**
- Modify: `src/currents_mcp/currents_source.py` (cache/dirs keyed by normalized label; `events_for_station`/`dirs_for_station` take a name)
- Modify: `src/currents_mcp/passages.py` (`Gate` drops `station_id`/`noaa_bin`; `_gate_from` stops reading `providerId`/`providerBin`)
- Modify: `src/currents_mcp/tools.py` (call `events_for_station(gate.name)` / `dirs_for_station(gate.name)`)
- Modify: the tests that reference `station_id`/`noaa_bin`/`providerId` or call `events_for_station`/`dirs_for_station` with an id: `tests/test_currents_source.py`, `tests/test_passages.py`, `tests/test_get_gate_current.py`, `tests/test_plan_passage.py`, `tests/test_currents_near.py`, `tests/test_server.py`.

**Interfaces:**
- `CurrentsSource.events_for_station(name: str)` and `dirs_for_station(name: str)` — now take a station display name; internally normalized.
- `Gate` no longer has `station_id` or `noaa_bin`.

- [ ] **Step 1: Add the normalizer and re-key the cache in `currents_source.py`**

At module level (near the top, after imports), add:
```python
def _norm(name: str) -> str:
    """Join key for correlating a gate to a plugin reading: fold case and trim
    so a label/name that differs only in casing or spacing still matches."""
    return name.strip().casefold()
```

In `_load`, change the per-station loop to key on the label, not the stationId:
```python
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
```

Change the two accessors to take and normalize a name:
```python
    async def events_for_station(self, name: str) -> list[CurrentEvent]:
        return (await self._load()).get(_norm(name), [])

    async def dirs_for_station(self, name: str) -> dict:
        await self._load()
        return self._dirs.get(_norm(name), {})
```
Also update the docstring on `_load` (line ~41) that says "stationId -> events" to "station name -> events".

- [ ] **Step 2: Drop the id fields in `passages.py`**

In the `Gate` dataclass, delete these two fields:
```python
    station_id: str
```
and
```python
    noaa_bin: int | None = None
```
In `_gate_from`, delete the two lines in the `Gate(...)` constructor that set them:
```python
            station_id=str(station["providerId"]),
```
and
```python
            noaa_bin=station.get("providerBin"),
```
Leave `name=station["name"]` and everything else. Update the module docstring line that says "A gate's name, position, provider and provider station id come from the station registry" to drop "and provider station id".

- [ ] **Step 3: Update the call sites in `tools.py`**

Both call sites of each (there are two of each, around lines 194/199 and 286/291): change
`events_for_station(gate.station_id)` → `events_for_station(gate.name)` and
`dirs_for_station(gate.station_id)` → `dirs_for_station(gate.name)`.

- [ ] **Step 4: Update the tests**

The test payloads already include `label` (e.g. `{"stationId": "63aef…", "label": "Dodd Narrows", …}`), so correlation still resolves once it keys on the label. Update each referenced test:
- Anywhere a test constructs `Gate(..., station_id=..., noaa_bin=...)`, remove those two arguments.
- Anywhere a test calls `events_for_station(<id>)` / `dirs_for_station(<id>)` directly, pass the station **name** instead (e.g. `events_for_station("Dodd Narrows")`).
- Payload dicts may keep their `stationId` field (now ignored) — no need to remove it; but they MUST have a `label`. Confirm each payload used for correlation has a `label` matching the gate name (they do today).
- Do NOT touch `test_registry_drift.py` — that is Task 2.
- If a test asserts on `gate.station_id`/`gate.noaa_bin`, drop or rewrite that assertion (those attributes no longer exist).

Add one focused test in `tests/test_currents_source.py` proving the normalization join: a payload whose `label` differs only in case/whitespace from the queried name (e.g. label `"Dodd Narrows"`, query `" dodd narrows "`) still returns the events.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q`
Expected: everything passes EXCEPT `tests/test_registry_drift.py`, which stays red (the vendored `_registry.json` still has `providerId`; Task 2 re-vendors it). Confirm no OTHER test fails.

- [ ] **Step 6: Confirm no id-keyed correlation remains in source**

Run: `grep -rn -e providerId -e providerBin -e station_id -e noaa_bin src/currents_mcp`
Expected: no matches (a `stationId` field read only for a skip-warning message is acceptable; nothing may *key* on it).

- [ ] **Step 7: Commit**

```bash
git add src/currents_mcp/currents_source.py src/currents_mcp/passages.py src/currents_mcp/tools.py tests/
git commit -m "feat!: correlate currents to gates by name, not station id"
```
(End the message with the workspace trailers — see the dispatch.)

---

### Task 2: Re-vendor the id-less registry

**Files:**
- Overwrite: `src/currents_mcp/_registry.json` (copy of `station-corrections/data/registry.json`)

- [ ] **Step 1: Re-vendor**

Copy the published registry from the sibling checkout verbatim:
```bash
cp ../station-corrections/data/registry.json src/currents_mcp/_registry.json
```

- [ ] **Step 2: Confirm it dropped the ids and matches the source**

Run: `grep -c -e providerId -e providerBin src/currents_mcp/_registry.json`
Expected: 0.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: ALL pass, including `tests/test_registry_drift.py` (the vendored copy now byte-matches `station-corrections/data/registry.json`).

- [ ] **Step 4: Commit**

```bash
git add src/currents_mcp/_registry.json
git commit -m "chore: re-vendor id-less registry from station-corrections@2.0.0"
```

---

## Self-Review

**Spec coverage:** correlation swap (Task 1: currents_source keys by normalized label; passages drops the id fields; tools passes the name); tests updated + a normalization test added; re-vendor (Task 2) turns the drift test green. Every referenced source and test file from the grounding grep is covered.

**Type/interface consistency:** `events_for_station(name)`/`dirs_for_station(name)` renamed param, same return types. `Gate` loses two fields; `_gate_from` and every `Gate(...)` construction in tests updated in lockstep. `_norm` applied symmetrically on cache-build and lookup.

**Expected intermediate state:** after Task 1 the suite is green except `test_registry_drift.py` (documented); Task 2 closes it. This mirrors the Phase-2A→2B handoff.

**Out of scope:** `signalk-currents` (Phase 3) — it already emits `label == name`, which is what makes this correlation work; do not modify it here. The plugin also holds its own CHS ids in its config/defaults, which is a separate phase.
