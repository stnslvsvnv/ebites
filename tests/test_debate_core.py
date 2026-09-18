import re
import shlex
import shutil
import time
from argparse import Namespace
from pathlib import Path

import pytest
import yaml
from textual.widgets import Input

from debate.cli import resolve_model_selection
from debate.config import (
    DEFAULT_DEBATE_CONFIG_PATH,
    DebateRuntimeConfig,
    load_debate_state,
    load_models_config,
    resolve_debate_runtime_config,
    resolve_models_config_path,
    save_debate_state,
)
from debate.orchestrator import DebateOrchestrator, prune_unsaved_debate_tmp
from debate.runner import ModelRunner
from debate.tui import ClosingOverlay, DebateApp, DebateView, TurnDisplay, markdown_to_rich


def _models_yaml():
    return {
        "workers": {
            "codex": {
                "launch": 'codex exec "{{PROMPT}}"',
                "description": "OpenAI Codex",
            },
            "deepseek-pro": {
                "launch": 'opencode run --model opencode-go/deepseek-v4-pro "{{PROMPT}}"',
                "description": "DeepSeek Pro",
            },
            "glm": {
                "launch": 'opencode run --model opencode-go/glm-5.1 "{{PROMPT}}"',
                "description": "GLM",
            },
            "deepseek-flash": {
                "launch": 'opencode run --model opencode-go/deepseek-v4-flash "{{PROMPT}}"',
                "description": "DeepSeek Flash",
            },
        },
        "profiles": {
            "current": {
                "investigation": "deepseek-pro",
                "implementation": "glm",
                "review": "deepseek-pro",
            },
            "claude": {
                "investigation": "codex",
                "implementation": "codex",
                "review": "codex",
            },
        },
    }


def _args(**overrides):
    values = {
        "models": None,
        "profile": None,
        "preset": None,
        "max_turns": None,
        "models_config": None,
    }
    values.update(overrides)
    return Namespace(**values)


class FakeTmuxClient:
    def __init__(self):
        self.sessions = set()
        self.sent_commands = []
        self.captured_output = ""
        self.killed_sessions = []

    def has_session(self, session_name):
        return session_name in self.sessions

    def new_session(self, session_name, cwd):
        self.sessions.add(session_name)

    def set_history_limit(self, session_name, limit):
        assert session_name in self.sessions

    def send_command(self, session_name, command):
        assert session_name in self.sessions
        self.sent_commands.append(command)

    def send_ctrl_c(self, session_name):
        assert session_name in self.sessions

    def clear_history(self, session_name):
        assert session_name in self.sessions

    def capture(self, session_name):
        assert session_name in self.sessions
        return self.captured_output

    def kill_session(self, session_name):
        self.killed_sessions.append(session_name)
        self.sessions.discard(session_name)


def _worker(name="codex", launch='codex exec "{{PROMPT}}"'):
    return {"name": name, "launch": launch, "description": name}


def test_resolve_model_selection_uses_named_launch_configs():
    models_yaml = _models_yaml()
    runtime_config = DebateRuntimeConfig(models=("deepseek-pro", "codex", "off"))

    model_a, model_b, model_c, model_d, model_e = resolve_model_selection(
        _args(),
        models_yaml,
        runtime_config,
    )

    assert model_a["name"] == "deepseek-pro"
    assert model_a["launch"].startswith("opencode run")
    assert model_b["name"] == "codex"
    assert model_b["launch"] == 'codex exec "{{PROMPT}}"'
    assert model_c is None
    assert model_d is None
    assert model_e is None


def test_resolve_model_selection_trims_custom_models():
    runtime_config = DebateRuntimeConfig(models=("deepseek-pro", "glm", "off"))

    model_a, model_b, model_c, model_d, model_e = resolve_model_selection(
        _args(models=" codex, glm, off "),
        _models_yaml(),
        runtime_config,
    )

    assert model_a["name"] == "codex"
    assert model_b["name"] == "glm"
    assert model_c is None
    assert model_d is None
    assert model_e is None


def test_resolve_model_selection_accepts_two_models_as_third_off():
    runtime_config = DebateRuntimeConfig(models=("deepseek-pro", "glm", "off"))

    model_a, model_b, model_c, model_d, model_e = resolve_model_selection(
        _args(models="codex,glm"),
        _models_yaml(),
        runtime_config,
    )

    assert model_a["name"] == "codex"
    assert model_b["name"] == "glm"
    assert model_c is None
    assert model_d is None
    assert model_e is None


def test_get_model_config_names_missing_model_and_lists_available(capsys):
    from debate.cli import get_model_config

    with pytest.raises(SystemExit) as exc:
        get_model_config(_models_yaml(), "no-such-model")

    assert exc.value.code == 3
    captured = capsys.readouterr()
    assert "no-such-model" in captured.err
    assert "codex" in captured.err


def test_debate_level_flags_select_preset_and_force_headless():
    from debate.cli import apply_debate_level

    for flag in ("low", "high"):
        args = _args(**{flag: True}, headless=False)
        apply_debate_level(args)

        assert args.preset == flag
        assert args.headless is True


def test_debate_level_flags_reject_explicit_model_selection(capsys):
    from debate.cli import apply_debate_level

    for conflict in ({"models": "glm,qwen"}, {"profile": "current"}, {"preset": "council"}):
        args = _args(low=True, **conflict)
        with pytest.raises(SystemExit) as exc:
            apply_debate_level(args)

        assert exc.value.code == 3
        assert "--low/--high cannot be combined" in capsys.readouterr().err


