"""The ceilings hold at runtime: an oversized tool result or hook field never leaves memai."""

from __future__ import annotations

import inspect
import json

from memai import budget, hook, server


def test_an_oversized_tool_result_becomes_an_error():
    def _huge_probe(n: int = 1) -> dict:
        """Probe."""
        return {"body": "x" * (budget.MCP_RESULT_MAX_CHARS + n)}

    try:
        probe = server.tool("_probe", server.READ)(_huge_probe)
        out = probe()
    finally:
        server._GROUP_OF.pop("_huge_probe", None)
    assert out["ok"] is False and "_huge_probe" in out["errors"][0]
    assert budget.result_chars(out) < 1000


def test_a_result_within_the_ceiling_passes_through_untouched():
    def _small_probe() -> dict:
        """Probe."""
        return {"body": "fine"}

    try:
        out = server.tool("_probe", server.READ)(_small_probe)()
    finally:
        server._GROUP_OF.pop("_small_probe", None)
    assert out == {"body": "fine"}


def test_wrapping_keeps_the_signature_the_host_publishes():
    assert list(inspect.signature(server.task_read).parameters) == ["uid", "part", "item", "offset"]
    assert server.task_read.__name__ == "task_read"


def test_emit_clips_additional_context_and_system_message(capsysbinary):
    hook._emit("SessionStart", "line\n" * 5000, system="sys\n" * 5000)
    out = json.loads(capsysbinary.readouterr().out)
    assert len(out["hookSpecificOutput"]["additionalContext"]) <= budget.HOOK_MAX_CHARS + 200
    assert len(out["systemMessage"]) <= budget.HOOK_MAX_CHARS + 200


def test_block_clips_the_reason(capsysbinary):
    hook._block("ask\n" * 5000)
    assert len(json.loads(capsysbinary.readouterr().out)["reason"]) <= budget.HOOK_MAX_CHARS + 200


def test_short_hook_output_is_untouched(capsysbinary):
    hook._block("one ask")
    assert json.loads(capsysbinary.readouterr().out)["reason"] == "one ask"
