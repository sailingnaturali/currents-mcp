"""Catch the committed test fixture drifting from the real currents-vault.

The suite runs against tests/fixtures/vault (self-contained CI); this test — when
the sibling currents-vault is present — asserts the fixture still matches it, so a
vault edit that isn't mirrored into the fixture fails here instead of silently
testing stale data."""

from pathlib import Path

import pytest

from currents_mcp.passages import _load

FIXTURE = Path(__file__).parent / "fixtures" / "vault"
REAL = Path(__file__).resolve().parents[2] / "currents-vault"


@pytest.mark.skipif(not (REAL / "destinations.yaml").is_file(),
                    reason="sibling currents-vault not present")
def test_fixture_matches_real_vault():
    fix_gates, fix_pass = _load(FIXTURE)
    real_gates, real_pass = _load(REAL)
    assert fix_gates == real_gates, "fixture passes/ drifted from currents-vault"
    assert fix_pass == real_pass, "fixture destinations.yaml drifted from currents-vault"
