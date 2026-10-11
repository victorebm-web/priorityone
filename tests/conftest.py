"""Shared test setup: a throwaway SQLite database and a fixed "today", set before db.py is imported."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["PRIORITYONE_DB"] = _tmp.name
os.environ["PRIORITYONE_TODAY"] = "2026-10-08"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def fresh_db():
    """A freshly seeded database for each test module that asks for one."""
    from seed import seed
    seed(verbose=False)
    yield


def pytest_sessionfinish(session, exitstatus):
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass
