"""Pin the corpus for the test run: the unit tests build fake AAPL stores on the V0–V3 company
list, whatever TENK_COMPANIES the shell has. Tests on the real V4 store switch to companies.yaml
themselves (test_interfaces.py)."""

import os
from pathlib import Path

os.environ["TENK_COMPANIES"] = str(Path(__file__).resolve().parents[1] / "companies-v3.yaml")
