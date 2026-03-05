"""Tests for JSON protocol models introduced in Phase 1."""

from agent_cli.core.runtime.agents.protocol import (
    AgentPromptResponse,
    CompletionPayload,
    DecisionPayload,
    DecisionType,
    MessageEnvelope,
    ProtocolMessageType,
    ToolCallPayload,
)


def test_decision_payload_validation():
    action = DecisionPayload(
        type=DecisionType.EXECUTE_ACTION,
        tool="read_file",
        args={"path": "README.md"},
    )
    assert action.is_valid()

    missing_tool = DecisionPayload(type=DecisionType.EXECUTE_ACTION, tool="")
    assert not missing_tool.is_valid()

    notify = DecisionPayload(type=DecisionType.NOTIFY_USER, message="done")
    assert notify.is_valid()


def test_agent_prompt_response_defaults_to_reflect():
    response = AgentPromptResponse()
    assert response.decision.type == DecisionType.REFLECT
    assert response.decision.is_valid()


def test_message_envelope_serializes_dataclass_payload():
    payload = ToolCallPayload(tool="read_file", args={"path": "README.md"})
    envelope = MessageEnvelope.create(ProtocolMessageType.TOOL_CALL, payload)
    data = envelope.to_dict()

    assert data["type"] == "tool_call"
    assert data["version"] == "1.0"
    assert data["id"].startswith("msg_")
    assert data["timestamp"].endswith("Z")
    assert data["payload"] == {"tool": "read_file", "args": {"path": "README.md"}}


def test_message_envelope_serializes_mapping_payload():
    payload = CompletionPayload(result="done", reasoning="verified")
    envelope = MessageEnvelope.create(ProtocolMessageType.COMPLETION, payload)
    data = envelope.to_dict()

    assert data["type"] == "completion"
    assert data["payload"]["result"] == "done"
    assert data["payload"]["reasoning"] == "verified"
