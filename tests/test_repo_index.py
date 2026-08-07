import pytest

from pico.repo_index import RepoIndex


def build_repo(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from .service import Worker\n", encoding="utf-8")
    (package / "service.py").write_text(
        "from pathlib import Path\n\n"
        "class Worker:\n"
        "    def run(self, value):\n"
        "        return value + 1\n\n"
        "def build_worker():\n"
        "    return Worker()\n",
        encoding="utf-8",
    )
    (package / "consumer.py").write_text(
        "from .service import Worker\n\n"
        "def consume(worker: Worker):\n"
        "    return worker.run(1)\n",
        encoding="utf-8",
    )
    return package


def test_repo_index_builds_outline_symbols_and_imports(tmp_path):
    package = build_repo(tmp_path)
    index = RepoIndex(tmp_path)

    outline = index.file_outline("pkg/service.py")

    assert outline["path"] == "pkg/service.py"
    assert [item["qualified_name"] for item in outline["symbols"]] == [
        "Worker",
        "Worker.run",
        "build_worker",
    ]
    assert outline["imports"] == ["pathlib"]
    assert package.exists()


def test_repo_index_finds_symbols_references_and_internal_dependencies(tmp_path):
    build_repo(tmp_path)
    index = RepoIndex(tmp_path)

    symbol = index.find_symbol("Worker", "pkg")
    references = index.find_references("Worker", "pkg")
    graph = index.dependency_graph("pkg")

    assert {item["path"] for item in symbol["results"]} == {"pkg/service.py"}
    assert any(item["kind"] == "name" and item["path"] == "pkg/consumer.py" for item in references["results"])
    assert {edge["to"] for edge in graph["edges"]} == {"pkg/service.py"}
    assert any(item["path"] == "pkg/consumer.py" for item in graph["files"])


def test_repo_index_reuses_unchanged_files_and_reports_syntax_diagnostics(tmp_path):
    build_repo(tmp_path)
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    index = RepoIndex(tmp_path)

    first = index.refresh(".")
    second = index.refresh(".")
    outline = index.file_outline("broken.py")

    assert first["files_indexed"] == 4
    assert second["files_reused"] == 4
    assert outline["symbols"] == []
    assert "syntax error" in outline["diagnostics"][0]


def test_repo_index_rejects_paths_outside_workspace(tmp_path):
    index = RepoIndex(tmp_path)

    with pytest.raises(ValueError, match="path escapes workspace"):
        index.file_outline(tmp_path.parent / "outside.py")
