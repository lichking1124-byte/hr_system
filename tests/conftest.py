"""Test setup: runs the real Flask app against a real PostgreSQL.

By default an embedded Postgres is started with `pgserver` (pip install pgserver).
To use your own database instead, set TEST_DATABASE_URL (use a throw-away database!).
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

_server = None
uri = os.environ.get("TEST_DATABASE_URL")
if not uri:
    import pgserver
    _server = pgserver.get_server(tempfile.mkdtemp())
    uri = _server.get_uri()

os.environ["DATABASE_URL"] = uri
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test")

import app as app_module  # noqa: E402  (imports run init_db() against the test database)
import database  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    if _server is not None:
        _server.cleanup()


@pytest.fixture(scope="session")
def flask_app():
    app_module.app.config["TESTING"] = True
    return app_module.app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture(scope="session")
def db():
    return database.get_db
