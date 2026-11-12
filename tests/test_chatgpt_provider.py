"""Tests for the ChatGPT provider, which runs ChatGPT-plan models through ``codex exec``.

Every test replaces ``run_cancellable``; none starts the Codex CLI or touches the network.
"""

import base64
import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

import providers.chatgpt as chatgpt_module
from providers.chatgpt import (
    ChatGPTCLIError,
    ChatGPTModelProvider,
    codex_login_problem,
    parse_codex_events,
)
from providers.shared import ProviderType
from tools.models import ToolModelCategory

_FAKE_CODEX = "/opt/fake/bin/codex"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _events(*events) -> bytes:
    return "\n".join(json.dumps(event) for event in events).encode("utf-8")


def _reply(text="OK", usage=None) -> bytes:
    return _events(
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}},
        {
            "type": "turn.completed",
            "usage": usage
            or {"input_tokens": 120, "cached_input_tokens": 100, "output_tokens": 5, "reasoning_output_tokens": 2},
        },
    )


class FakeCodex:
    """Stands in for ``run_cancellable`` and records each call."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, command, **kwargs):
        cwd = Path(kwargs["cwd"])
        self.calls.append(
            {
                "command": list(command),
                "kwargs": kwargs,
                "cwd_exists": cwd.is_dir(),
                "cwd_entries": sorted(p.name for p in cwd.iterdir()) if cwd.is_dir() else None,
                "images": [Path(command[i + 1]).read_bytes() for i, arg in enumerate(command) if arg == "--image"],
            }
        )
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        stdout, stderr, returncode = result
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(chatgpt_module.shutil, "which", lambda name: _FAKE_CODEX)
    monkeypatch.setattr(chatgpt_module.time, "sleep", lambda _seconds: None)
    return ChatGPTModelProvider()


def _use(monkeypatch, *results) -> FakeCodex:
    fake = FakeCodex(*results)
    monkeypatch.setattr(chatgpt_module, "run_cancellable", fake)
    return fake


def _config_values(command):
    return [command[i + 1] for i, arg in enumerate(command) if arg == "--config"]


# --------------------------------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------------------------------


class TestCatalog:
    def test_every_model_is_namespaced_and_matches_its_capabilities(self, provider):
        capabilities = provider.get_all_model_capabilities()

        assert capabilities
        for name, caps in capabilities.items():
            assert name.startswith("chatgpt/")
            assert caps.provider == ProviderType.CHATGPT
            assert caps.supports_temperature is False
            assert caps.supports_images is True
            assert caps.effort_levels

    def test_aliases_resolve_case_insensitively(self, provider):
        assert provider.get_capabilities("chatgpt/sol").model_name == "chatgpt/gpt-6.1-sol"
        assert provider.get_capabilities("CHATGPT/GPT-6-LUNA").model_name == "chatgpt/gpt-6-luna"

    @pytest.mark.parametrize("name", ["gpt-6.1-sol", "sol", "codex", "openai/gpt-6.1-sol", "gpt-5.5"])
    def test_does_not_claim_other_providers_names(self, provider, name):
        assert provider.validate_model_name(name) is False

    def test_restriction_env_limits_the_models(self, monkeypatch):
        import utils.model_restrictions as restrictions

        monkeypatch.setenv("CHATGPT_ALLOWED_MODELS", "chatgpt/gpt-6-luna")
        monkeypatch.setattr(restrictions, "_restriction_service", None)
        try:
            restricted = ChatGPTModelProvider()
            assert restricted.validate_model_name("chatgpt/luna") is True
            assert restricted.validate_model_name("chatgpt/sol") is False
        finally:
            monkeypatch.setattr(restrictions, "_restriction_service", None)


# --------------------------------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------------------------------


class TestBuildCommand:
    def test_model_only_flags(self):
        command = ChatGPTModelProvider.build_command(
            executable=_FAKE_CODEX,
            slug="gpt-6-luna",
            workspace="/tmp/ws",
            effort="high",
            system_prompt=None,
            image_paths=[],
        )

        assert command[:2] == [_FAKE_CODEX, "exec"]
        for flag in ("--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"):
            assert flag in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--cd") + 1] == "/tmp/ws"
        assert command[command.index("--model") + 1] == "gpt-6-luna"
        assert 'forced_login_method="chatgpt"' in _config_values(command)
        assert 'model_reasoning_effort="high"' in _config_values(command)
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        assert command[-1] == "-"

    def test_no_effort_flag_without_effort(self):
        command = ChatGPTModelProvider.build_command(
            executable="codex", slug="m", workspace="/w", effort=None, system_prompt=None, image_paths=[]
        )
        assert not any(value.startswith("model_reasoning_effort") for value in _config_values(command))

    def test_system_prompt_is_a_toml_string_after_the_model_only_note(self):
        system_prompt = 'Say "hi" \\ then\nstop. é 😀'
        command = ChatGPTModelProvider.build_command(
            executable="codex", slug="m", workspace="/w", effort=None, system_prompt=system_prompt, image_paths=[]
        )

        (value,) = [v for v in _config_values(command) if v.startswith("developer_instructions=")]
        # A JSON string with literal non-ASCII is a valid TOML basic string.
        instructions = json.loads(value.split("=", 1)[1])
        assert instructions.startswith(chatgpt_module._MODEL_ONLY_NOTE)
        assert instructions.endswith(system_prompt)
        assert "\\ud83d" not in value  # no surrogate-pair escapes, which TOML rejects

    def test_images_come_before_the_next_flag_not_before_stdin(self):
        # --image takes several values, so an image right before "-" would swallow it.
        command = ChatGPTModelProvider.build_command(
            executable="codex", slug="m", workspace="/w", effort=None, system_prompt=None, image_paths=["/a.png"]
        )
        index = command.index("--image")
        assert command[index + 1] == "/a.png"
        assert command[index + 2].startswith("--")
        assert command[-2] != "/a.png"


# --------------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------------


class TestGenerateContent:
    def test_returns_the_reply_with_usage(self, provider, monkeypatch):
        fake = _use(monkeypatch, (_reply("OK"), b"", 0))

        response = provider.generate_content("Reply with OK", "chatgpt/luna")

        assert response.content == "OK"
        assert response.model_name == "chatgpt/gpt-6-luna"
        assert response.provider == ProviderType.CHATGPT
        assert response.friendly_name == "ChatGPT"
        assert response.usage == {"input_tokens": 120, "output_tokens": 5, "total_tokens": 125}
        assert response.metadata["cached_input_tokens"] == 100
        assert response.metadata["reasoning_effort"] == "medium"
        assert len(fake.calls) == 1

    def test_prompt_goes_on_stdin_and_system_prompt_in_instructions(self, provider, monkeypatch):
        fake = _use(monkeypatch, (_reply(), b"", 0))

        provider.generate_content("USER PROMPT", "chatgpt/gpt-6-sol", system_prompt="SYSTEM PROMPT")

        call = fake.calls[0]
        assert call["kwargs"]["input"] == b"USER PROMPT"
        assert "USER PROMPT" not in " ".join(call["command"])
        assert any("SYSTEM PROMPT" in value for value in _config_values(call["command"]))
        assert call["command"][call["command"].index("--model") + 1] == "gpt-6-sol"

    def test_runs_in_an_empty_temp_workspace_that_is_removed(self, provider, monkeypatch):
        fake = _use(monkeypatch, (_reply(), b"", 0))

        provider.generate_content("hi", "chatgpt/luna")

        call = fake.calls[0]
        workspace = call["command"][call["command"].index("--cd") + 1]
        assert call["kwargs"]["cwd"] == workspace
        assert call["cwd_exists"] is True
        assert call["cwd_entries"] == []
        assert not Path(workspace).exists()

    def test_strips_api_keys_from_the_child_environment(self, provider, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
        monkeypatch.setenv("CODEX_API_KEY", "also-not")
        fake = _use(monkeypatch, (_reply(), b"", 0))

        provider.generate_content("hi", "chatgpt/luna")

        env = fake.calls[0]["kwargs"]["env"]
        assert "OPENAI_API_KEY" not in env
        assert "CODEX_API_KEY" not in env
        assert env.get("PATH") == os.environ.get("PATH")

    @pytest.mark.parametrize(
        "thinking_mode,expected",
        [("minimal", "low"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "max"), (None, "medium")],
    )
    def test_thinking_mode_maps_to_reasoning_effort(self, provider, monkeypatch, thinking_mode, expected):
        fake = _use(monkeypatch, (_reply(), b"", 0))

        provider.generate_content("hi", "chatgpt/luna", thinking_mode=thinking_mode)

        assert f'model_reasoning_effort="{expected}"' in _config_values(fake.calls[0]["command"])

    def test_temperature_and_max_output_tokens_are_ignored(self, provider, monkeypatch):
        fake = _use(monkeypatch, (_reply(), b"", 0))

        provider.generate_content("hi", "chatgpt/luna", temperature=0.2, max_output_tokens=50)

        joined = " ".join(fake.calls[0]["command"])
        assert "temperature" not in joined
        assert "max_output_tokens" not in joined

    def test_images_are_copied_and_attached(self, provider, monkeypatch, tmp_path):
        image_file = tmp_path / "shot.png"
        image_file.write_bytes(_PNG)
        data_url = "data:image/png;base64," + base64.b64encode(_PNG).decode()
        fake = _use(monkeypatch, (_reply(), b"", 0))

        provider.generate_content("describe", "chatgpt/luna", images=[str(image_file), data_url])

        call = fake.calls[0]
        assert call["images"] == [_PNG, _PNG]
        assert call["cwd_entries"] == []  # images sit outside the workspace Codex is pointed at

    def test_invalid_images_are_skipped(self, provider, monkeypatch, tmp_path):
        fake = _use(monkeypatch, (_reply(), b"", 0))

        provider.generate_content("describe", "chatgpt/luna", images=[str(tmp_path / "missing.png")])

        assert "--image" not in fake.calls[0]["command"]

    def test_last_agent_message_is_the_reply(self, provider, monkeypatch):
        stdout = _events(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Let me think."}},
            {"type": "item.completed", "item": {"type": "reasoning", "text": "hidden"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Final answer"}},
        )
        _use(monkeypatch, (stdout, b"", 0))

        assert provider.generate_content("hi", "chatgpt/luna").content == "Final answer"

    def test_a_reply_wins_over_a_nonzero_exit(self, provider, monkeypatch):
        _use(monkeypatch, (_reply("OK"), b"warning", 1))

        assert provider.generate_content("hi", "chatgpt/luna").content == "OK"

    def test_logs_omit_prompt_and_system_prompt(self, provider, monkeypatch, caplog):
        _use(monkeypatch, (_reply(), b"", 0))

        with caplog.at_level(logging.DEBUG, logger="providers.chatgpt"):
            provider.generate_content("SECRET-PROMPT", "chatgpt/luna", system_prompt="SECRET-SYSTEM")

        assert "SECRET-PROMPT" not in caplog.text
        assert "SECRET-SYSTEM" not in caplog.text
        assert "developer_instructions=<" in caplog.text

    def test_timeout_setting_is_passed_to_subprocess(self, monkeypatch):
        monkeypatch.setattr(chatgpt_module.shutil, "which", lambda name: _FAKE_CODEX)
        monkeypatch.setenv("CHATGPT_TIMEOUT_SECONDS", "42")
        fake = _use(monkeypatch, (_reply(), b"", 0))

        ChatGPTModelProvider().generate_content("hi", "chatgpt/luna")

        assert fake.calls[0]["kwargs"]["timeout"] == 42

    @pytest.mark.parametrize("raw", ["soon", "0", "-5"])
    def test_bad_timeout_setting_falls_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("CHATGPT_TIMEOUT_SECONDS", raw)
        assert ChatGPTModelProvider()._timeout_seconds == chatgpt_module.DEFAULT_TIMEOUT_SECONDS

    def test_custom_codex_command(self, monkeypatch):
        looked_up = []
        monkeypatch.setattr(chatgpt_module.shutil, "which", lambda name: looked_up.append(name) or "/x/my-codex")
        monkeypatch.setenv("CHATGPT_CODEX_COMMAND", "my-codex")
        fake = _use(monkeypatch, (_reply(), b"", 0))

        ChatGPTModelProvider().generate_content("hi", "chatgpt/luna")

        assert looked_up == ["my-codex"]
        assert fake.calls[0]["command"][0] == "/x/my-codex"

    def test_unknown_model_is_rejected_before_running_codex(self, provider, monkeypatch):
        fake = _use(monkeypatch)

        with pytest.raises(ValueError):
            provider.generate_content("hi", "chatgpt/gpt-9")
        assert fake.calls == []


class TestTimeouts:
    """Each call's limit: the model's value for the effort, then the model's value, then the default."""

    @pytest.fixture
    def custom_catalogue(self, monkeypatch, tmp_path):
        """Load a one-model catalogue with its own limits, and restore the shipped one afterwards."""

        def load(**limits):
            entry = {
                "model_name": "chatgpt/test-model",
                "aliases": ["chatgpt/test"],
                "effort_levels": ["low", "medium", "high"],
                "supports_temperature": False,
                **limits,
            }
            path = tmp_path / "chatgpt_models.json"
            path.write_text(json.dumps({"models": [entry]}))
            monkeypatch.setenv("CHATGPT_MODELS_CONFIG_PATH", str(path))
            ChatGPTModelProvider.reload_registry()

        yield load
        monkeypatch.delenv("CHATGPT_MODELS_CONFIG_PATH", raising=False)
        ChatGPTModelProvider.reload_registry()

    def _timeout_of_call(self, monkeypatch, model, thinking_mode=None):
        fake = _use(monkeypatch, (_reply(), b"", 0))
        ChatGPTModelProvider().generate_content("hi", model, thinking_mode=thinking_mode)
        return fake.calls[0]["kwargs"]["timeout"]

    def test_default_is_1800_seconds(self, provider, monkeypatch):
        assert chatgpt_module.DEFAULT_TIMEOUT_SECONDS == 1800
        assert self._timeout_of_call(monkeypatch, "chatgpt/luna", "max") == 1800

    def test_astra_at_max_effort_gets_an_hour(self, provider, monkeypatch):
        assert self._timeout_of_call(monkeypatch, "chatgpt/astra", "max") == 3600
        assert self._timeout_of_call(monkeypatch, "chatgpt/astra", "high") == 1800

    def test_model_entry_wins_over_the_env_setting(self, provider, monkeypatch):
        monkeypatch.setenv("CHATGPT_TIMEOUT_SECONDS", "42")

        assert self._timeout_of_call(monkeypatch, "chatgpt/astra", "max") == 3600
        assert self._timeout_of_call(monkeypatch, "chatgpt/astra", "high") == 42

    def test_effort_value_then_model_value_then_default(self, provider, monkeypatch, custom_catalogue):
        custom_catalogue(timeout_seconds=300, timeout_seconds_by_effort={"high": 900})
        monkeypatch.setenv("CHATGPT_TIMEOUT_SECONDS", "42")

        assert self._timeout_of_call(monkeypatch, "chatgpt/test", "high") == 900
        assert self._timeout_of_call(monkeypatch, "chatgpt/test", "low") == 300
        assert self._timeout_of_call(monkeypatch, "chatgpt/test") == 300

    def test_effort_value_uses_the_effort_that_is_sent(self, provider, monkeypatch, custom_catalogue):
        # The model stops at high, so thinking_mode "max" sends high and gets the high limit.
        custom_catalogue(timeout_seconds_by_effort={"high": 900})

        assert self._timeout_of_call(monkeypatch, "chatgpt/test", "max") == 900
        assert self._timeout_of_call(monkeypatch, "chatgpt/test", "medium") == 1800

    @pytest.mark.parametrize(
        "limits, message",
        [
            ({"timeout_seconds": 0}, "positive whole number"),
            ({"timeout_seconds": "600"}, "positive whole number"),
            ({"timeout_seconds": True}, "positive whole number"),
            ({"timeout_seconds_by_effort": {"high": -1}}, "positive whole number"),
            ({"timeout_seconds_by_effort": {"max": 900}}, "not one of"),
            ({"timeout_seconds_by_effort": [900]}, "must map effort levels"),
        ],
    )
    def test_bad_catalogue_values_are_rejected(self, tmp_path, limits, message):
        from providers.registries.chatgpt import ChatGPTModelRegistry

        path = tmp_path / "chatgpt_models.json"
        entry = {"model_name": "chatgpt/test-model", "effort_levels": ["low", "medium", "high"], **limits}
        path.write_text(json.dumps({"models": [entry]}))

        with pytest.raises(ValueError, match=message):
            ChatGPTModelRegistry(config_path=str(path))

    def test_timeout_error_names_the_setting_to_raise(self, provider, monkeypatch):
        _use(monkeypatch, subprocess.TimeoutExpired(cmd="codex", timeout=3600))

        with pytest.raises(ChatGPTCLIError) as raised:
            provider.generate_content("hi", "chatgpt/astra", thinking_mode="max")

        message = str(raised.value)
        assert "within 3600s" in message
        assert "timeout_seconds_by_effort.max of chatgpt/gpt-6-astra" in message
        assert "CHATGPT_MODELS_CONFIG_PATH" in message

    def test_summary_lists_the_default_and_each_override(self, monkeypatch):
        assert chatgpt_module.timeout_summary() == "default 1800s (built-in default); chatgpt/gpt-6-astra: max 3600s"

        monkeypatch.setenv("CHATGPT_TIMEOUT_SECONDS", "900")
        assert chatgpt_module.timeout_summary().startswith("default 900s (CHATGPT_TIMEOUT_SECONDS); ")

        monkeypatch.setenv("CHATGPT_TIMEOUT_SECONDS", "soon")
        assert chatgpt_module.timeout_summary().startswith("default 1800s (built-in default); ")

    def test_summary_shows_both_kinds_of_model_limit(self, custom_catalogue):
        custom_catalogue(timeout_seconds=300, timeout_seconds_by_effort={"high": 900})

        assert chatgpt_module.timeout_summary().endswith("chatgpt/test-model: 300s, high 900s")