def test_shipped_config_references_only_registered_workers():
    config = yaml.safe_load(Path("debate.yaml").read_text(encoding="utf-8"))
    workers = set(config["workers"])

    referenced = set(config["default"]["models"])
    referenced.update(model for preset in config["presets"].values() for model in preset["models"])
    referenced.update(model for profile in config["profiles"].values() for model in profile.values())
    referenced.discard("off")

    assert referenced <= workers, f"unregistered workers referenced: {sorted(referenced - workers)}"


def test_debate_runtime_config_reads_default_models():
    config_yaml = {
        "default": {
            "models": ["glm", "deepseek-pro", "off"],
            "max_turns": 7,
            "display_mode": "final",
            "save_raw_output": True,
        },
        "presets": {"fast": {"models": ["deepseek-pro", "glm"], "max_turns": 3}},
    }

    runtime_config = resolve_debate_runtime_config(_args(), config_yaml)

    assert runtime_config.models == ("glm", "deepseek-pro", "off", "off", "off")
    assert runtime_config.max_turns == 7
    assert runtime_config.display_mode == "final"
    assert runtime_config.save_raw_output is True


def test_debate_runtime_config_supports_presets_and_cli_overrides():
    config_yaml = {
        "default": {"models": ["codex", "deepseek-pro", "off"], "max_turns": None},
        "presets": {"fast": {"models": ["deepseek-flash", "glm", "off"], "max_turns": 4}},
    }

    runtime_config = resolve_debate_runtime_config(
        _args(models=" codex, glm, claude ", preset="fast", max_turns=2), config_yaml
    )

    assert runtime_config.models == ("codex", "glm", "claude", "off", "off")
    assert runtime_config.max_turns == 2


def test_debate_runtime_config_uses_last_state_before_default():
    config_yaml = {"default": {"models": ["deepseek-pro", "codex", "off"], "max_turns": None}}
    state_yaml = {"last_models": ["glm", "deepseek-pro", "off"]}

    runtime_config = resolve_debate_runtime_config(_args(), config_yaml, state_yaml)

    assert runtime_config.models == ("glm", "deepseek-pro", "off", "off", "off")


def test_debate_runtime_config_cli_overrides_last_state():
    config_yaml = {"default": {"models": ["deepseek-pro", "codex", "off"]}}
    state_yaml = {"last_models": ["glm", "deepseek-pro", "off"]}

    runtime_config = resolve_debate_runtime_config(
        _args(models="codex,glm,off"), config_yaml, state_yaml
    )

    assert runtime_config.models == ("codex", "glm", "off", "off", "off")


def test_debate_runtime_config_drops_state_models_missing_from_registry():
    config_yaml = {
        "workers": {"glm": {"launch": "x"}, "qwen": {"launch": "x"}},
        "default": {"models": ["glm", "qwen", "off"]},
    }
    state_yaml = {"last_models": ["codex", "grok", "glm", "qwen", "kimi-k3"]}

    runtime_config = resolve_debate_runtime_config(_args(), config_yaml, state_yaml)

    assert runtime_config.models == ("glm", "qwen", "off", "off", "off")


def test_debate_runtime_config_falls_back_to_default_when_state_is_fully_stale():
    config_yaml = {
        "workers": {"glm": {"launch": "x"}, "qwen": {"launch": "x"}},
        "default": {"models": ["qwen", "glm", "off"]},
    }
    state_yaml = {"last_models": ["codex", "grok", "off", "off", "off"]}

    runtime_config = resolve_debate_runtime_config(_args(), config_yaml, state_yaml)

    assert runtime_config.models == ("qwen", "glm", "off", "off", "off")


def test_debate_runtime_config_rejects_less_than_two_active_models():
    config_yaml = {"default": {"models": ["codex", "off", "off"]}}

    with pytest.raises(ValueError, match="at least two active"):
        resolve_debate_runtime_config(_args(), config_yaml)


def test_debate_runtime_config_treats_yaml_false_as_off_slot():
    config_yaml = {"default": {"models": ["deepseek-pro", "codex", "off"]}}
    state_yaml = {"last_models": ["codex", "claude", False]}

    runtime_config = resolve_debate_runtime_config(_args(), config_yaml, state_yaml)

    assert runtime_config.models == ("codex", "claude", "off", "off", "off")


def test_debate_state_round_trip(tmp_path):
    state_path = tmp_path / "state.yaml"

    save_debate_state(("deepseek-pro", "codex", "off"), state_path)

    assert load_debate_state(state_path) == {
        "last_models": ["deepseek-pro", "codex", "off", "off", "off"]
    }


def test_models_config_path_defaults_to_debate_yaml():
    assert resolve_models_config_path(_args()) == DEFAULT_DEBATE_CONFIG_PATH


def test_models_config_path_cli_override_wins():
    path = resolve_models_config_path(_args(models_config="override/debate.yaml"))

    assert path == Path("override/debate.yaml")


def test_models_config_path_prefers_project_local_over_user_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    user_config = tmp_path / "xdg" / "debate" / "debate.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("workers: {}\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "debate.yaml").write_text("workers: {}\n", encoding="utf-8")
    monkeypatch.chdir(project)

    assert resolve_models_config_path(_args()) == DEFAULT_DEBATE_CONFIG_PATH


