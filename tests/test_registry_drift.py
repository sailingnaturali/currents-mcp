"""Catch the vendored station registry drifting from station-corrections.

Station identity is owned by @sailingnaturali/station-corrections, which publishes
data/registry.json as a plain JSON artifact for non-JavaScript consumers. We vendor a
copy so the server runs standalone; when that repo is checked out alongside (a dev box,
not CI), this asserts the copy still matches.

It cannot see staleness against npm — only against a sibling checkout.
"""

import json
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[1] / "src" / "currents_mcp" / "_registry.json"
REAL = Path(__file__).resolve().parents[2] / "station-corrections" / "data" / "registry.json"


@pytest.mark.skipif(not REAL.is_file(), reason="sibling station-corrections not present")
def test_bundle_matches_published_registry():
    assert json.loads(BUNDLE.read_text()) == json.loads(REAL.read_text()), (
        "vendored _registry.json drifted from station-corrections/data/registry.json"
    )