class TestFailures:
    def test_missing_codex_cli(self, monkeypatch):
        monkeypatch.setattr(chatgpt_module.shutil, "which", lambda name: None)
        fake = _use(monkeypatch)

        with pytest.raises(ChatGPTCLIError, match="not found on PATH"):
            ChatGPTModelProvider().generate_content("hi", "chatgpt/luna")
        assert fake.calls == []

    def test_not_signed_in_is_not_retried(self, provider, monkeypatch):
        stdout = _events({"type": "error", "message": "unexpected status 401 Unauthorized"})
        fake = _use(monkeypatch, (stdout, b"", 1), (_reply(), b"", 0))

        with pytest.raises(ChatGPTCLIError, match="codex login"):
            provider.generate_content("hi", "chatgpt/luna")
        assert len(fake.calls) == 1

    def test_usage_limit_is_not_retried(self, provider, monkeypatch):
        stdout = _events({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}})
        fake = _use(monkeypatch, (stdout, b"", 1), (_reply(), b"", 0))

        with pytest.raises(ChatGPTCLIError, match="usage limit"):
            provider.generate_content("hi", "chatgpt/luna")
        assert len(fake.calls) == 1

    def test_transient_failure_is_retried_once(self, provider, monkeypatch):
        stdout = _events({"type": "error", "message": "stream disconnected before completion"})
        fake = _use(monkeypatch, (stdout, b"", 1), (_reply("recovered"), b"", 0))

        assert provider.generate_content("hi", "chatgpt/luna").content == "recovered"
        assert len(fake.calls) == 2

    def test_transient_failure_gives_up_after_two_attempts(self, provider, monkeypatch):
        stdout = _events({"type": "error", "message": "stream disconnected before completion"})
        fake = _use(monkeypatch, (stdout, b"", 1), (stdout, b"", 1), (_reply(), b"", 0))

        with pytest.raises(ChatGPTCLIError, match="stream disconnected"):
            provider.generate_content("hi", "chatgpt/luna")
        assert len(fake.calls) == 2

    def test_timeout_is_not_retried(self, provider, monkeypatch):
        fake = _use(monkeypatch, subprocess.TimeoutExpired(cmd="codex", timeout=600), (_reply(), b"", 0))

        with pytest.raises(ChatGPTCLIError, match="CHATGPT_TIMEOUT_SECONDS"):
            provider.generate_content("hi", "chatgpt/luna")
        assert len(fake.calls) == 1

    def test_error_events_are_never_returned_as_a_reply(self, provider, monkeypatch):
        stdout = _events({"type": "error", "message": "model is not supported"})
        _use(monkeypatch, (stdout, b"", 0))

        with pytest.raises(ChatGPTCLIError, match="model is not supported"):
            provider.generate_content("hi", "chatgpt/luna")

    def test_falls_back_to_stderr_when_there_are_no_events(self, provider, monkeypatch):
        _use(monkeypatch, (b"", b"line one\nError: unknown variant `bogus`\n", 2))

        with pytest.raises(ChatGPTCLIError, match="unknown variant"):
            provider.generate_content("hi", "chatgpt/luna")

    def test_cli_that_cannot_start(self, provider, monkeypatch):
        _use(monkeypatch, PermissionError("permission denied"))

        with pytest.raises(ChatGPTCLIError, match="Could not start"):
            provider.generate_content("hi", "chatgpt/luna")


# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------


def test_parse_codex_events_skips_noise():
    stdout = "Reading prompt from stdin...\nnot json\n{broken\n" + _reply("OK").decode()

    result = parse_codex_events(stdout)

    assert result["message"] == "OK"
    assert result["usage"]["output_tokens"] == 5
    assert result["errors"] == []


class TestCodexLoginProblem:
    def _status(self, monkeypatch, stdout=b"", stderr=b"", returncode=0):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

        monkeypatch.setattr(chatgpt_module.subprocess, "run", fake_run)
        return calls

    def test_signed_in_with_chatgpt(self, monkeypatch):
        calls = self._status(monkeypatch, stdout=b"Logged in using ChatGPT\n")
        assert codex_login_problem(_FAKE_CODEX) is None
        assert calls == [[_FAKE_CODEX, "login", "status"]]

    def test_signed_in_with_an_api_key(self, monkeypatch):
        self._status(monkeypatch, stdout=b"Logged in using an API key - sk-***\n")
        assert "not signed in with ChatGPT" in codex_login_problem(_FAKE_CODEX)

    def test_not_signed_in(self, monkeypatch):
        self._status(monkeypatch, stderr=b"Not logged in\n", returncode=1)
        assert "codex login" in codex_login_problem(_FAKE_CODEX)


def test_preferred_models(provider):
    allowed = list(provider.get_all_model_capabilities())

    assert provider.get_preferred_model(ToolModelCategory.FAST_RESPONSE, allowed).endswith("-luna")
    best = provider.get_preferred_model(ToolModelCategory.EXTENDED_REASONING, allowed)
    assert best in {"chatgpt/gpt-6.1-sol", "chatgpt/gpt-6-astra"}
    assert provider.get_preferred_model(ToolModelCategory.BALANCED, []) is None
