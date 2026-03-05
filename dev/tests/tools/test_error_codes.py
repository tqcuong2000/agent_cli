from agent_cli.core.runtime.tools.error_codes import ToolErrorCode


def test_retryable_error_codes() -> None:
    assert ToolErrorCode.COMMAND_TIMEOUT.retryable is True
    assert ToolErrorCode.APPROVAL_TIMEOUT.retryable is True
    assert ToolErrorCode.OUTPUT_TRUNCATED.retryable is True
    assert ToolErrorCode.FILE_NOT_FOUND.retryable is False
