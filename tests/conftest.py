"""Shared test wiring: import the backend package and point config at a
throwaway SQLite database with seeded demo data.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

_TMP = Path(__file__).resolve().parent / "_tmp"
_TMP.mkdir(exist_ok=True)

DB_PATH = _TMP / "analytics_test.db"
HISTORY_PATH = _TMP / "history_test.db"

import pytest


def _ignore_local_env_file():
    """Never let a developer's ``backend/.env`` leak into the suite.

    ``Settings`` reads ``.env`` from the working directory, so a locally saved
    ``READER_DATABASE_URL`` would silently point the read-only engine at the demo
    warehouse and make tests pass or fail depending on an untracked file.
    """
    from app.config import Settings

    Settings.model_config = dict(Settings.model_config, env_file=None)


_ignore_local_env_file()


def _settings():
    from app.config import Settings

    return Settings(
        database_url=f"sqlite:///{DB_PATH}",
        reader_database_url=f"sqlite:///{DB_PATH}",
        history_db_url=f"sqlite:///{HISTORY_PATH}",
        llm_provider="heuristic",
        allowed_tables=["customers", "products", "orders", "order_items", "order_revenue"],
        hidden_columns=["customers.email"],
        max_rows=50,
        conversation_window=3,
    )


@pytest.fixture(scope="session", autouse=True)
def demo_database():
    DB_PATH.unlink(missing_ok=True)
    HISTORY_PATH.unlink(missing_ok=True)
    from scripts.init_demo_db import seed

    seed(f"sqlite:///{DB_PATH}", orders=120)
    yield f"sqlite:///{DB_PATH}"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, demo_database):
    """Resolve get_settings() to the temp database for every module that imports it.

    ``base_settings`` is the patched seam, never ``get_settings`` itself: the
    datasource tests activate and deactivate sources at runtime and depend on the
    real override logic.
    """
    resolved = _settings()
    import app.config as config

    config.set_active_settings(None)
    monkeypatch.setattr(config, "base_settings", lambda: resolved)
    config.get_settings.cache_clear()
    yield resolved
    config.set_active_settings(None)
    # The schema catalog and history engine are process-level caches keyed by URL.
    import app.agents.schema_agent as schema_agent
    import app.services.history_store as history_store

    schema_agent._catalog_cache.clear()
    history_store._engine_for.cache_clear()


@pytest.fixture
def settings():
    return _settings()
