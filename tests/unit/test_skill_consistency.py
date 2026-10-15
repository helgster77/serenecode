"""Tests that the agent skill names only tools, commands, and flags that exist.

The skill in skills/serenecode/SKILL.md is copied into agents and skill
marketplaces, where a renamed tool or flag would keep instructing agents
to call something that is gone.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from serenecode.cli import main
from serenecode.config import default_config, minimal_config, strict_config
from serenecode.mcp.server import build_server
from serenecode.mcp.tools import reset_state

_SKILL = Path(__file__).resolve().parents[2] / "skills" / "serenecode" / "SKILL.md"
_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_INVOCATION = re.compile(r"(?<![\w-])serenecode ([a-z]+)([^#\n]*)")
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def _skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def _code_snippets(text: str) -> list[str]:
    """Return fenced-block lines and inline code spans; prose is ignored."""
    fenced = [line for block in _FENCE.findall(text) for line in block.splitlines()]
    return fenced + _INLINE.findall(_FENCE.sub("", text))


def _invocations(text: str) -> list[tuple[str, list[str]]]:
    """Return (subcommand, flags) for every `serenecode <subcommand>` in code."""
    found = []
    for snippet in _code_snippets(text):
        for match in _INVOCATION.finditer(snippet):
            found.append((match.group(1), _FLAG.findall(match.group(2))))
    return found


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> None:
    reset_state()


def test_skill_has_name_and_description_frontmatter() -> None:
    text = _skill_text()
    frontmatter = text.split("---\n")[1]
    assert "name: serenecode\n" in frontmatter
    assert re.search(r"^description: \S", frontmatter, re.MULTILINE)


def test_every_mcp_tool_named_in_the_skill_is_registered() -> None:
    named = set(re.findall(r"\bserenecode_[a-z_]+\b", _skill_text()))
    registered = {tool.name for tool in asyncio.run(build_server().list_tools())}
    assert named, "the skill should name the MCP tools it recommends"
    assert named <= registered, f"unregistered tools: {sorted(named - registered)}"


def test_every_cli_command_and_flag_in_the_skill_exists() -> None:
    invocations = _invocations(_skill_text())
    assert {"doctor", "init", "spec", "check", "mcp"} <= {cmd for cmd, _ in invocations}
    for command, flags in invocations:
        assert command in main.commands, f"unknown command: serenecode {command}"
        options = {opt for param in main.commands[command].params for opt in param.opts}
        missing = set(flags) - options
        assert not missing, f"serenecode {command} has no {sorted(missing)}"


def test_preset_depths_in_the_skill_match_the_configs() -> None:
    text = _skill_text()
    for preset, config in (
        ("minimal", minimal_config()),
        ("default", default_config()),
        ("strict", strict_config()),
    ):
        assert f"{preset} L{config.recommended_level}" in text


def test_invocation_parser_reports_flags_per_command() -> None:
    sample = "Run `serenecode check src/ --level 4 --structural` then `serenecode doctor`."
    assert _invocations(sample) == [("check", ["--level", "--structural"]), ("doctor", [])]
