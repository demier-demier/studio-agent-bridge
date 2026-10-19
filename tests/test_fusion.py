from pathlib import Path

import pytest

from studio_agent_bridge.adapters import fusion


def test_fusion_rejects_remote_endpoint():
    for endpoint in [
        "https://example.com/mcp",
        "http://127.0.0.1.evil.test/mcp",
        "http://user:secret@localhost/mcp",
    ]:
        with pytest.raises(ValueError):
            fusion._endpoint({"fusion_url": endpoint})


def test_fusion_input_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "private.f3d"
    outside.write_text("private")
    (workspace / "escape.f3d").symlink_to(outside)
    with pytest.raises(ValueError):
        fusion._path(workspace, "escape.f3d", ".f3d")


def test_script_access_requires_explicit_configuration(tmp_path):
    result = fusion.execute(
        tmp_path, {"action": "run_script", "parameters": {}}, tmp_path / "run", {}
    )
    assert result["status"] == "blocked"
    assert not result["artifacts"]


def test_inspection_never_falls_back_to_active_document(tmp_path, monkeypatch):
    def unexpected(*args):
        raise AssertionError("Should never connect without an input")

    monkeypatch.setattr(fusion, "FusionClient", unexpected)
    result = fusion.execute(tmp_path, {"action": "inspect", "parameters": {}}, tmp_path / "run", {})
    assert result["status"] == "failed"
    assert "input_design is required" in result["summary"]


def test_missing_evidence_is_not_success(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, config):
            pass

        def script(self, script):
            return [{"success": True}]

    monkeypatch.setattr(fusion, "FusionClient", FakeClient)
    result = fusion.execute(
        tmp_path, {"action": "create_demo", "parameters": {}}, tmp_path / "run", {}
    )
    assert result["status"] == "failed"
    assert "without native geometry evidence" in result["summary"]


def test_transport_timeout_propagates_for_reconciliation(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, config):
            pass

        def script(self, script):
            raise TimeoutError("native host may still be running")

    monkeypatch.setattr(fusion, "FusionClient", FakeClient)
    with pytest.raises(RuntimeError, match="outcome is uncertain"):
        fusion.execute(tmp_path, {"action": "create_demo", "parameters": {}}, tmp_path / "run", {})


def test_fusion_failure_payload_does_not_become_success():
    client = object.__new__(fusion.FusionClient)
    client.rpc = lambda *args: {
        "content": [{"type": "text", "text": '{"success":false,"error":"native API failed"}'}]
    }
    with pytest.raises(RuntimeError, match="native API failed"):
        client.call("fusion_mcp_execute", {})


def test_source_contains_explicit_native_entrypoint():
    # Packaging guard: a missing host script would break installed wheels.
    runner = Path(fusion.__file__).parents[1] / "host_scripts/fusion/runner.py"
    compile(runner.read_text(), str(runner), "exec")
    assert "def run(context):" in runner.read_text()


def test_stale_native_evidence_cannot_be_reused(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "fusion-evidence.json").write_text('{"body_count":8}')
    result = fusion.execute(tmp_path, {"action": "create_demo", "parameters": {}}, run, {})
    assert result["status"] == "failed"
    assert "stale evidence" in result["summary"]


def test_source_archive_change_is_detected(tmp_path, monkeypatch):
    source = tmp_path / "source.f3d"
    source.write_text("original")

    class FakeClient:
        def __init__(self, config):
            pass

        def script(self, script):
            source.write_text("changed by a trusted script")
            return [{"success": True}]

    monkeypatch.setattr(fusion, "FusionClient", FakeClient)
    result = fusion.execute(
        tmp_path,
        {"action": "inspect", "parameters": {"input_design": "source.f3d"}},
        tmp_path / "run",
        {},
    )
    assert result["status"] == "failed"
    assert "source archive changed" in result["summary"]
