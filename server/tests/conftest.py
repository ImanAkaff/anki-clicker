"""
Test setup. pytest runs this before any test file.

We point the app at a throwaway SQLite file and set a known admin token
BEFORE the app is imported, because app/config.py reads the environment
at import time.
"""

import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
# Default: a throwaway SQLite file. To run the same tests against Postgres
# (what production uses), set TEST_DATABASE_URL to an EMPTY test database:
#   TEST_DATABASE_URL=postgresql://user:pass@localhost/clicker_test pytest
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["ADMIN_TOKEN"] = "test-admin"

# Let tests `import app` when pytest is run from the server/ folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    # TestClient calls the app directly in-process: no real network, but it
    # behaves exactly like real HTTP requests. `with` runs the startup code.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def user_token(client):
    r = client.post(
        "/v1/admin/users",
        json={"name": "test friend"},
        headers={"Authorization": "Bearer test-admin"},
    )
    assert r.status_code == 201
    return r.json()["token"]
