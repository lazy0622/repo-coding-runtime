import os
import subprocess
import sys
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pico.service.config import Repository, Settings
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


@pytest.fixture
def configured(tmp_path, store):
    source = tmp_path / "source"
    source.mkdir()
    (source / "calculator.py").write_text("def add(a,b):\n    return a-b\n")
    (source / ".gitignore").write_text(".pico/\n__pycache__/\n")
    for args in [
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ]:
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    count = tmp_path / "agent-count.txt"
    agent = tmp_path / "agent.py"
    agent.write_text(
        "from pathlib import Path\nimport sys\n"
        f'with Path({str(count)!r}).open("a") as h:h.write("executed\\n")\n'
        "root=Path(sys.argv[1])\n"
        'root.joinpath("calculator.py").write_text("def add(a,b):\\n    return a+b\\n")\n'
        'root.joinpath(".pico").mkdir(exist_ok=True)\n'
        'root.joinpath(".pico/private.txt").write_text("runtime-private")\n'
        'root.joinpath(".env").write_text("PRIVATE=do-not-publish")\n'
        'root.joinpath("notes.txt").write_text("new tracked artifact")\n',
        encoding="utf-8",
    )
    verify = tmp_path / "verify.py"
    verify.write_text(
        "import os,runpy,sys\nfrom pathlib import Path\n"
        'assert "RUNTIME_DATABASE_URL" not in os.environ\n'
        'assert "RUNTIME_API_TOKEN" not in os.environ\n'
        'assert "PICO_DEEPSEEK_API_KEY" not in os.environ\n'
        'assert not (Path(sys.argv[1])/".env").exists()\n'
        'assert not (Path(sys.argv[1])/".pico").exists()\n'
        'assert (Path(sys.argv[1])/"notes.txt").is_file()\n'
        'add=runpy.run_path(str(Path(sys.argv[1])/"calculator.py"))["add"]\n'
        'assert add(2,3)==5 and add(-2,3)==1\nprint("verification passed")\n',
        encoding="utf-8",
    )
    settings = Settings(
        store.engine.url,
        "t" * 32,
        tmp_path / "data",
        {
            "demo": Repository(
                source,
                [sys.executable, str(verify), "{workspace}"],
                [sys.executable, str(agent), "{workspace}"],
            )
        },
        lease_seconds=6,
        task_timeout=15,
        verify_timeout=15,
    )
    settings.data_dir.mkdir()
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    job = store.submit(
        "job", {"repository_id": "demo", "task": "Fix addition"}, base_commit=base
    )[0]
    return settings, job, count