def test_models_config_path_falls_back_to_user_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    user_config = tmp_path / "xdg" / "debate" / "debate.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("workers: {}\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    assert resolve_models_config_path(_args()) == user_config


def test_models_config_path_without_any_config_keeps_default(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.chdir(tmp_path)

    assert resolve_models_config_path(_args()) == DEFAULT_DEBATE_CONFIG_PATH


def test_load_models_config_reads_workers_from_single_config(tmp_path):
    config = tmp_path / "debate.yaml"
    config.write_text(
        "workers:\n"
        "  codex:\n"
        "    launch: 'codex exec \"{{PROMPT}}\"'\n"
        "default:\n"
        "  models: [codex, deepseek-pro, \"off\"]\n",
        encoding="utf-8",
    )

    loaded = load_models_config(config)

    assert loaded["workers"]["codex"]["launch"] == 'codex exec "{{PROMPT}}"'
    assert loaded["default"]["models"] == ["codex", "deepseek-pro", "off"]


def test_skill_debate_yaml_matches_root_config():
    root = yaml.safe_load(Path("debate.yaml").read_text(encoding="utf-8"))
    skill = yaml.safe_load(Path("skills/debate/debate.yaml").read_text(encoding="utf-8"))

    assert root == skill


def test_opencode_workers_require_prompt_file_launch_for_headless_transport():
    config = yaml.safe_load(Path("debate.yaml").read_text(encoding="utf-8"))

    opencode_workers = {
        name: worker
        for name, worker in config["workers"].items()
        if "opencode run" in worker["launch"]
    }
    assert opencode_workers, "expected at least one opencode-backed worker"

    for name, worker in opencode_workers.items():
        assert "prompt_file_launch" in worker, f"worker {name} must define prompt_file_launch"
        assert "{{PROMPT_FILE}}" in worker["prompt_file_launch"]
        assert "--file {{PROMPT_FILE}}" in worker["prompt_file_launch"]
        assert "2>/dev/null" not in worker["prompt_file_launch"], "stderr must stay visible"
        assert "{{PROMPT}}" in worker["launch"]


def test_runner_uses_wrapper_script_when_no_prompt_file_launch(tmp_path):
    tmux = FakeTmuxClient()
    runner = ModelRunner(_worker(), tmux_client=tmux, session_root=tmp_path)

    process = runner.run("Discuss architecture", session_id="abc123", agent_id="A", turn_index=1)

    assert process.session_name == "debate-abc123-agent-A"
    assert process.prompt_file.read_text() == "Discuss architecture"
    assert tmux.sent_commands
    command = tmux.sent_commands[-1]
    assert "{{PROMPT}}" not in command
    assert "$(cat " not in command
    wrapper_path = process.prompt_file.with_name(process.prompt_file.stem + "_wrapper.zsh")
    assert wrapper_path.exists()
    assert str(wrapper_path) in command


def test_runner_wrapper_script_reads_prompt_file_and_invokes_launch_safely(tmp_path):
    tmux = FakeTmuxClient()
    runner = ModelRunner(_worker(), tmux_client=tmux, session_root=tmp_path)

    process = runner.run(
        'Prompt with "quotes" and $vars', session_id="abc123", agent_id="A", turn_index=1
    )

    wrapper_path = process.prompt_file.with_name(process.prompt_file.stem + "_wrapper.zsh")
    wrapper = wrapper_path.read_text(encoding="utf-8")

    assert "#!/usr/bin/env zsh" in wrapper
    assert f"__debate_prompt=$(< {shlex.quote(str(process.prompt_file))})" in wrapper
    assert 'codex exec "$__debate_prompt"' in wrapper
    assert process.prompt_file.read_text(encoding="utf-8") == 'Prompt with "quotes" and $vars'


def test_runner_can_pass_prompt_file_path_without_inlining_prompt(tmp_path):
    tmux = FakeTmuxClient()
    runner = ModelRunner(
        {
            "name": "claude",
            "launch": 'printf "%s" "{{PROMPT}}" | claude --bare --add-dir . --print',
            "prompt_file_launch": "claude --bare --add-dir . --print < {{PROMPT_FILE}}",
            "description": "Claude",
        },
        tmux_client=tmux,
        session_root=tmp_path,
    )

    process = runner.run(
        "Multiline\nprompt with spaces", session_id="abc123", agent_id="B", turn_index=2
    )

    command = tmux.sent_commands[-1]
    assert process.prompt_file.read_text() == "Multiline\nprompt with spaces"
    assert "{{PROMPT_FILE}}" not in command
    assert "{{PROMPT}}" not in command
    assert "$(cat " not in command
    assert "claude --bare --add-dir . --print < " in command
    assert str(process.prompt_file) in command


def test_max_reasoning_maps_codex_to_xhigh():
    template = 'codex exec -c model="cx/gpt-5.6-sol" -c model_reasoning_effort="high" "{{PROMPT}}"'

    rendered = ModelRunner._apply_reasoning(template, "max")

    assert 'model_reasoning_effort="xhigh"' in rendered
    assert 'model_reasoning_effort="high"' not in rendered


def test_runner_running_state_and_clean_output_use_sentinel(tmp_path):
    tmux = FakeTmuxClient()
    runner = ModelRunner(_worker(), tmux_client=tmux, session_root=tmp_path)
    process = runner.run("Prompt", session_id="abc123", agent_id="A", turn_index=1)

    tmux.captured_output = f"typed command\n{process.start_marker}\npartial output"
    assert runner.is_running(process)
    assert runner.clean_output(tmux.captured_output, process) == "partial output"

    tmux.captured_output = (
        f"typed command\n{process.start_marker}\nfinal output\n{process.sentinel}:0\nshell prompt"
    )
    assert not runner.is_running(process)
    assert runner.clean_output(tmux.captured_output, process) == "final output"


def test_orchestrator_waits_for_initial_prompt_and_builds_context():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), max_turns=1
    )

    assert not orchestrator.has_initial_prompt

    orchestrator.set_initial_prompt("Study 3A and discuss feature X")
    orchestrator.finalize_turn("A", "Proposal from A")
    orchestrator.add_user_intervention("Check performance impact")

    prompt = orchestrator.build_agent_prompt("B")

    assert "Original task: Study 3A and discuss feature X" in prompt
    assert "[Agent A]:" in prompt
    assert "Proposal from A" in prompt
    assert "[USER]:" in prompt
    assert "Check performance impact" in prompt


