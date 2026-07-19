"""Pin the vault loader to the bundled snapshot before currents_mcp imports, so
the suite is deterministic on every machine (a dev box with ~/.currents-vault
must not silently test the live vault). The bundle is kept in sync with the real
currents-vault by test_real_vault_drift.py."""

import os
from pathlib import Path

_BUNDLE = Path(__file__).resolve().parents[1] / "src" / "currents_mcp" / "_vault"
os.environ["CURRENTS_VAULT_PATH"] = str(_BUNDLE)
