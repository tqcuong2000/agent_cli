import json
import logging

from agent_cli.core.config import AgentSettings
from agent_cli.core.logging import (
    JSONLineFormatter,
    configure_observability,
    sanitize_log_line,
)


def test_sanitize_log_line_redacts_secret_patterns():
    line = "Authorization: Bearer sk-ant-aaaaaaaaaaaaaaaaaaaa token=secret123"
    redacted = sanitize_log_line(line)
    assert "sk-ant-aaaaaaaaaaaaaaaaaaaa" not in redacted
    assert "secret123" not in redacted
    assert "REDACTED" in redacted


def test_json_formatter_outputs_structured_payload():
    logger = logging.getLogger("agent_cli.tests.logging")
    record = logger.makeRecord(
        name=logger.name,
        level=logging.INFO,
        fn="test_logging.py",
        lno=1,
        msg="hello",
        args=(),
        exc_info=None,
        extra={
            "source": "unit_test",
            "task_id": "task-123",
            "span_id": "span-1",
            "span_type": "llm_call",
            "data": {"k": "v"},
        },
    )
    payload = JSONLineFormatter().format(record)
    parsed = json.loads(payload)
    assert parsed["level"] == "INFO"
    assert parsed["source"] == "unit_test"
    assert parsed["task_id"] == "task-123"
    assert parsed["span_type"] == "llm_call"
    assert parsed["data"]["k"] == "v"


def test_observability_records_metrics_and_writes_summary(tmp_path):
    settings = AgentSettings(
        log_directory=str(tmp_path / "logs"),
        log_level="INFO",
        log_max_file_size_mb=1,
    )
    obs = configure_observability(settings)
    obs.record_task_created()
    obs.record_llm_call(
        task_id="task-a",
        model="gpt-4o",
        provider="openai",
        input_tokens=1000,
        output_tokens=500,
        duration_ms=12,
        cost_usd=0.01,
    )
    obs.record_tool_call(
        task_id="task-a",
        tool_name="read_file",
        success=True,
        duration_ms=4,
        result_length=120,
    )
    obs.record_task_result(is_success=True)
    obs.log_task_summary("task-a", is_success=True)
    obs.shutdown()

    assert obs.log_file.exists()
    assert obs.summary_file.exists()

    summary = json.loads(obs.summary_file.read_text(encoding="utf-8"))
    assert summary["llm_calls"] == 1
    assert summary["tokens"]["total"] == 1500
    assert summary["tasks"]["created"] == 1
    assert summary["tasks"]["succeeded"] == 1
