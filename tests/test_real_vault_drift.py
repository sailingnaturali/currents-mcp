"""Catch the bundled vault snapshot drifting from the real currents-vault.

The server ships a snapshot at src/currents_mcp/_vault so it runs standalone; the
canonical, editable copy is the currents-vault repo. When that repo is checked out
alongside (a dev box, not CI), this asserts the bundle still matches it, so a vault
edit that isn't mirrored into the bundle fails here instead of shipping stale."""

from pathlib import Path

import pytest

from currents_mcp.passages import _load

BUNDLE = Path(__file__).resolve().parents[1] / "src" / "currents_mcp" / "_vault"
REAL = Path(__file__).resolve().parents[2] / "currents-vault"


@pytest.mark.skipif(not (REAL / "destinations.yaml").is_file(),
                    reason="sibling currents-vault not present")
def test_bundle_matches_real_vault():
    bundle_gates, bundle_pass = _load(BUNDLE)
    real_gates, real_pass = _load(REAL)
    assert bundle_gates == real_gates, "bundled _vault passes/ drifted from currents-vault"
    assert bundle_pass == real_pass, "bundled _vault destinations.yaml drifted from currents-vault"
