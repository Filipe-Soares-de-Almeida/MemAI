"""What the bundled subagent definitions are held to.

A `tools:` list is an allowlist the host matches literally, so an MCP tool
whose name does not match leaves the agent running without it and with
nothing on screen to say so -- the report comes back empty for want of a
store rather than for want of a memory.

The name in the middle of an MCP tool is the server's, and that is the
user's to choose at registration. These tests hold a definition to the two
spellings the documentation and the guard's matcher both carry.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from memai import hook_install, server

AGENTS = hook_install.bundled_agents()

# `mcp__memai__search` -> the server name and the tool it publishes.
MCP_TOOL = re.compile(r"mcp__([A-Za-z]+)__([a-z_]+)")

# How the server's name is written where it is written at all: the command
# the install guide registers, and the package's own capitalisation.
SPELLINGS = ("memai", "MemAI")

pytestmark = pytest.mark.skipif(not AGENTS, reason="no agents bundled")


def _tools(agent: Path) -> list[str]:
    """The entries of an agent's `tools:` list, in frontmatter order."""
    text = agent.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{agent.name} opens with its frontmatter"
    block = text[4:text.index("\n---\n", 3)]
    start = block.index("[", block.index("tools:"))
    inner = block[start + 1:block.index("]", start)]
    return [entry.strip() for entry in inner.split(",") if entry.strip()]


@pytest.mark.parametrize("agent", AGENTS, ids=lambda p: p.name)
def test_every_memai_tool_an_agent_lists_is_published(agent):
    """A name the server does not publish is an allowlist entry that matches
    nothing, and the agent is short one tool it was written around."""
    named = {m.group(2) for entry in _tools(agent)
             if (m := MCP_TOOL.fullmatch(entry)) and m.group(1).lower() == "memai"}
    assert named <= set(server._TOOLS), named - set(server._TOOLS)


@pytest.mark.parametrize("agent", AGENTS, ids=lambda p: p.name)
def test_a_memai_tool_is_listed_under_every_spelling_of_the_server_name(agent):
    """The host builds the name from the server's registered one, which no
    definition can predict. Listing both keeps either registration working."""
    named = {m.group(2) for entry in _tools(agent)
             if (m := MCP_TOOL.fullmatch(entry)) and m.group(1).lower() == "memai"}
    listed = set(_tools(agent))
    missing = {f"mcp__{name}__{tool}" for tool in named for name in SPELLINGS} - listed
    assert not missing, missing
