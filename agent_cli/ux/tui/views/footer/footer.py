from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static, TextArea

from agent_cli.core.events.events import (
    AgentQuestionRequestEvent,
    AgentQuestionResponseEvent,
    BaseEvent,
    ChangedFileReviewActionEvent,
    ChangedFileSelectedEvent,
    UserApprovalRequestEvent,
    UserApprovalResponseEvent,
    UserRequestEvent,
)
from agent_cli.ux.tui.views.footer.submit_btn import SubmitButtonComponent
from agent_cli.ux.tui.views.footer.user_input import UserInputComponent
from agent_cli.ux.tui.views.footer.user_interaction import UserInteraction
from agent_cli.ux.tui.views.header.status import StatusContainer


class FooterContainer(Container):
    """The footer container holding the terminal input area and status bar."""

    DEFAULT_CSS = """
    FooterContainer {
        dock: bottom;
        width: 100%;
        height: auto;
        background: transparent;
        align: left bottom;
    }

    FooterContainer .input_container {
        width: 100%;
        height: auto;
        border: solid #2a2f35;
        min-height: 1;
        align: left bottom;
    }

    FooterContainer #question_input_hint {
        width: 100%;
        height: 1;
        color: $text-muted;
        padding: 0 1;
        margin: 0;
    }

    FooterContainer #question_input_hint.-hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs):
        if "id" not in kwargs:
            kwargs["id"] = "footer"
        super().__init__(**kwargs)

        self.input_comp = UserInputComponent()
        self.submit_btn = SubmitButtonComponent()
        self.user_interaction = UserInteraction()
        self._subscriptions: list[str] = []
        self._selected_changed_file_path: str = ""
        self._selected_changed_file_change_type: str = ""

    def compose(self) -> ComposeResult:
        yield self.user_interaction
        yield Static("Type your answer", id="question_input_hint", classes="-hidden")
        with Horizontal(classes="input_container"):
            yield self.input_comp
            yield self.submit_btn
        yield StatusContainer()

    def on_mount(self) -> None:
        self._sync_submit_button_offset()
        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            return
        self._subscriptions.append(
            app_context.event_bus.subscribe(
                "UserApprovalRequestEvent",
                self._on_user_approval_request,
                priority=50,
            )
        )
        self._subscriptions.append(
            app_context.event_bus.subscribe(
                "AgentQuestionRequestEvent",
                self._on_agent_question_request,
                priority=50,
            )
        )
        self._subscriptions.append(
            app_context.event_bus.subscribe(
                "ChangedFileSelectedEvent",
                self._on_changed_file_selected,
                priority=50,
            )
        )

    def on_unmount(self) -> None:
        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            return
        for subscription_id in self._subscriptions:
            app_context.event_bus.unsubscribe(subscription_id)
        self._subscriptions.clear()

    def _sync_submit_button_offset(self) -> None:
        """Keep the submit button aligned with the bottom input line."""
        visible_lines = self.input_comp.visible_line_count
        self.submit_btn.styles.offset = (0, visible_lines - 1)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self.input_comp:
            self._sync_submit_button_offset()

    def on_submit_button_component_pressed(
        self, _: SubmitButtonComponent.Pressed
    ) -> None:
        self.input_comp.submit()

    async def _on_user_approval_request(self, event: BaseEvent) -> None:
        if not isinstance(event, UserApprovalRequestEvent):
            return
        self._hide_question_hint()
        self.user_interaction.show_approval(
            task_id=event.task_id,
            tool_name=event.tool_name,
            tool_args=event.arguments,
            message=event.risk_description,
        )

    async def _on_agent_question_request(self, event: BaseEvent) -> None:
        if not isinstance(event, AgentQuestionRequestEvent):
            return
        self._show_question_hint()
        self.user_interaction.show_question(
            task_id=event.task_id,
            question=event.question,
            options=event.options,
        )

    async def _on_changed_file_selected(self, event: BaseEvent) -> None:
        if not isinstance(event, ChangedFileSelectedEvent):
            return

        self._selected_changed_file_path = event.file_path
        self._selected_changed_file_change_type = event.change_type

        self._hide_question_hint()
        self.user_interaction.show_review(
            task_id=event.task_id,
            file_path=event.file_path,
            change_type=event.change_type,
        )

    async def on_user_interaction_action_selected(
        self, event: UserInteraction.ActionSelected
    ) -> None:
        event.stop()
        self._hide_question_hint()
        self.user_interaction.hide_panel()

        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            return

        if event.action in {"review_accept", "review_reject"}:
            file_path = self._selected_changed_file_path.strip()
            if not file_path:
                return

            action = "accept" if event.action == "review_accept" else "reject"
            await app_context.event_bus.emit(
                ChangedFileReviewActionEvent(
                    source="tui",
                    task_id=event.task_id,
                    file_path=file_path,
                    action=action,
                )
            )
            self._selected_changed_file_path = ""
            self._selected_changed_file_change_type = ""
            self.user_interaction.hide_panel()
            return

        approved = event.action == "approve"
        await app_context.event_bus.emit(
            UserApprovalResponseEvent(
                source="tui",
                task_id=event.task_id,
                approved=approved,
                modified_arguments=None,
            )
        )

    async def on_user_interaction_question_answered(
        self, event: UserInteraction.QuestionAnswered
    ) -> None:
        event.stop()
        self._hide_question_hint()
        self.user_interaction.hide_panel()

        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            return

        await app_context.event_bus.emit(
            AgentQuestionResponseEvent(
                source="tui",
                task_id=event.task_id,
                answer=event.answer,
            )
        )

    async def on_user_input_component_submitted(
        self, event: UserInputComponent.Submitted
    ) -> None:
        text = event.value.strip()
        if not text:
            return
        event.stop()

        app_context = getattr(self.app, "app_context", None)
        if app_context is None:
            return

        # ── AgentQuestion typed answer path ──────────────────────
        if self.user_interaction.question_active:
            self.user_interaction.submit_typed_answer(text)
            return

        # ── Slash-command interception ────────────────────────────
        # Execute commands locally; do NOT publish a UserRequestEvent
        # so the chat window never shows the raw "/command" text.
        if text.startswith("/"):
            parser = getattr(app_context, "command_parser", None)
            if parser is not None:
                result = await parser.execute(text)
                if result.message:
                    from agent_cli.core.events.events import AgentMessageEvent

                    await app_context.event_bus.emit(
                        AgentMessageEvent(
                            source="command_system",
                            content=result.message,
                            is_monologue=False,
                        )
                    )
                return  # Do NOT publish UserRequestEvent for commands

        # ── Mount user message BEFORE emitting the event ─────────
        # This guarantees the user bubble is in the DOM before the
        # Orchestrator starts the agent and agent responses mount.
        try:
            from agent_cli.ux.tui.views.body.text_window import TextWindowContainer

            text_window = self.app.query_one(TextWindowContainer)
            text_window.add_user_message(text)
        except Exception:
            pass  # TextWindowContainer may not be mounted

        # ── Normal user input → event bus ────────────────────────
        await app_context.event_bus.emit(
            UserRequestEvent(
                source="tui",
                text=text,
            )
        )

    def _show_question_hint(self) -> None:
        try:
            self.query_one("#question_input_hint", Static).remove_class("-hidden")
        except Exception:
            pass

    def _hide_question_hint(self) -> None:
        try:
            self.query_one("#question_input_hint", Static).add_class("-hidden")
        except Exception:
            pass
