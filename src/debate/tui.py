#!/usr/bin/env python3
"""Textual TUI for AI Debate"""

import asyncio
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.suggester import SuggestFromList
from textual.widgets import Footer, Header, Input, Static

from .config import DEFAULT_DEBATE_STATE_PATH, OFF_MODEL_NAME, save_debate_state
from .orchestrator import AGENT_IDS, DebateOrchestrator

CONSENSUS_TEXT = "CONSENSUS REACHED"
COMMAND_SUGGESTIONS = ("/new", "/models", "/save", "/reasoning max", "/reasoning medium")
CONSENSUS_ACTION_SUGGESTIONS = ("new", "save", "continue")
QUIT_DOUBLE_CTRL_C_WINDOW_SECONDS = 2.0
CONSENSUS_CONTINUE_MESSAGE = (
    "Continue after the previous consensus. Re-open the debate, explore remaining "
    "trade-offs, risks, and alternatives before any new consensus."
)
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")
DEFAULT_TERMINAL_TITLE = "debate"

_RE_MD_BOLD_ITALIC = re.compile(r"\*\*\*(.+?)\*\*\*")
_RE_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_RE_MD_CODE_INLINE = re.compile(r"`([^`\n]+)`")
_RE_MD_HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
_RE_MD_BOLD_UNDERSCORE = re.compile(r"__([^_]+(?:_[^_]+)*)__")
_RE_ANSI_ESCAPE = re.compile(r"\\033\[[0-9;]*m")


def markdown_to_rich(text: str) -> str:
    """Convert common markdown inline formatting into Rich markup syntax."""

    if not text.strip():
        return text

    text = _RE_ANSI_ESCAPE.sub("", text)
    text = text.replace("[", "[[").replace("]", "]]")
    text = _RE_MD_BOLD_ITALIC.sub(r"[bold italic]\1[/bold italic]", text)
    text = _RE_MD_BOLD.sub(r"[bold]\1[/bold]", text)
    text = _RE_MD_BOLD_UNDERSCORE.sub(r"[bold]\1[/bold]", text)
    text = _RE_MD_ITALIC.sub(r"[italic]\1[/italic]", text)
    text = _RE_MD_CODE_INLINE.sub(r"[bold yellow]\1[/bold yellow]", text)
    text = _RE_MD_HEADING.sub(r"[bold underline]\1[/bold underline]", text)

    return text


class PromptDisplay(Static):
    """Display the initial prompt as part of the debate transcript."""

    def __init__(self, prompt: str, **kwargs):
        kwargs.setdefault("id", "initial-prompt")
        super().__init__(markdown_to_rich(prompt), **kwargs)
        self.border_title = "Initial prompt"


class TurnDisplay(Static):
    """Display for a single turn in the debate"""

    def __init__(self, agent_id: str, turn_number: int, content: str = "", **kwargs):
        super().__init__("Running..." if not content else markdown_to_rich(content), **kwargs)
        self.agent_id = agent_id
        self.turn_number = turn_number
        self._raw_content = content
        self.is_thinking = True
        self.border_title = f"Turn {self.turn_number} - Agent {self.agent_id}"
        self.add_class("thinking")

    def update_content(self, content: str, finished: bool = False):
        """Update turn content"""
        self._raw_content = content
        self.is_thinking = not finished
        try:
            self.update(markdown_to_rich(content) if content else "Running...")
        except Exception:
            self.update(content or "Running...")
        self.set_class(self.is_thinking, "thinking")
        self.set_class(finished, "finished")
        if finished:
            self.remove_class("pulse-on")


class ClosingOverlay(Container):
    """Full-screen dimming overlay shown while cleanup runs."""

    def __init__(self, **kwargs):
        kwargs.setdefault("id", "closing-overlay")
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Container(
            Static("App closing, wait", id="closing-label"),
            Static("...", id="closing-dots"),
            id="closing-box",
        )


