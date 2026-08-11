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


def test_repo_index_extracts_multilanguage_structure_and_references(tmp_path):
    (tmp_path / "Service.java").write_text(
        "import java.util.List;\npublic class Service {\n  public String run() { return helper(); }\n  private String helper() { return \"ok\"; }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "worker.ts").write_text(
        "import { api } from './api';\nexport function run() { return api(); }\nconst helper = () => run();\n",
        encoding="utf-8",
    )
    index = RepoIndex(tmp_path)

    java = index.file_outline("Service.java")
    typescript = index.file_outline("worker.ts")
    references = index.find_references("run", ".")

    assert java["language"] == "java"
    assert {item["name"] for item in java["symbols"]} >= {"Service", "run", "helper"}
    assert java["imports"] == ["java.util.List"]
    assert typescript["language"] == "typescript"
    assert {item["name"] for item in typescript["symbols"]} >= {"run", "helper"}
    assert any(item["kind"] == "token" and item["path"] == "worker.ts" for item in references["results"])


def test_repo_index_persists_and_invalidates_file_records(tmp_path):
    source = tmp_path / "service.py"
    source.write_text("def before():\n    return 1\n", encoding="utf-8")
    first = RepoIndex(tmp_path)
    first.refresh(".")

    second = RepoIndex(tmp_path)
    cached = second.refresh(".")
    assert cached["files_reused"] == 1
    assert (tmp_path / ".pico" / "index" / "repo-index-v2.json").is_file()

    source.write_text("def after():\n    return 2\n", encoding="utf-8")
    refreshed = second.refresh(".")
    assert refreshed["files_reused"] == 0
    assert second.find_symbol("after")["count"] == 1
