"""Catch the vendored station registry drifting from station-metadata.

Station identity is owned by @openwaters/station-metadata, which publishes
data/registry.json as a plain JSON artifact for non-JavaScript consumers. We vendor a
copy so the server runs standalone; when that repo is checked out on the same machine
(a dev box, not CI), this asserts the copy still matches.

It cannot see staleness against npm — only against a local checkout.
"""

import json
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[1] / "src" / "currents_mcp" / "_registry.json"
# station-metadata lives in the Open Waters org, so it is not a sibling of this repo.
REAL = Path.home() / "src" / "openwaters" / "station-metadata" / "data" / "registry.json"


@pytest.mark.skipif(not REAL.is_file(), reason="station-metadata checkout not present")
def test_bundle_matches_published_registry():
    assert json.loads(BUNDLE.read_text()) == json.loads(REAL.read_text()), (
        "vendored _registry.json drifted from station-metadata/data/registry.json"
    )