class DebateView(ScrollableContainer):
    """Scrollable container for debate turns"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.turns = []

    def show_initial_prompt(self, prompt: str) -> PromptDisplay:
        """Show the initial user prompt before agent answers."""
        prompt_display = PromptDisplay(prompt)
        self.mount(prompt_display)
        self.scroll_end(animate=False)
        return prompt_display

    def add_turn(self, agent_id: str, turn_number: int) -> TurnDisplay:
        """Add a new turn display"""
        turn = TurnDisplay(agent_id, turn_number)
        self.turns.append(turn)
        self.mount(turn)
        self.scroll_end(animate=False)
        return turn

    def show_consensus(self):
        """Show consensus banner"""
        banner = Static(CONSENSUS_TEXT)
        self.mount(banner)
        self.scroll_end(animate=False)


class DebateApp(App):
    """Main Textual app for AI Debate"""

    CSS = """
    Screen {
        background: $surface;
        layers: base overlay;
    }

    #status {
        height: 7;
        background: $panel;
        padding: 0 1;
        layer: base;
        text-align: left;
    }

    #debate-view {
        height: 1fr;
        border: solid $primary;
        layer: base;
    }

    PromptDisplay {
        margin: 1;
        padding: 1;
        background: $surface-darken-1;
        border: round white;
        border-title-align: left;
    }

    TurnDisplay {
        margin: 1;
        padding: 1;
        background: $surface-darken-1;
        border: round $accent;
        border-title-align: left;
    }

    TurnDisplay.thinking.pulse-on {
        border: heavy $accent;
    }

    TurnDisplay.finished {
        border: round $accent;
    }

    #input-container {
        height: auto;
        background: $panel;
        padding: 1;
        layer: base;
    }

    Input {
        width: 100%;
    }

    #help {
        height: 2;
        background: $boost;
        color: $text-muted;
        text-align: center;
        layer: base;
    }

    #closing-overlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        background: black 70%;
        align: center middle;
    }

    #closing-box {
        width: 36;
        height: 5;
        padding: 1 2;
        border: round $accent;
        background: $panel;
        layout: horizontal;
        align: center middle;
    }

    #closing-label {
        width: auto;
        height: 1;
    }

    #closing-dots {
        width: 3;
        height: 1;
    }

    #closing-dots.blink-off {
        color: $panel;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+s", "save", "Save transcript"),
        Binding("escape", "pause", "Pause & intervene"),
    ]

    def __init__(
        self,
        orchestrator: DebateOrchestrator,
        *,
        models_yaml: dict[str, Any] | None = None,
        state_path: Path = DEFAULT_DEBATE_STATE_PATH,
    ):
        super().__init__()
        self.orchestrator = orchestrator
        self.models_yaml = models_yaml or {"workers": {}}
        self.state_path = state_path
        self.current_turn_display: Optional[TurnDisplay] = None
        self.is_paused = False
        self.debate_task: Optional[asyncio.Task] = None
        self.active_agent: Optional[str] = None
        self.current_turn_interrupted = False
        self.pending_interventions: list[str] = []
        self.debate_finished = False
        self.model_selection_active = False
        self.awaiting_consensus_action = False
        self._reasoning_warn_shown = False
        self._last_quit_press: float = 0.0
        self.next_agent = self.orchestrator.first_agent_id
        self.next_turn_number = 1
        self.next_turn_is_first = True
        self._pulse_on = True
        self._spinner_index = 0
        self._terminal_title = DEFAULT_TERMINAL_TITLE
        self._tty = None
        try:
            self._tty = open("/dev/tty", "w")
        except (OSError, IOError):
            pass

    def status_text(self, state: str) -> str:
        from .orchestrator import ALL_AGENT_IDS

        def _truncate(s: str, n: int = 40) -> str:
            return s if len(s) <= n else s[: n - 1] + "…"

        lines = []
        for agent_id in ALL_AGENT_IDS:
            if agent_id in self.orchestrator.agent_runners:
                runner = self.orchestrator.agent_runners[agent_id]
                lines.append(
                    f"Agent {agent_id}: {runner.model} ({_truncate(runner.description)})"
                )
            else:
                lines.append(f"Agent {agent_id}: off")
        lines.append(f"reasoning: {self.orchestrator.reasoning_level} | {state}")
        return "\n".join(lines)

    def reset_turn_cursor(self) -> None:
        self.next_agent = self.orchestrator.first_agent_id
        self.next_turn_number = 1
        self.next_turn_is_first = True

    def compose(self) -> ComposeResult:
        """Compose the UI"""
        yield Header()
        yield Static(
            self.status_text("Enter initial prompt"),
            id="status",
        )
        yield DebateView(id="debate-view")
        yield Container(
            Input(
                placeholder="Type the debate prompt...",
                id="user-input",
                suggester=SuggestFromList(COMMAND_SUGGESTIONS, case_sensitive=False),
            ),
            id="input-container",
        )
        yield Static(
            "Commands: /new | /models | /save | /reasoning max|medium | ESC - pause | Ctrl+C - quit",
            id="help",
        )
        yield Footer()

    async def on_mount(self) -> None:
        """Focus input and wait for the initial prompt."""
        self.query_one("#user-input", Input).focus()
        self.set_interval(0.2, self._tick_pulse)

    def _set_terminal_title(self, title: str) -> None:
        """Set the terminal tab/window title via ANSI escape sequence."""
        if self._terminal_title == title:
            return
        self._terminal_title = title
        if self._tty is None:
            return
        try:
            self._tty.write(f"\033]0;{title}\007")
            self._tty.flush()
        except (OSError, IOError):
            self._tty = None

    def _tick_pulse(self) -> None:
        """Toggle active borders, closing dots, and spinner at a 2.5Hz visible cycle."""
        self._pulse_on = not self._pulse_on
        for turn in self.query(TurnDisplay):
            turn.set_class(turn.has_class("thinking") and self._pulse_on, "pulse-on")
        for dots in self.query("#closing-dots"):
            dots.set_class(not self._pulse_on, "blink-off")
            dots.update("..." if self._pulse_on else "   ")

        if self.active_agent and not self.is_paused:
            frame = SPINNER_FRAMES[self._spinner_index % len(SPINNER_FRAMES)]
            self._spinner_index += 1
            self._set_terminal_title(f"{frame} debate — Agent {self.active_agent}")
        elif self.is_paused:
            self._set_terminal_title("⏸ debate")
        else:
            self._set_terminal_title(DEFAULT_TERMINAL_TITLE)

    def notify_consensus(self) -> None:
        """Ask the terminal to signal consensus with its configured bell."""
        self.bell()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input"""
        message = event.value.strip()
        input_widget = self.query_one("#user-input", Input)
        input_widget.value = ""

        if not message:
            if self.awaiting_consensus_action:
                await self.handle_consensus_action("new")
            return

        await self.handle_user_message(message)

    async def handle_user_message(self, message: str) -> None:
        """Handle commands, initial prompt, and runtime interventions."""

        if self.awaiting_consensus_action:
            await self.handle_consensus_action(message)
            return

        if message == "/new":
            await self.action_new_debate()
            return
        if message == "/save":
            await self.action_save()
            return
        if message == "/models":
            await self.action_models()
            return
        if message.startswith("/reasoning"):
            await self.action_reasoning(message)
            return
        if self.model_selection_active:
            await self.apply_model_selection(message)
            return

        if self.debate_finished and not self.orchestrator.is_any_agent_running():
            await self.start_new_debate(message)
            return

        if not self.orchestrator.has_initial_prompt:
            await self.start_debate(message)
            return

        debate_view = self.query_one("#debate-view", DebateView)
        intervention = Static(Text(f"\n[USER]: {message}\n"), classes="user-intervention")
        debate_view.mount(intervention)
        debate_view.scroll_end(animate=False)

        if self.active_agent and self.orchestrator.is_agent_running(self.active_agent):
            self.current_turn_interrupted = True
            self.pending_interventions.append(message)
            self.orchestrator.interrupt_agent(self.active_agent)
        else:
            self.orchestrator.add_user_intervention(message)

        self.is_paused = False

    async def start_debate(self, initial_prompt: str) -> None:
        self.debate_finished = False
        self.awaiting_consensus_action = False
        self.reset_turn_cursor()
        self.orchestrator.set_initial_prompt(initial_prompt)
        self.query_one("#debate-view", DebateView).show_initial_prompt(initial_prompt)
        self.query_one("#status", Static).update(self.status_text("Running"))
        self.configure_input(
            "Type intervention or command (/new, /save, /models)...",
            COMMAND_SUGGESTIONS,
        )
        self.debate_task = asyncio.create_task(self.run_debate())

    async def start_new_debate(self, initial_prompt: str | None = None) -> None:
        if self.debate_task and not self.debate_task.done():
            self.debate_task.cancel()
        self.orchestrator.reset()
        self.current_turn_display = None
        self.active_agent = None
        self.current_turn_interrupted = False
        self.pending_interventions.clear()
        self.is_paused = False
        self.debate_finished = False
        self.awaiting_consensus_action = False
        self.reset_turn_cursor()

        debate_view = self.query_one("#debate-view", DebateView)
        for child in list(debate_view.children):
            await child.remove()
        debate_view.turns.clear()

        self.query_one("#status", Static).update(self.status_text("Enter initial prompt"))
        self.configure_input("Type the debate prompt...", COMMAND_SUGGESTIONS)
        if initial_prompt:
            await self.start_debate(initial_prompt)

    def configure_input(self, placeholder: str, suggestions: tuple[str, ...]) -> None:
        input_widget = self.query_one("#user-input", Input)
        input_widget.placeholder = placeholder
        input_widget.suggester = SuggestFromList(suggestions, case_sensitive=False)

    def show_consensus_actions(self) -> None:
        self.awaiting_consensus_action = True
        self.debate_finished = True
        self.query_one("#status", Static).update(
            self.status_text("Consensus reached - choose action")
        )
        self.query_one("#help", Static).update(
            "Consensus actions: Enter/new - new debate | save - save & close agents | continue - keep agents"
        )
        self.configure_input("Consensus: Enter/new | save | continue", CONSENSUS_ACTION_SUGGESTIONS)

    async def handle_consensus_action(self, message: str) -> None:
        action = message.strip().lower()
        if action.startswith("/"):
            action = action[1:]

        if action in {"", "new"}:
            await self.action_new_debate()
            return

        if action == "save":
            await self.action_save()
            return

        if action == "continue":
            await self.continue_after_consensus()
            return

        await self.continue_after_consensus(message)

    async def continue_after_consensus(self, message: str | None = None) -> None:
        self.awaiting_consensus_action = False
        self.debate_finished = False
        self.is_paused = False
        if self.next_agent != self.orchestrator.first_agent_id:
            self.next_turn_number += 1
        self.next_agent = self.orchestrator.first_agent_id
        self.next_turn_is_first = False
        visible_message = message or "Continue after consensus."
        debate_view = self.query_one("#debate-view", DebateView)
        debate_view.mount(
            Static(Text(f"\n[USER]: {visible_message}\n"), classes="user-intervention")
        )
        debate_view.scroll_end(animate=False)
        self.orchestrator.add_user_intervention(message or CONSENSUS_CONTINUE_MESSAGE)
        self.query_one("#status", Static).update(self.status_text("Running"))
        self.query_one("#help", Static).update(
            "Commands: /new | /models | /save | /reasoning max|medium | ESC - pause | double Ctrl+C - quit"
        )
        self.configure_input(
            "Type intervention or command (/new, /save, /models)...",
            COMMAND_SUGGESTIONS,
        )
        self.debate_task = asyncio.create_task(self.run_debate())

    async def action_models(self) -> None:
        if self.orchestrator.is_any_agent_running():
            self.notify("Wait for the active turn to finish before changing models")
            return
        self.model_selection_active = True
        debate_view = self.query_one("#debate-view", DebateView)
        debate_view.mount(Static(self.model_menu_text(), classes="model-menu"))
        debate_view.scroll_end(animate=False)
        self.query_one(
            "#user-input", Input
        ).placeholder = "Choose Agent A, B, C, D, E model numbers, e.g. 1 2 5"

    async def action_reasoning(self, message: str) -> None:
        """Switch reasoning level for all agents (session-memory).

        `/reasoning max|medium`. 'max' is default (high effort); 'medium' spawns
        quick debates. Workers that do not support reasoning control are
        warned once per session via notify and keep their default launch.
        """

        parts = message.split()
        if len(parts) != 2 or parts[1] not in {"max", "medium"}:
            self.notify("Usage: /reasoning max | /reasoning medium")
            return
        level = parts[1]
        ok, unsupported = self.orchestrator.set_reasoning_level(level)
        if not ok and level != "max" and not self._reasoning_warn_shown:
            for name in unsupported:
                self.notify(
                    f"{name}: reasoning not supported, using default",
                    timeout=4,
                )
            self._reasoning_warn_shown = True
        self.query_one("#status", Static).update(
            self.status_text(f"reasoning: {level}")
        )
        self.notify(f"Reasoning set to {level}")

    async def apply_model_selection(self, message: str) -> None:
        parts = message.replace(",", " ").split()
        if len(parts) < 2 or len(parts) > 5 or not all(part.isdigit() for part in parts):
            self.notify("Enter two to five model numbers, e.g. 1 2 5")
            return
        model_names = self.available_model_names()
        indexes = [int(part) - 1 for part in parts]
        if any(index not in range(len(model_names)) for index in indexes):
            self.notify("Model number out of range")
            return

        selected_names = [model_names[index] for index in indexes]
        if sum(model_name != OFF_MODEL_NAME for model_name in selected_names) < 2:
            self.notify("Select at least two active models")
            return
        while len(selected_names) < 5:
            selected_names.append(OFF_MODEL_NAME)

        selected_configs = [self.worker_config(model_name) for model_name in selected_names]
        self.orchestrator.set_models(agent_model_configs=selected_configs)
        self.reset_turn_cursor()
        save_debate_state(tuple(selected_names), self.state_path)
        self.model_selection_active = False
        self.query_one("#status", Static).update(self.status_text("Models updated"))
        self.configure_input("Type the debate prompt...", COMMAND_SUGGESTIONS)
        self.notify(
            "Models: "
            + ", ".join(
                f"Agent {agent_id}={model_name}"
                for agent_id, model_name in zip(AGENT_IDS, selected_names)
            )
        )

    def available_model_names(self) -> list[str]:
        model_names = list(self.models_yaml.get("workers", {}).keys())
        return [name for name in model_names if name != OFF_MODEL_NAME] + [OFF_MODEL_NAME]

    def worker_config(self, model_name: str) -> dict[str, Any] | None:
        if model_name == OFF_MODEL_NAME:
            return None
        config = dict(self.models_yaml["workers"][model_name])
        config["name"] = model_name
        return config

    def model_menu_text(self) -> str:
        model_names = self.available_model_names()
        lines = [
            "Select models: type two to five numbers, e.g. 1 2 5",
            "",
            "Agent A                 Agent B                 Agent C                 Agent D                 Agent E",
        ]
        for index, model_name in enumerate(model_names, 1):
            prefix = f"{index:>2}. {model_name:<18}"
            lines.append(f"{prefix} {prefix} {prefix} {prefix} {index:>2}. {model_name}")
        return "\n".join(lines)

    async def run_debate(self):
        """Main debate loop"""
        debate_view = self.query_one("#debate-view", DebateView)

        while self.orchestrator.should_continue():
            if self.is_paused:
                await asyncio.sleep(0.5)
                continue

            # Start turn
            current_agent = self.next_agent
            turn_number = self.next_turn_number
            is_first_turn = self.next_turn_is_first
            turn_display = debate_view.add_turn(current_agent, turn_number)
            self.current_turn_display = turn_display
            self.active_agent = current_agent
            self.current_turn_interrupted = False

            self.orchestrator.start_turn(current_agent, is_first_turn)
            self.next_turn_is_first = False

            # Poll for output
            last_output = ""
            while self.orchestrator.is_agent_running(current_agent):
                if self.is_paused:
                    await asyncio.sleep(0.5)
                    continue

                if self.orchestrator.display_mode == "live":
                    output = self.orchestrator.capture_agent_output(current_agent)
                    if output != last_output:
                        turn_display.update_content(output, finished=False)
                        last_output = output

                await asyncio.sleep(self.orchestrator.poll_interval_seconds)

            # Finalize turn
            raw_output = self.orchestrator.capture_agent_output(current_agent)
            was_interrupted = self.current_turn_interrupted
            final_output = self.orchestrator.finalize_turn(
                current_agent, raw_output, interrupted=was_interrupted
            )
            turn_display.update_content(final_output, finished=True)
            if was_interrupted:
                for intervention in self.pending_interventions:
                    self.orchestrator.add_user_intervention(intervention)
                self.pending_interventions.clear()
            self.active_agent = None

            next_agent = self.orchestrator.next_agent_id(current_agent)
            next_turn_number = (
                turn_number + 1 if next_agent == self.orchestrator.first_agent_id else turn_number
            )
            self.next_agent = next_agent
            self.next_turn_number = next_turn_number

            # Check for consensus
            if not was_interrupted and self.orchestrator.can_accept_consensus(final_output):
                debate_view.show_consensus()
                self.notify_consensus()
                self.show_consensus_actions()
                break

            await asyncio.sleep(1)

        if self.awaiting_consensus_action:
            return

        self.query_one("#status", Static).update(
            self.status_text("Finished - type a new prompt to start a new debate")
        )
        self.debate_finished = True
        if self.orchestrator.auto_save:
            self.orchestrator.save_transcript()
        self.orchestrator.cleanup()

    async def action_save(self) -> None:
        """Save transcript"""
        filepath = self.orchestrator.save_transcript()
        self.notify(f"Transcript saved to {filepath}")
        if self.awaiting_consensus_action:
            self.orchestrator.cleanup()
            self.awaiting_consensus_action = False
            self.debate_finished = True
            self.query_one("#status", Static).update(
                self.status_text("Saved - type a new prompt to start a new debate")
            )
            self.query_one("#help", Static).update(
                "Commands: /new | /models | /save | /reasoning max|medium | ESC - pause | double Ctrl+C - quit"
            )
            self.configure_input("Type the debate prompt...", COMMAND_SUGGESTIONS)

    async def action_pause(self) -> None:
        """Smart ESC: stop a running agent, or toggle pause idle.

        When an agent is actively running, ESC interrupts it and resets the
        debate back to the 'Enter initial prompt' prompt so the user can
        start over. When nothing is running, ESC toggles a passive pause
        flag (legacy behavior) that the run-debate loop observes.
        """

        if self.active_agent and self.orchestrator.is_agent_running(self.active_agent):
            # Hard stop: cancel the debate task and clean up the whole session.
            if self.debate_task and not self.debate_task.done():
                self.debate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.debate_task
            self.orchestrator.interrupt_agent(self.active_agent)
            await asyncio.to_thread(self.orchestrator.cleanup)
            self.active_agent = None
            self.current_turn_display = None
            self.current_turn_interrupted = False
            self.pending_interventions.clear()
            self.is_paused = False
            await self.start_new_debate()
            self.notify("Debate stopped, enter a new prompt")
            return

        self.is_paused = not self.is_paused
        status = "paused" if self.is_paused else "resumed"
        self.query_one("#status", Static).update(self.status_text(status.title()))
        self.notify(f"Debate {status}")

    async def action_new_debate(self) -> None:
        """Start new debate"""
        await self.start_new_debate()
        self.notify("New debate ready")

    async def show_closing_overlay(self) -> None:
        """Dim the app and show a centered cleanup message."""
        if len(self.query("#closing-overlay")) == 0:
            await self.mount(ClosingOverlay())
        self._pulse_on = True
        for dots in self.query("#closing-dots"):
            dots.remove_class("blink-off")
            dots.update("...")
        self.refresh(layout=True)
        await asyncio.sleep(0)

    async def action_quit(self) -> None:
        """Quit on double Ctrl+C within QUIT_DOUBLE_CTRL_C_WINDOW_SECONDS.

        A single Ctrl+C notifies the user to press again to confirm, so terminal
        text selection by mouse is not blown away by an accidental first press.
        """

        now = time.monotonic()
        if self._last_quit_press and now - self._last_quit_press <= QUIT_DOUBLE_CTRL_C_WINDOW_SECONDS:
            self._last_quit_press = 0.0
            await self._do_quit()
            return
        self._last_quit_press = now
        self.notify("Press Ctrl+C again to quit", timeout=2)

    async def _do_quit(self) -> None:
        self._set_terminal_title(DEFAULT_TERMINAL_TITLE)
        if self._tty is not None:
            try:
                self._tty.close()
            except (OSError, IOError):
                pass
            self._tty = None
        await self.show_closing_overlay()
        if self.debate_task and not self.debate_task.done():
            self.debate_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.debate_task
        await asyncio.to_thread(self.orchestrator.cleanup)
        await super().action_quit()


def run_tui(
    orchestrator: DebateOrchestrator,
    *,
    models_yaml: dict[str, Any] | None = None,
    state_path: Path = DEFAULT_DEBATE_STATE_PATH,
):
    """Run the TUI app"""
    app = DebateApp(orchestrator, models_yaml=models_yaml, state_path=state_path)
    app.run()
