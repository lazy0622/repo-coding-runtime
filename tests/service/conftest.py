import os
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pico.service.store import Store


@pytest.fixture
def store():
    load_dotenv(".env.service", override=False)
    url = os.environ.get("RUNTIME_TEST_DATABASE_URL") or os.environ.get(
        "RUNTIME_DATABASE_URL"
    )
    if not url:
        pytest.skip("Set RUNTIME_TEST_DATABASE_URL to run real PostgreSQL tests")
    schema = "test_runtime_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    isolated = make_url(url).update_query_dict({"options": "-csearch_path=" + schema})
    instance = Store(isolated)
    instance.initialize()
    try:
        yield instance
    finally:
        instance.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
