#!/usr/bin/env python3
"""Textual TUI for AI Debate"""

import asyncio
from pathlib import Path
from typing import Any, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.widgets import Footer, Header, Input, Static

from .config import DEFAULT_DEBATE_STATE_PATH, save_debate_state
from .orchestrator import DebateOrchestrator

CONSENSUS_TEXT = "CONSENSUS REACHED"


class TurnDisplay(Static):
    """Display for a single turn in the debate"""

    def __init__(self, agent_id: str, turn_number: int, content: str = "", **kwargs):
        super().__init__(**kwargs)
        self.agent_id = agent_id
        self.turn_number = turn_number
        self.content = content
        self.is_thinking = True

    def render(self) -> str:
        agent_label = f"Agent {self.agent_id}"
        border = "─" * 60

        if self.is_thinking and not self.content:
            return f"┌─ [Turn {self.turn_number}] {agent_label} ─ working ─┐\n│ Running...\n└{border}┘"

        return f"┌─ [Turn {self.turn_number}] {agent_label} ─────────────────┐\n│ {self.content}\n└{border}┘"

    def update_content(self, content: str, finished: bool = False):
        """Update turn content"""
        self.content = content
        self.is_thinking = not finished
        self.refresh()


class DebateView(ScrollableContainer):
    """Scrollable container for debate turns"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.turns = []

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
    }

    #status {
        height: 3;
        background: $panel;
        padding: 1;
    }

    #debate-view {
        height: 1fr;
        border: solid $primary;
    }

    TurnDisplay {
        margin: 1;
        padding: 1;
        background: $surface-darken-1;
        border: solid $accent;
    }

    #input-container {
        height: auto;
        background: $panel;
        padding: 1;
    }

    Input {
        width: 100%;
    }

    #help {
        height: 2;
        background: $boost;
        color: $text-muted;
        text-align: center;
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

    def status_text(self, state: str) -> str:
        return (
            f"Agent A: {self.orchestrator.model_a.model} ({self.orchestrator.model_a.description}) | "
            f"Agent B: {self.orchestrator.model_b.model} ({self.orchestrator.model_b.description}) | "
            f"{state}"
        )

    def compose(self) -> ComposeResult:
        """Compose the UI"""
        yield Header()
        yield Static(
            self.status_text("Enter initial prompt"),
            id="status",
        )
        yield DebateView(id="debate-view")
        yield Container(
            Input(placeholder="Type the debate prompt...", id="user-input"),
            id="input-container",
        )
        yield Static(
            "Commands: /new | /models | /save | ESC - pause | Ctrl+C - quit",
            id="help",
        )
        yield Footer()

    async def on_mount(self) -> None:
        """Focus input and wait for the initial prompt."""
        self.query_one("#user-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input"""
        message = event.value.strip()
        input_widget = self.query_one("#user-input", Input)
        input_widget.value = ""

        if not message:
            return

        await self.handle_user_message(message)

    async def handle_user_message(self, message: str) -> None:
        """Handle commands, initial prompt, and runtime interventions."""

        if message == "/new":
            await self.action_new_debate()
            return
        if message == "/save":
            await self.action_save()
            return
        if message == "/models":
            await self.action_models()
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
        intervention = Static(f"\n[USER]: {message}\n", classes="user-intervention")
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
        self.orchestrator.set_initial_prompt(initial_prompt)
        self.query_one("#status", Static).update(self.status_text("Running"))
        self.query_one("#user-input", Input).placeholder = (
            "Type intervention or command (/new, /save, /models)..."
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

        debate_view = self.query_one("#debate-view", DebateView)
        for child in list(debate_view.children):
            await child.remove()
        debate_view.turns.clear()

        self.query_one("#status", Static).update(self.status_text("Enter initial prompt"))
        self.query_one("#user-input", Input).placeholder = "Type the debate prompt..."
        if initial_prompt:
            await self.start_debate(initial_prompt)

    async def action_models(self) -> None:
        if self.orchestrator.is_any_agent_running():
            self.notify("Wait for the active turn to finish before changing models")
            return
        self.model_selection_active = True
        debate_view = self.query_one("#debate-view", DebateView)
        debate_view.mount(Static(self.model_menu_text(), classes="model-menu"))
        debate_view.scroll_end(animate=False)
        self.query_one("#user-input", Input).placeholder = "Choose Agent A and B model numbers, e.g. 1 2"

    async def apply_model_selection(self, message: str) -> None:
        parts = message.replace(",", " ").split()
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            self.notify("Enter two model numbers, e.g. 1 2")
            return
        model_names = self.available_model_names()
        first_index, second_index = (int(parts[0]) - 1, int(parts[1]) - 1)
        if first_index not in range(len(model_names)) or second_index not in range(len(model_names)):
            self.notify("Model number out of range")
            return

        model_a_name = model_names[first_index]
        model_b_name = model_names[second_index]
        self.orchestrator.set_models(
            self.worker_config(model_a_name), self.worker_config(model_b_name)
        )
        save_debate_state((model_a_name, model_b_name), self.state_path)
        self.model_selection_active = False
        self.query_one("#status", Static).update(self.status_text("Models updated"))
        self.query_one("#user-input", Input).placeholder = "Type the debate prompt..."
        self.notify(f"Models: {model_a_name} vs {model_b_name}")

    def available_model_names(self) -> list[str]:
        return list(self.models_yaml.get("workers", {}).keys())

    def worker_config(self, model_name: str) -> dict[str, Any]:
        config = dict(self.models_yaml["workers"][model_name])
        config["name"] = model_name
        return config

    def model_menu_text(self) -> str:
        model_names = self.available_model_names()
        lines = ["Select models: type two numbers, e.g. 1 2", "", "Agent A                 Agent B"]
        for index, model_name in enumerate(model_names, 1):
            lines.append(f"{index:>2}. {model_name:<18} {index:>2}. {model_name}")
        return "\n".join(lines)

    async def run_debate(self):
        """Main debate loop"""
        debate_view = self.query_one("#debate-view", DebateView)
        turn_number = 1

        # Agent A starts
        current_agent = "A"
        is_first_turn = True

        while self.orchestrator.should_continue():
            if self.is_paused:
                await asyncio.sleep(0.5)
                continue

            # Start turn
            turn_display = debate_view.add_turn(current_agent, turn_number)
            self.current_turn_display = turn_display
            self.active_agent = current_agent
            self.current_turn_interrupted = False

            self.orchestrator.start_turn(current_agent, is_first_turn)
            is_first_turn = False

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

            # Check for consensus
            if not was_interrupted and self.orchestrator.check_consensus(final_output):
                debate_view.show_consensus()
                break

            # Switch agent
            current_agent = "B" if current_agent == "A" else "A"
            if current_agent == "A":
                turn_number += 1

            await asyncio.sleep(1)

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

    async def action_pause(self) -> None:
        """Pause debate"""
        self.is_paused = not self.is_paused
        status = "paused" if self.is_paused else "resumed"
        self.query_one("#status", Static).update(self.status_text(status.title()))
        self.notify(f"Debate {status}")

    async def action_new_debate(self) -> None:
        """Start new debate"""
        await self.start_new_debate()
        self.notify("New debate ready")

    async def action_quit(self) -> None:
        """Quit and cleanup"""
        if self.debate_task:
            self.debate_task.cancel()
        self.orchestrator.cleanup()
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