def test_orchestrator_prompt_is_minimal_and_requires_final_markers():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )

    prompt = orchestrator.build_agent_prompt("A", is_first_turn=True)

    assert "<DEBATE_FINAL>" in prompt
    assert "</DEBATE_FINAL>" in prompt
    assert "3A" not in prompt
    assert "graphify" not in prompt
    assert "Linear" not in prompt


def test_second_agent_prompt_includes_initial_prompt_and_first_final_answer():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Read a tiny file and comment",
    )
    orchestrator.finalize_turn("A", "<DEBATE_FINAL>First final answer</DEBATE_FINAL>")

    prompt = orchestrator.build_agent_prompt("B")

    assert "Original task: Read a tiny file and comment" in prompt
    assert "First final answer" in prompt


def test_three_agent_orchestrator_cycles_context_and_consensus_gate():
    orchestrator = DebateOrchestrator(
        agent_model_configs=(
            _worker("deepseek-pro"),
            _worker("codex"),
            _worker("glm"),
        ),
        initial_prompt="Task",
    )

    assert orchestrator.active_agent_ids == ["A", "B", "C"]
    assert orchestrator.next_agent_id("A") == "B"
    assert orchestrator.next_agent_id("B") == "C"
    assert orchestrator.next_agent_id("C") == "A"

    prompt = orchestrator.build_agent_prompt("C")
    assert "three-agent debate" in prompt
    assert "Agent A, Agent B, Agent C" in prompt

    orchestrator.finalize_turn("A", "A answer")
    orchestrator.finalize_turn("B", "B answer")
    third = orchestrator.finalize_turn("C", "CONSENSUS: too early")
    assert orchestrator.check_consensus(third)
    assert not orchestrator.can_accept_consensus(third)
    assert orchestrator.should_continue()

    fourth = orchestrator.finalize_turn("A", "CONSENSUS: after all agents spoke")
    assert orchestrator.can_accept_consensus(fourth)
    assert not orchestrator.should_continue()


def test_three_agent_max_turns_counts_full_rounds():
    orchestrator = DebateOrchestrator(
        agent_model_configs=(
            _worker("deepseek-pro"),
            _worker("codex"),
            _worker("glm"),
        ),
        initial_prompt="Task",
        max_turns=1,
    )

    orchestrator.finalize_turn("A", "A response")
    assert orchestrator.should_continue()
    orchestrator.finalize_turn("B", "B response")
    assert orchestrator.should_continue()
    orchestrator.finalize_turn("C", "C response")
    assert not orchestrator.should_continue()


def test_orchestrator_stores_only_final_answer_and_saves_raw_output(tmp_path):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"),
        model_b_config=_worker("deepseek"),
        initial_prompt="Task",
        save_raw_output=True,
    )
    orchestrator.session_dir = tmp_path
    raw_output = "tool noise\n<DEBATE_FINAL>\nFinal answer only\n</DEBATE_FINAL>\nmore noise"

    orchestrator.finalize_turn("A", raw_output)

    assert orchestrator.conversation_history[-1]["response"] == "Final answer only"
    raw_file = tmp_path / "raw" / "turn_001_agent_A.txt"
    assert raw_file.read_text(encoding="utf-8") == raw_output


def test_save_transcript_includes_initial_prompt_and_full_history(tmp_path):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Original task",
    )
    orchestrator.session_dir = tmp_path
    orchestrator.finalize_turn("A", "<DEBATE_FINAL>Agent A answer</DEBATE_FINAL>")
    orchestrator.finalize_turn("B", "<DEBATE_FINAL>Agent B answer</DEBATE_FINAL>")
    orchestrator.add_user_intervention("User clarification")

    transcript_path = orchestrator.save_transcript()
    transcript = transcript_path.read_text(encoding="utf-8")

    assert "## Initial Prompt" in transcript
    assert "Original task" in transcript
    assert "Agent A answer" in transcript
    assert "Agent B answer" in transcript
    assert "User clarification" in transcript


