from pico.evaluation.tool_protocol import run_tool_protocol_benchmark


def test_tool_protocol_benchmark_is_provider_free_and_passes(tmp_path):
    artifact = run_tool_protocol_benchmark(tmp_path / "protocol.json")

    assert artifact["summary"]["failed"] == 0
    assert artifact["summary"]["pass_rate"] == 1.0
    assert artifact["metrics"]["network_calls"] == 0
    assert (tmp_path / "protocol.json").is_file()
