from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.widgets import Static


class ActionChip(Static):
    """Compact clickable static action chip."""

    class Pressed(Message):
        def __init__(
            self,
            sender: "ActionChip",
            *,
            action: str,
            value: str = "",
        ) -> None:
            super().__init__()
            self.sender = sender
            self.action = action
            self.value = value

    def __init__(
        self,
        label: str,
        *,
        action: str,
        value: str = "",
        **kwargs,
    ) -> None:
        super().__init__(label, **kwargs)
        self.action = action
        self.value = value

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(
            self.Pressed(
                self,
                action=self.action,
                value=self.value,
            )
        )


class UserInteraction(Container):
    """Inline interaction area above input (approval + AgentQuestion)."""

    DEFAULT_CSS = """
    UserInteraction {
        width: 100%;
        height: auto;
        border: round $surface 0%;
        padding: 0 1 0 1;
        margin: 0;
    }

    UserInteraction.-hidden {
        display: none;
    }

    UserInteraction .-hidden {
        display: none;
    }

    /* Approval row (single-line compact) */
    UserInteraction #ui_approval_row {
        width: 100%;
        height: 1;
        align: left middle;
    }

    UserInteraction #ui_approval_title {
        color: $warning;
        width: auto;
        height: 1;
    }

    UserInteraction #ui_approval_message {
        width: 1fr;
        height: 1;
        color: $text;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    UserInteraction #ui_approval_actions {
        width: auto;
        align: right middle;
        margin-left: 1;
    }

    UserInteraction .ui_action_approve {
        width: auto;
        height: 1;
        color: $text;
        text-style: bold;
        background: $success;
        padding: 0 1;
    }

    UserInteraction .ui_action_approve:hover {
        background: $success-lighten-2;
    }

    UserInteraction .ui_action_deny {
        width: auto;
        height: 1;
        color: $text;
        text-style: bold;
        margin-right: 1;
        background: $error;
        padding: 0 1;
    }

    UserInteraction .ui_action_deny:hover {
        background: $error-lighten-2;
    }

    /* AgentQuestion panel (multi-line) */
    UserInteraction #ui_question_panel {
        width: 100%;
        height: auto;
    }

    UserInteraction #ui_question_header {
        width: 100%;
        height: auto;

    }

    UserInteraction #ui_question_title {
        width: auto;
        height: auto;
        color: $warning;
        text-style: bold;
    }

    UserInteraction #ui_question_text {
        width: auto;
        height: auto;
        color: $text;
        margin: 0 0 1 0;
    }

    UserInteraction #ui_question_options {
        width: 100%;
        height: auto;
    }

    UserInteraction .ui_question_option {
        width: 100%;
        height: auto;
        color: $text;
        padding: 0 1;
        border: round $panel-lighten-1;
    }

    UserInteraction .ui_question_option:hover {
        border: round $success;
    }

    /* Changed-file review row (single-line compact) */
    UserInteraction #ui_review_row {
        width: 100%;
        height: 1;
        align: left middle;
    }

    UserInteraction #ui_review_title {
        color: $warning;
        width: auto;
        height: 1;
    }

    UserInteraction #ui_review_message {
        width: 1fr;
        height: 1;
        color: $text;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    UserInteraction #ui_review_actions {
        width: auto;
        align: right middle;
        margin-left: 1;
    }

    """

    class ActionSelected(Message):
        """Dispatched when user approves or denies an approval request."""

        def __init__(
            self,
            sender: "UserInteraction",
            *,
            task_id: str,
            action: str,
        ) -> None:
            super().__init__()
            self.sender = sender
            self.task_id = task_id
            self.action = action

    class QuestionAnswered(Message):
        """Dispatched when user answers an AgentQuestion."""

        def __init__(
            self,
            sender: "UserInteraction",
            *,
            task_id: str,
            answer: str,
        ) -> None:
            super().__init__()
            self.sender = sender
            self.task_id = task_id
            self.answer = answer

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "user_interaction"
        super().__init__(**kwargs)
        self._task_id = ""
        self._mode = "none"  # "none" | "approval" | "question" | "review"

    def compose(self) -> ComposeResult:
        with Horizontal(id="ui_approval_row"):
            yield Static("Approval Required: ", id="ui_approval_title")
            yield Static("", id="ui_approval_message")
            with Horizontal(id="ui_approval_actions"):
                yield ActionChip("Deny", action="deny", classes="ui_action_deny")
                yield ActionChip(
                    "Approve",
                    action="approve",
                    classes="ui_action_approve",
                )

        with Vertical(id="ui_question_panel"):
            with Horizontal(id="ui_question_header"):
                yield Static("Question: ", id="ui_question_title")
                yield Static("", id="ui_question_text")
            with Vertical(id="ui_question_options"):
                pass

        with Horizontal(id="ui_review_row"):
            yield Static("Review Change: ", id="ui_review_title")
            yield Static("", id="ui_review_message")
            with Horizontal(id="ui_review_actions"):
                yield ActionChip(
                    "Reject",
                    action="review_reject",
                    classes="ui_action_deny",
                )
                yield ActionChip(
                    "Accept",
                    action="review_accept",
                    classes="ui_action_approve",
                )

    def on_mount(self) -> None:
        self.hide_panel()

    def show_approval(
        self,
        *,
        task_id: str,
        tool_name: str,
        tool_args: dict,
        message: str = "",
    ) -> None:
        """Show a compact approval prompt."""
        self._task_id = task_id
        self._mode = "approval"

        body = message or f"{tool_name}"
        if tool_args:
            body = f"{tool_name}({self._format_args(tool_args)})"

        self.query_one("#ui_approval_message", Static).update(body)
        self._set_mode_classes()
        self.remove_class("-hidden")

    def show_question(
        self,
        *,
        task_id: str,
        question: str,
        options: list[str],
    ) -> None:
        """Show AgentQuestion with 2-5 quick answers and typed-answer hint."""
        self._task_id = task_id
        self._mode = "question"

        self.query_one("#ui_question_title", Static).update("Question: ")
        self.query_one("#ui_question_text", Static).update(question)

        options_container = self.query_one("#ui_question_options", Vertical)
        for child in list(options_container.children):
            child.remove()
        for option in options[:5]:
            options_container.mount(
                ActionChip(
                    option,
                    action="question_option",
                    value=option,
                    classes="ui_question_option",
                )
            )

        self._set_mode_classes()
        self.remove_class("-hidden")

    def show_review(
        self,
        *,
        task_id: str,
        file_path: str,
        change_type: str = "",
    ) -> None:
        """Show changed-file review actions (accept/reject)."""
        self._task_id = task_id
        self._mode = "review"

        label = {
            "created": "Created",
            "modified": "Modified",
            "deleted": "Deleted",
        }.get((change_type or "").strip().lower(), "Changed")
        self.query_one("#ui_review_title", Static).update("Review Change: ")
        self.query_one("#ui_review_message", Static).update(f"{label} — {file_path}")

        self._set_mode_classes()
        self.remove_class("-hidden")

    def hide_panel(self) -> None:
        self._task_id = ""
        self._mode = "none"
        self.add_class("-hidden")
        self._set_mode_classes()

    @property
    def question_active(self) -> bool:
        return self._mode == "question" and not self.has_class("-hidden")

    def submit_typed_answer(self, answer: str) -> None:
        """Submit free-text answer while question mode is active."""
        if not self.question_active:
            return
        text = answer.strip()
        if not text:
            return
        self.post_message(
            self.QuestionAnswered(
                self,
                task_id=self._task_id,
                answer=text,
            )
        )

    def on_action_chip_pressed(self, event: ActionChip.Pressed) -> None:
        event.stop()

        if event.action == "question_option":
            self.post_message(
                self.QuestionAnswered(
                    self,
                    task_id=self._task_id,
                    answer=event.value,
                )
            )
            return

        if event.action in {"review_accept", "review_reject"}:
            action = "accept" if event.action == "review_accept" else "reject"
            self.post_message(
                self.ActionSelected(
                    self,
                    task_id=self._task_id,
                    action=f"review_{action}",
                )
            )
            return

        self.post_message(
            self.ActionSelected(
                self,
                task_id=self._task_id,
                action=event.action,
            )
        )

    def _set_mode_classes(self) -> None:
        approval = self.query_one("#ui_approval_row", Horizontal)
        question = self.query_one("#ui_question_panel", Vertical)
        review = self.query_one("#ui_review_row", Horizontal)

        show_approval = self._mode == "approval"
        show_question = self._mode == "question"
        show_review = self._mode == "review"

        approval.set_class(not show_approval, "-hidden")
        question.set_class(not show_question, "-hidden")
        review.set_class(not show_review, "-hidden")

    def _format_args(self, args: dict) -> str:
        parts = [f"{k}={v!r}" for k, v in args.items()]
        joined = ", ".join(parts)
        if len(joined) <= 120:
            return joined
        return f"{joined[:117]}..."