def test_cleanup_removes_unsaved_debate_session_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    marker = orchestrator.session_dir / "raw" / "turn.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("raw output", encoding="utf-8")

    session_dir = orchestrator.session_dir
    orchestrator.cleanup()

    assert not session_dir.exists()


def test_cleanup_preserves_saved_debate_session_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    session_dir = orchestrator.session_dir

    transcript_path = orchestrator.save_transcript()
    orchestrator.cleanup()

    assert session_dir.exists()
    assert transcript_path.exists()


def test_reset_removes_previous_unsaved_debate_session_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    old_session_dir = orchestrator.session_dir
    (old_session_dir / "turn.txt").write_text("prompt", encoding="utf-8")

    orchestrator.reset(initial_prompt="Next task")

    assert not old_session_dir.exists()
    assert orchestrator.session_dir.exists()
    assert orchestrator.session_dir != old_session_dir


def test_prune_unsaved_debate_tmp_deletes_only_unsaved_inactive_dirs(tmp_path):
    tmp_root = tmp_path / ".debate" / "tmp"
    unsaved = tmp_root / "debate-unsaved"
    saved = tmp_root / "debate-saved"
    active = tmp_root / "debate-active"
    other = tmp_root / "1a-run"
    for path in (unsaved, saved, active, other):
        path.mkdir(parents=True)
    (saved / "transcript.md").write_text("saved", encoding="utf-8")

    removed = prune_unsaved_debate_tmp(
        tmp_root, is_session_active=lambda session_name: session_name == "debate-active"
    )

    assert removed == [unsaved]
    assert not unsaved.exists()
    assert saved.exists()
    assert active.exists()
    assert other.exists()


def test_prune_unsaved_debate_tmp_tolerates_concurrent_removal(tmp_path):
    tmp_root = tmp_path / ".debate" / "tmp"
    stale = tmp_root / "debate-stale"
    stale.mkdir(parents=True)

    def lose_race_to_other_debate(session_name):
        # A concurrent debate deletes the same stale dir after this process
        # already globbed it, so the rmtree below targets a missing path.
        shutil.rmtree(stale)
        return False

    assert prune_unsaved_debate_tmp(tmp_root, is_session_active=lose_race_to_other_debate) == []
    assert not stale.exists()


def test_orchestrator_final_answer_fallback_uses_tail_without_marker():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )
    raw_output = "line 1\nline 2\nline 3\nline 4"

    assert orchestrator.extract_final_answer(raw_output) == "line 1\nline 2\nline 3\nline 4"


def test_orchestrator_max_turns_counts_full_rounds():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"),
        model_b_config=_worker("deepseek"),
        initial_prompt="Task",
        max_turns=1,
    )

    assert orchestrator.should_continue()
    orchestrator.finalize_turn("A", "A response")
    assert orchestrator.should_continue()
    orchestrator.finalize_turn("B", "B response")
    assert not orchestrator.should_continue()


def test_orchestrator_prompt_requires_structured_consensus_rationale():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )

    prompt = orchestrator.build_agent_prompt("A", is_first_turn=True)

    assert "CONSENSUS:" in prompt
    assert "1. Decision:" in prompt
    assert "2. Key arguments:" in prompt
    assert "3. Residual risks" in prompt
    assert "4. Next step:" in prompt


def test_consensus_requires_response_prefix():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )

    assert orchestrator.check_consensus("CONSENSUS: ship it")
    assert orchestrator.check_consensus("\n  CONSENSUS: ship it")
    assert not orchestrator.check_consensus("The prompt mentioned CONSENSUS: but no agreement")


def test_consensus_is_not_accepted_before_second_round():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )

    prompt = orchestrator.build_agent_prompt("A", is_first_turn=True)

    assert "Do not use CONSENSUS on your first turn" in prompt

    first_response = orchestrator.finalize_turn("A", "CONSENSUS: premature")
    assert orchestrator.check_consensus(first_response)
    assert not orchestrator.can_accept_consensus(first_response)
    assert orchestrator.should_continue()

    second_response = orchestrator.finalize_turn("B", "CONSENSUS: still too early")
    assert orchestrator.check_consensus(second_response)
    assert not orchestrator.can_accept_consensus(second_response)
    assert orchestrator.should_continue()

    third_response = orchestrator.finalize_turn("A", "CONSENSUS: now this is grounded")
    assert orchestrator.can_accept_consensus(third_response)
    assert not orchestrator.should_continue()

    orchestrator.add_user_intervention("Continue and compare another trade-off")
    assert orchestrator.should_continue()


@pytest.mark.asyncio
async def test_tui_waits_for_initial_prompt_before_starting_debate():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("codex"),
            model_b_config=_worker("deepseek"),
        )
    )

    async with app.run_test():
        assert app.debate_task is None
        assert not app.orchestrator.has_initial_prompt


@pytest.mark.asyncio
async def test_tui_does_not_mount_ascii_logo():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("codex"),
            model_b_config=_worker("deepseek"),
        )
    )

    async with app.run_test():
        assert len(app.query("#logo")) == 0


def test_tui_uses_textual_default_theme_tokens():
    css = DebateApp.CSS

    # Custom muted palette (ink-violet editorial scheme) — no default
    # $surface/$primary/$accent tokens expected anymore.
    assert "#14131a" in css
    assert "#221f2a" in css
    assert "#4a4560" in css
    assert "$surface" not in css
    assert "$primary" not in css
    assert "$accent" not in css
    assert "theme-night" not in css


