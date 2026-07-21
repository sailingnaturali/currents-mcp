# Resolve gate identity from the station registry

**Date:** 2026-07-21
**Status:** Approved design, not yet implemented
**Scope:** Phase 3b of 4 — `currents-vault` and `currents-mcp`, one coordinated change

## Problem

A tidal gate's identity — display name, position, provider, provider's station id, and for NOAA
stations a depth bin — is written in `currents-vault/passes/*.md` frontmatter:

```yaml
name: Dodd Narrows
provider: chs
station_id: 63aef1866a2b9417c035030f
latitude: 49.1344
longitude: -123.8171
transit_window_minutes: 30
hazards: [...]
```

All twenty of those gates now also exist in `@sailingnaturali/station-corrections`'
registry (v1.5.0), which is the agreed source of truth for station identity across the
workspace. Phases 1 and 2 moved `station-corrections` and `chs-constituents` onto it. The vault
is the last writer still holding its own copy.

Only two of those frontmatter fields are the vault's own knowledge: `transit_window_minutes` and
`hazards`. The rest is duplicated reference data.

## Goals

- The vault holds **passage knowledge** — hazards, transit windows, route notes — and references
  station identity rather than restating it.
- `currents-mcp` resolves identity through the registry, so a curated name or position is written
  once.
- Routing stops depending on display names, so a rename cannot require synchronised edits in two
  repositories.

## Non-goals

- **No change to what the MCP tools accept from a user.** People say "Dodd Narrows", not
  `chs-dodd-narrows`. Registry keys are internal plumbing and must not surface in tool arguments
  or in text shown to a person. See "The naming trap" below — this is the easiest thing to break.
- **No change to hazard or destination content.** This is a data-shape migration.
- **No new fetching.** `currents_source.py` already delegates current data elsewhere.

## Design

### Vault frontmatter references the registry

```yaml
station: chs-dodd-narrows
transit_window_minutes: 30
hazards:
  - state: flood
    text: >-
      Tide rips form off the north entrance, and the pass is harder to enter
      from the north.
```

`name`, `provider`, `station_id`, `latitude`, `longitude` and `noaa_bin` are removed. `station` is
the registry key, which is stable by construction — unlike a display name, it does not move when
someone improves the wording.

### `destinations.yaml` routes by key

```yaml
- destination: Nanaimo
  aliases: [nanaimo, newcastle island]
  gates: [chs-dodd-narrows]
  route_note: Protected inside route; Dodd is the final gate.
```

Today this file routes by display name, and `_load` raises if a name does not resolve
(`passages.py`, pinned by a test added 2026-07-21). That guard works, but it enforces a coupling
rather than removing one: the name appears in two files that must be edited together, and after
this change those two files would live in different repositories.

Keying on the registry key makes the coupling structural — a display name becomes presentation
only, and renaming one is a single-file edit in `station-corrections` with nothing downstream to
synchronise.

### `currents-mcp` resolves through a vendored registry

`Gate` keeps its shape; the fields simply come from a different place:

| Field | Before | After |
|---|---|---|
| `name`, `latitude`, `longitude`, `provider`, `station_id`, `noaa_bin` | vault frontmatter | registry, via `station` key |
| `transit_window_minutes`, `hazards` | vault frontmatter | unchanged |
| *new* `key` | — | the registry key |

`noaa_bin` maps from the registry's `providerBin`, added in station-corrections v1.5.0 for
Boundary Pass (`noaa/PUG1717`, bin 35).

**`GATES` is rekeyed from display name to registry key.** This removes a latent failure the
current code documents but cannot fix: gate names are not unique on this coast — Juan de Fuca and
Johnstone Strait each have a Race Passage — and `_load` currently has to raise on a duplicate name
because the dict would silently drop one. Registry keys are unique by construction, so two gates
sharing a display name become representable.

### The naming trap

Rekeying `GATES` breaks user-facing text in a way tests will not catch, because the strings are
still well-formed. Every read of `GATES.keys()` intended for a human must become `gate.name`:

- `tools.py` builds `"Known gates: " + ", ".join(GATES.keys())`. Rekeyed, that reads
  *"Known gates: chs-dodd-narrows, chs-active-pass, …"* — slugs spoken at a person.
- `tools.py` looks up `GATES[gname]` from `passage.gate_names`; those become keys, so the lookup
  still works, but any nearby display use of `gname` does not.

`find_gate(name)` stays a case-insensitive **display-name** lookup — that is the user-facing
entry point and its contract does not change. Because display names may now legitimately collide,
its behaviour on an ambiguous name must be decided and documented rather than left to iteration
order. Returning the first match silently is the current behaviour and is not good enough once
collisions are representable.

### Getting the registry into Python

`currents-mcp` already vendors the vault at `src/currents_mcp/_vault`, with `vault_path()`
preferring `CURRENTS_VAULT_PATH`, then `~/.currents-vault`, then the sibling repo, then the
bundle — and `test_real_vault_drift.py` failing when the bundle drifts from the real vault.

The registry follows the same pattern: vendor `registry.json` alongside the vault snapshot, with a
matching drift test that skips when the sibling `station-corrections` checkout is absent (CI) and
asserts equality when it is present (dev boxes).

`registry.json` is a plain JSON artifact published on the `./data/*` subpath precisely so
non-JavaScript consumers can read it without npm. Python reads it directly; no new dependency.

## Risks

**Two repositories, one change.** The vault cannot drop fields before `currents-mcp` can resolve
them, and `currents-mcp`'s drift test compares its bundle against the real vault — so a
half-applied migration fails loudly rather than silently, but it does fail. Sequence within the
implementation plan: teach `currents-mcp` to resolve from `station` while tolerating the old
fields, then change the vault, then remove the tolerance.

**A vault gate with no registry entry** must be a load error naming the key, not a skipped gate.
All twenty resolve today; the failure mode matters for the twenty-first.

**Registry staleness.** The vendored `registry.json` can lag the published package. The drift test
catches divergence from a sibling checkout but not from npm. Worth recording the registry version
in the bundle so a stale snapshot is visible.

## What this does not fix

`noaa_bin` has no consumer. It is read from frontmatter into `Gate` and asserted in one test;
no production code uses it, because `currents_source.py` delegates current fetching elsewhere.
Carrying it through from `providerBin` preserves the status quo and keeps the identity record
complete, but this migration does not make it load-bearing, and a reader should not assume it is.

## Follow-on

- **Phase 4** — whatever else consumes vault frontmatter directly. To be surveyed once this lands.
- `station-corrections` issue #8: `check-slugs` passes for a station absent from the slug lock, so
  a newly added slug enters the public API unguarded. Unrelated to this work, tracked separately.
