"""Test setup: point the app at a municipality profile and a throwaway JWT secret
before any app module is imported. No network; tests that need tables ask for
the db_schema fixture."""
import os
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

os.environ.setdefault("DEPLOYMENT", "us/ny/nassau/long-beach")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/civiclens_test")
os.environ.setdefault("STORAGE_BACKEND", "local")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def db_schema():
    """Create the schema on demand. Tests that touch the database (including
    through TestClient, which doesn't run startup handlers) request this
    instead of relying on another test having run init_db() first."""
    from database import init_db

    init_db()