def test_tui_status_label_uses_worker_names():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )

    assert "Agent A: deepseek-pro" in app.status_text("Enter initial prompt")
    assert "Agent B: codex" in app.status_text("Enter initial prompt")


def test_turn_display_uses_textual_border_title_not_manual_box():
    turn = TurnDisplay("A", 2)

    assert turn.border_title == "Turn 2 - Agent A"
    assert str(turn.render()) == "Running..."
    assert "┌" not in str(turn.render())
    assert "└" not in str(turn.render())
    assert turn.has_class("thinking")

    turn.update_content("Final answer", finished=True)

    assert str(turn.render()) == "Final answer"
    assert not turn.has_class("thinking")
    assert turn.has_class("finished")


def test_model_menu_lists_each_model_once_with_its_description():
    models_yaml = {
        "workers": {
            "glm": {"launch": "run glm", "description": "GLM via opencode-go"},
            "qwen": {"launch": "run qwen", "description": "Qwen via opencode-go"},
        }
    }
    app = DebateApp(
        DebateOrchestrator(
            model_a_config={"name": "glm", "launch": "run glm"},
            model_b_config={"name": "qwen", "launch": "run qwen"},
        ),
        models_yaml=models_yaml,
    )

    numbered = [
        line.strip()
        for line in app.model_menu_text().splitlines()
        if re.match(r"\d+\. ", line.strip())
    ]

    assert len(numbered) == 3
    for index, name in enumerate(app.available_model_names(), 1):
        matching = [line for line in numbered if line.startswith(f"{index}. {name}")]
        assert len(matching) == 1, f"{name} must have exactly one entry"
        assert matching[0].count(name) == 1, f"{name} must be listed once, not once per column"


@pytest.mark.asyncio
async def test_tui_shows_initial_prompt_before_first_turn(monkeypatch):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )

    async def fake_run_debate():
        return None

    monkeypatch.setattr(app, "run_debate", fake_run_debate)

    async with app.run_test():
        await app.handle_user_message("Read the tiny file and comment")
        debate_view = app.query_one("#debate-view", DebateView)
        prompt_display = app.query_one("#initial-prompt")
        turn_display = debate_view.add_turn("A", 1)

        children = list(debate_view.children)

    assert prompt_display.border_title == "Initial prompt"
    assert str(prompt_display.render()) == "Read the tiny file and comment"
    assert children.index(prompt_display) < children.index(turn_display)


def test_tui_css_defines_active_turn_pulse_and_closing_blink():
    css = DebateApp.CSS

    assert "TurnDisplay.thinking.pulse-on" in css
    assert "#closing-dots.blink-off" in css


def test_tui_status_strip_has_compact_top_gap():
    css = DebateApp.CSS

    assert "#status" in css
    assert "height: 8;" in css
    assert "padding: 1 1 0 1;" in css


def test_initial_prompt_frame_uses_plain_text_border():
    css = DebateApp.CSS

    assert "PromptDisplay {" in css
    assert "border: round #4a4560;" in css
    assert "TurnDisplay {" in css
    assert "border: round #4a4560;" in css


@pytest.mark.asyncio
async def test_tui_input_suggests_slash_commands():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )

    async with app.run_test():
        input_widget = app.query_one("#user-input", Input)

        assert input_widget.suggester is not None
        assert await input_widget.suggester.get_suggestion("/") == "/new"
        assert await input_widget.suggester.get_suggestion("/mo") == "/models"
        assert await input_widget.suggester.get_suggestion("/sa") == "/save"


@pytest.mark.asyncio
async def test_tui_closing_overlay_is_centered_and_blinks():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )

    async with app.run_test():
        await app.show_closing_overlay()
        overlay = app.query_one("#closing-overlay", ClosingOverlay)
        label = app.query_one("#closing-label")
        dots = app.query_one("#closing-dots")

        assert overlay.border_title is None
        assert str(label.render()) == "App closing, wait"
        assert str(dots.render()) == "..."

        app._pulse_on = True
        app._tick_pulse()

        assert dots.has_class("blink-off")
        assert str(dots.render()) == "   "


@pytest.mark.asyncio
async def test_tui_quit_shows_closing_overlay_before_cleanup(monkeypatch):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
    )
    app = DebateApp(orchestrator)
    events = []

    async def fake_show_closing_overlay():
        events.append("overlay")

    monkeypatch.setattr(app, "show_closing_overlay", fake_show_closing_overlay)
    monkeypatch.setattr(orchestrator, "cleanup", lambda: events.append("cleanup"))
    # double Ctrl+C: prime first press so action_quit triggers _do_quit immediately
    app._last_quit_press = time.monotonic()

    async with app.run_test():
        await app.action_quit()

    assert events[:2] == ["overlay", "cleanup"]


@pytest.mark.asyncio
async def test_tui_models_menu_selects_two_numbered_models(tmp_path):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        ),
        models_yaml=_models_yaml(),
        state_path=tmp_path / "state.yaml",
    )

    async with app.run_test():
        await app.handle_user_message("/models")
        assert app.model_selection_active
        await app.handle_user_message("3 1")

    assert app.orchestrator.model_a.model == "glm"
    assert app.orchestrator.model_b.model == "codex"
    assert app.orchestrator.model_c is None
    assert load_debate_state(tmp_path / "state.yaml") == {
        "last_models": ["glm", "codex", "off", "off", "off"]
    }


