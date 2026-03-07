from agent_cli.core.infra.logging.tracing import bind_trace, get_trace_fields, start_span


def test_bind_trace_and_span_lifecycle():
    assert get_trace_fields()["trace_id"] == ""

    with bind_trace(trace_id="trace-1", task_id="task-1"):
        fields = get_trace_fields()
        assert fields["trace_id"] == "trace-1"
        assert fields["task_id"] == "task-1"

        span = start_span("llm_call")
        in_span = get_trace_fields()
        assert in_span["span_type"] == "llm_call"
        assert in_span["span_id"] != ""

        finished = span.finish()
        assert finished["trace_id"] == "trace-1"
        assert finished["task_id"] == "task-1"
        assert finished["span_type"] == "llm_call"
        assert int(finished["duration_ms"]) >= 0

    after = get_trace_fields()
    assert after["trace_id"] == ""
    assert after["task_id"] == ""
    assert after["span_id"] == ""
