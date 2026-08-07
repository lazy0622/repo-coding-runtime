import json

import pytest

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.patching import PatchError, PatchJournal, apply_unified_diff, preview_file_diff


PATCH = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return \"hi\"
+    return \"hello\"
"""


def test_preview_apply_and_rollback_unified_diff(tmp_path):
    path = tmp_path / "app.py"
    path.write_text('def greet():\n    return "hi"\n', encoding="utf-8")
    journal = PatchJournal(tmp_path)

    preview = preview_file_diff(tmp_path, "app.py", 'def greet():\n    return "hello"\n')
    result = apply_unified_diff(tmp_path, PATCH, journal=journal)

    assert preview["changed"] is True
    assert "-    return \"hi\"" in preview["diff"]
    assert result["status"] == "applied"
    assert path.read_text(encoding="utf-8") == 'def greet():\n    return "hello"\n'

    rollback = journal.rollback(result["backup_id"])

    assert rollback["status"] == "rolled_back"
    assert path.read_text(encoding="utf-8") == 'def greet():\n    return "hi"\n'


def test_unified_diff_refuses_context_mismatch_and_path_escape(tmp_path):
    path = tmp_path / "app.py"
    path.write_text('def greet():\n    return "different"\n', encoding="utf-8")

    with pytest.raises(PatchError, match="context mismatch"):
        apply_unified_diff(tmp_path, PATCH)

    escape = PATCH.replace("app.py", "../outside.py")
    with pytest.raises(PatchError, match="path escapes workspace"):
        apply_unified_diff(tmp_path, escape)


def test_rollback_refuses_files_changed_after_patch(tmp_path):
    path = tmp_path / "app.py"
    path.write_text('def greet():\n    return "hi"\n', encoding="utf-8")
    result = apply_unified_diff(tmp_path, PATCH)
    path.write_text('def greet():\n    return "user change"\n', encoding="utf-8")

    with pytest.raises(PatchError, match="file changed after patch"):
        PatchJournal(tmp_path).rollback(result["backup_id"])


def test_pico_gateway_exposes_index_and_patch_tools(tmp_path):
    (tmp_path / "app.py").write_text('def greet():\n    return "hi"\n', encoding="utf-8")
    agent = Pico(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
    )

    outline = agent.execute_tool("get_file_outline", {"path": "app.py"})
    applied = agent.execute_tool("apply_patch", {"patch": PATCH})
    data = json.loads(applied.content)
    rolled_back = agent.execute_tool("rollback_patch", {"backup_id": data["backup_id"]})

    assert outline.metadata["read_only"] is True
    assert "greet" in outline.content
    assert applied.metadata["workspace_changed"] is True
    assert json.loads(rolled_back.content)["status"] == "rolled_back"
