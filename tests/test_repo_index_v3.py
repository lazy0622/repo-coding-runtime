import json

from pico.repo_index import INDEX_SCHEMA_VERSION, RepoIndex


def build_call_graph_repo(tmp_path):
    package = tmp_path / "pkg"
    tests = tmp_path / "tests"
    package.mkdir()
    tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        "class Worker:\n"
        "    def run(self, value):\n"
        "        return normalize(value)\n\n"
        "def normalize(value):\n"
        "    return value.strip()\n",
        encoding="utf-8",
    )
    (package / "consumer.py").write_text(
        "from pkg.service import Worker\n\n"
        "def consume(value):\n"
        "    return Worker().run(value)\n",
        encoding="utf-8",
    )
    (package / "entrypoint.py").write_text(
        "from pkg.consumer import consume\n\n"
        "def handle(value):\n"
        "    return consume(value)\n",
        encoding="utf-8",
    )
    (tests / "test_service.py").write_text(
        "from pkg.service import Worker\n\n"
        "def test_worker():\n"
        "    return Worker().run('ok')\n",
        encoding="utf-8",
    )


def test_repo_index_v3_persists_call_records_and_resolves_unique_calls(tmp_path):
    build_call_graph_repo(tmp_path)
    index = RepoIndex(tmp_path)
    outline = index.file_outline("pkg/service.py")

    assert INDEX_SCHEMA_VERSION == "repo-index-v3"
    assert any(call["callee_text"] == "normalize" for call in outline["calls"])
    payload = json.loads(
        (tmp_path / ".pico" / "index" / "repo-index-v2.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == "repo-index-v3"
    assert any(record["calls"] for record in payload["records"])


def test_analyze_impact_returns_callers_callees_tests_and_confidence(tmp_path):
    build_call_graph_repo(tmp_path)
    index = RepoIndex(tmp_path)

    result = index.analyze_impact("Worker.run", path="pkg", depth=1)

    assert result["definitions"]
    assert any(item["path"] == "pkg/consumer.py" for item in result["direct_callers"])
    assert any(item["callee_text"] == "normalize" for item in result["direct_callees"])
    assert any(item["path"] == "tests/test_service.py" for item in result["related_tests"])
    assert 0.0 <= result["confidence"] <= 1.0
    assert any("conservative" in item for item in result["diagnostics"])

    bounded = index.analyze_impact("Worker.run", path="pkg", depth=2)
    assert any(item["path"] == "pkg/consumer.py" for item in bounded["direct_callers"])
    assert any(item["path"] == "pkg/entrypoint.py" for item in bounded["indirect_callers"])
    assert bounded["depth"] == 2


def test_analyze_impact_rejects_unbounded_depth_and_missing_target_is_explicit(tmp_path):
    build_call_graph_repo(tmp_path)
    index = RepoIndex(tmp_path)

    try:
        index.analyze_impact("Worker", depth=3)
    except ValueError as exc:
        assert "depth" in str(exc)
    else:
        raise AssertionError("depth > 2 should be rejected")

    result = index.analyze_impact("DoesNotExist")
    assert result["definitions"] == []
    assert result["confidence"] == 0.0
    assert any("not resolved" in item for item in result["diagnostics"])