@pytest.mark.asyncio
async def test_tui_models_menu_selects_three_agents_and_off(tmp_path):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        ),
        models_yaml=_models_yaml(),
        state_path=tmp_path / "state.yaml",
    )

    async with app.run_test():
        await app.handle_user_message("/models")
        menu_text = str(app.query(".model-menu").last().render())
        assert "off" in menu_text
        await app.handle_user_message("3 1 5")

    assert app.orchestrator.active_agent_ids == ["A", "B"]
    assert app.orchestrator.model_a.model == "glm"
    assert app.orchestrator.model_b.model == "codex"
    assert app.orchestrator.model_c is None
    assert load_debate_state(tmp_path / "state.yaml") == {
        "last_models": ["glm", "codex", "off", "off", "off"]
    }


def _contrast_ratio(color_a, color_b) -> float:
    def lin(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    def luminance(color) -> float:
        return 0.2126 * lin(color.r) + 0.7152 * lin(color.g) + 0.0722 * lin(color.b)

    high, low = sorted((luminance(color_a), luminance(color_b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.asyncio
async def test_thinking_pulse_stays_visually_distinct_from_resting_border():
    app = DebateApp(
        DebateOrchestrator(model_a_config=_worker("glm"), model_b_config=_worker("qwen")),
    )

    async with app.run_test() as pilot:
        debate_view = app.query_one("#debate-view", DebateView)
        turn = TurnDisplay("A", 1)
        turn.add_class("pulse-on")
        await debate_view.mount(turn)
        await pilot.pause()
        _, pulse_color = turn.styles.border_top

        turn.remove_class("pulse-on")
        await pilot.pause()
        _, resting_color = turn.styles.border_top

    # The thinking indicator is a blinking border. If the two states sit too
    # close together the blink is imperceptible no matter that it still toggles,
    # so hold the pulse to the WCAG non-text UI floor of 3:1.
    assert _contrast_ratio(pulse_color, resting_color) >= 3.0


@pytest.mark.asyncio
async def test_tui_models_menu_reopens_without_duplicate_panel(tmp_path):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        ),
        models_yaml=_models_yaml(),
        state_path=tmp_path / "state.yaml",
    )

    async with app.run_test():
        await app.handle_user_message("/models")
        await app.handle_user_message("/models")

        assert len(app.query(".model-menu")) == 1


@pytest.mark.asyncio
async def test_tui_models_menu_rejects_single_active_agent(tmp_path):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        ),
        models_yaml=_models_yaml(),
        state_path=tmp_path / "state.yaml",
    )
    notifications = []
    app.notify = lambda message: notifications.append(message)

    async with app.run_test():
        await app.handle_user_message("/models")
        await app.handle_user_message("1 5 5")

    assert app.model_selection_active
    assert any("at least two active" in message for message in notifications)


@pytest.mark.asyncio
async def test_next_prompt_after_finished_starts_new_debate(monkeypatch):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )
    starts = []

    async def fake_run_debate():
        starts.append(app.orchestrator.initial_prompt)
        app.debate_finished = True

    monkeypatch.setattr(app, "run_debate", fake_run_debate)

    async with app.run_test():
        await app.handle_user_message("first prompt")
        await app.debate_task
        await app.handle_user_message("second prompt")
        await app.debate_task

    assert starts == ["first prompt", "second prompt"]


@pytest.mark.asyncio
async def test_tui_awaits_choice_and_keeps_sessions_after_consensus(monkeypatch):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    cleanup_calls = []

    monkeypatch.setattr(orchestrator, "start_turn", lambda *args, **kwargs: "session")
    monkeypatch.setattr(orchestrator, "is_agent_running", lambda agent_id: False)
    monkeypatch.setattr(
        orchestrator,
        "capture_agent_output",
        lambda agent_id: "<DEBATE_FINAL>CONSENSUS: done</DEBATE_FINAL>",
    )
    monkeypatch.setattr(orchestrator, "can_accept_consensus", lambda response: True)
    monkeypatch.setattr(orchestrator, "cleanup", lambda: cleanup_calls.append(True))

    app = DebateApp(orchestrator)

    async with app.run_test():
        await app.run_debate()
        input_widget = app.query_one("#user-input", Input)

    assert cleanup_calls == []
    assert app.awaiting_consensus_action
    assert app.debate_finished
    assert app.next_agent == "B"
    assert input_widget.placeholder == "Consensus: Enter/new | save | continue"


@pytest.mark.asyncio
async def test_consensus_enter_defaults_to_new_and_closes_sessions(monkeypatch):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    cleanup_calls = []

    monkeypatch.setattr(orchestrator, "cleanup", lambda: cleanup_calls.append(True))

    app = DebateApp(orchestrator)
    app.awaiting_consensus_action = True

    async with app.run_test():
        event = Input.Submitted(app.query_one("#user-input", Input), "")
        await app.on_input_submitted(event)

    assert cleanup_calls == [True]
    assert not app.awaiting_consensus_action
    assert not orchestrator.has_initial_prompt


