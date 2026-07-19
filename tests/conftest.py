"""Point the vault loader at the committed fixture before currents_mcp imports,
so the test suite is self-contained in CI (no sibling currents-vault checkout).
The fixture is kept in sync with the real vault by test_real_vault_drift.py."""

import os
from pathlib import Path

os.environ["CURRENTS_VAULT_PATH"] = str(Path(__file__).parent / "fixtures" / "vault")
