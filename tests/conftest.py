from __future__ import annotations

import os
import tempfile

_TEST_HOME = tempfile.mkdtemp(prefix="pbi_orch_test_home_")
os.environ["PBI_ORCHESTRATOR_HOME"] = _TEST_HOME