@pytest.mark.asyncio
async def test_consensus_save_closes_sessions_and_keeps_transcript_visible(monkeypatch, tmp_path):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    orchestrator.session_dir = tmp_path
    cleanup_calls = []

    monkeypatch.setattr(orchestrator, "cleanup", lambda: cleanup_calls.append(True))

    app = DebateApp(orchestrator)
    app.awaiting_consensus_action = True

    async with app.run_test():
        await app.handle_user_message("save")

    assert cleanup_calls == [True]
    assert not app.awaiting_consensus_action
    assert app.debate_finished
    assert (tmp_path / "transcript.md").exists()


@pytest.mark.asyncio
async def test_consensus_continue_keeps_sessions_and_resumes_debate(monkeypatch):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    cleanup_calls = []
    resumed = []

    monkeypatch.setattr(orchestrator, "cleanup", lambda: cleanup_calls.append(True))

    app = DebateApp(orchestrator)
    app.awaiting_consensus_action = True
    app.debate_finished = True
    app.next_agent = "B"
    app.next_turn_number = 2

    async def fake_run_debate():
        resumed.append(app.next_agent)

    monkeypatch.setattr(app, "run_debate", fake_run_debate)

    async with app.run_test():
        await app.handle_user_message("continue")
        await app.debate_task
        user_lines = [str(child.render()) for child in app.query_one("#debate-view").children]

    assert cleanup_calls == []
    assert resumed == ["A"]
    assert app.next_turn_number == 3
    assert not app.awaiting_consensus_action
    assert not app.debate_finished
    assert orchestrator.conversation_history[-1]["agent"] == "USER"
    assert any("[USER]: Continue after consensus." in line for line in user_lines)


@pytest.mark.asyncio
async def test_tui_rings_terminal_bell_after_consensus(monkeypatch):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    consensus_notifications = []

    monkeypatch.setattr(orchestrator, "start_turn", lambda *args, **kwargs: "session")
    monkeypatch.setattr(orchestrator, "is_agent_running", lambda agent_id: False)
    monkeypatch.setattr(
        orchestrator,
        "capture_agent_output",
        lambda agent_id: "<DEBATE_FINAL>CONSENSUS: done</DEBATE_FINAL>",
    )
    monkeypatch.setattr(orchestrator, "cleanup", lambda: None)

    app = DebateApp(orchestrator)
    monkeypatch.setattr(app, "notify_consensus", lambda: consensus_notifications.append("bell"))

    async with app.run_test():
        await app.run_debate()

    assert consensus_notifications == ["bell"]


def test_notify_consensus_uses_terminal_bell(monkeypatch):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )
    bells = []

    monkeypatch.setattr(app, "bell", lambda: bells.append("bel"))

    app.notify_consensus()

    assert bells == ["bel"]


class TestMarkdownToRich:
    def test_bold_conversion(self):
        result = markdown_to_rich("**fat**")
        assert "[bold]fat[/bold]" in result

    def test_italic_conversion(self):
        result = markdown_to_rich("*italic*")
        assert "[italic]italic[/italic]" in result

    def test_bold_italic_conversion(self):
        result = markdown_to_rich("***fat italic***")
        assert "[bold italic]fat italic[/bold italic]" in result

    def test_code_conversion(self):
        result = markdown_to_rich("`code`")
        assert "[bold yellow]code[/bold yellow]" in result

    def test_heading_conversion(self):
        result = markdown_to_rich("## Heading")
        assert "[bold underline]Heading[/bold underline]" in result

    def test_ansi_escape_stripped(self):
        result = markdown_to_rich(r"\033[31mRed text\033[0m")
        assert "[31m" not in result
        assert "\033[" not in result

    def test_ansi_with_semicolon_stripped(self):
        result = markdown_to_rich(r"\033[1;32mBold green\033[0m")
        assert "[1;32m" not in result
        assert "[0m" not in result

    def test_ansi_and_markdown_together(self):
        result = markdown_to_rich(r"\033[31mRed\033[0m and **bold**")
        assert "Red" in result
        assert "bold" in result
        assert "[bold]bold[/bold]" in result

    def test_bracket_escaped(self):
        result = markdown_to_rich("[task list]")
        assert "[[task list]]" in result

    def test_markdown_link_brackets_preserved(self):
        result = markdown_to_rich("[opencode](url)")
        assert "[[opencode]](url)" in result

    def test_footnote_brackets_preserved(self):
        result = markdown_to_rich("text[^1]")
        assert "[[^1]]" in result

    def test_mixed_brackets_and_markdown(self):
        result = markdown_to_rich("**fat** [link](url) *italic*")
        assert "[bold]fat[/bold]" in result
        assert "[[link]](url)" in result
        assert "[italic]italic[/italic]" in result

    def test_whitespace_passthrough(self):
        result = markdown_to_rich("   ")
        assert result == "   "

    def test_empty_passthrough(self):
        result = markdown_to_rich("")
        assert result == ""

    def test_plain_text_passthrough(self):
        result = markdown_to_rich("normal text")
        assert "[bold]" not in result
        assert result == "normal text"

    def test_code_with_brackets_inside(self):
        result = markdown_to_rich("`[code]`")
        assert "[bold yellow][[code]][/bold yellow]" in result

    def test_from_markup_compatibility(self):
        from textual.content import Content

        text = r"\033[31mRed\033[0m **bold** *italic* `code` [link](url)"
        rich = markdown_to_rich(text)
        c = Content.from_markup(rich)
        assert len(c.spans) >= 3
