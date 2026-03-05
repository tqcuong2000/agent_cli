from __future__ import annotations

from typing import Optional

from textual.containers import Container
from textual.widget import Widget

from agent_cli.core.ux.tui.views.body.messages.answer_block import AnswerBlock
from agent_cli.core.ux.tui.views.body.messages.changed_file_detail_block import (
    ChangedFileDetailBlock,
    DiffLine,
)
from agent_cli.core.ux.tui.views.body.messages.thinking_block import ThinkingBlock
from agent_cli.core.ux.tui.views.body.messages.tool_step import ToolStepWidget


class AgentResponseContainer(Container):
    """Container for a single agent turn (thinking, tools, final answer)."""

    DEFAULT_CSS = """
    AgentResponseContainer {
        layout: vertical;
        width: 100%;
        height: auto;
        padding: 0 2;
        margin: 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active_thinking: Optional["ThinkingBlock"] = None

    def append_thinking(self) -> "ThinkingBlock":
        thinking = self._create_thinking_block()
        self._active_thinking = thinking
        self._mount_or_defer(thinking)
        return thinking

    def append_tool_step(self, tool_name: str, args: dict) -> "ToolStepWidget":
        tool_step = self._create_tool_step(tool_name=tool_name, args=args)
        self._mount_or_defer(tool_step)
        return tool_step

    def set_answer(self, content: str) -> None:
        answer = self._create_answer_block(content=content)
        self._mount_or_defer(answer)
        self._active_thinking = None

    def set_changed_file_detail(
        self,
        *,
        title: str,
        summary: str = "",
        diff_lines: list[DiffLine] | None = None,
        file_path: str = "",
    ) -> None:
        detail = self._create_changed_file_detail_block(
            title=title,
            summary=summary,
            diff_lines=diff_lines or [],
            file_path=file_path,
        )
        self._mount_or_defer(detail)
        self._active_thinking = None

    def get_active_thinking(self) -> Optional["ThinkingBlock"]:
        if self._active_thinking is None:
            return None
        if getattr(self._active_thinking, "is_streaming", True):
            return self._active_thinking
        return None

    def _mount_child(self, child: Widget) -> None:
        self.mount(child)

    def _mount_or_defer(self, child: Widget) -> None:
        if self.is_mounted:
            self._mount_child(child)
            return
        self.call_after_refresh(self._mount_child, child)

    def _create_thinking_block(self) -> "ThinkingBlock":
        from agent_cli.core.ux.tui.views.body.messages.thinking_block import ThinkingBlock

        return ThinkingBlock()

    def _create_tool_step(self, tool_name: str, args: dict) -> "ToolStepWidget":
        from agent_cli.core.ux.tui.views.body.messages.tool_step import ToolStepWidget

        return ToolStepWidget(tool_name=tool_name, args=args)

    def _create_answer_block(self, content: str) -> "AnswerBlock":
        from agent_cli.core.ux.tui.views.body.messages.answer_block import AnswerBlock

        return AnswerBlock(content=content)

    def _create_changed_file_detail_block(
        self,
        *,
        title: str,
        summary: str,
        diff_lines: list[DiffLine],
        file_path: str = "",
    ) -> "ChangedFileDetailBlock":
        from agent_cli.core.ux.tui.views.body.messages.changed_file_detail_block import (
            ChangedFileDetailBlock,
        )

        return ChangedFileDetailBlock(
            title=title,
            summary=summary,
            diff_lines=diff_lines,
            file_path=file_path,
        )
