"""memai.shell: a path spelled so that sh and cmd.exe each read it back as one word."""

from __future__ import annotations

import subprocess
import sys

import pytest

from memai import shell


@pytest.mark.parametrize("word", [
    "/opt/env/bin/memai-hook",
    "C:/path/to/MemAI/.venv/Scripts/memai-hook.exe",
    "/opt/café/x-1.2/memai-hook",
])
def test_a_plain_path_stays_bare(word):
    assert shell.quote(word) == word


@pytest.mark.parametrize("word", [
    "C:/path/to/Mem AI/.venv/Scripts/memai-hook.exe",
    "C:/Program Files (x86)/env/memai-hook.exe",
    "/opt/R&D/memai-hook",
    "/opt/a,b;c=d/memai-hook",
    "/opt/tab\there/memai-hook",
    "C:\\path\\to\\MemAI\\session.jsonl",
    "",
])
def test_a_path_a_shell_would_split_or_rewrite_is_double_quoted(word):
    assert shell.quote(word) == f'"{word}"'


def test_a_quoted_path_with_a_space_runs_in_the_platform_shell(tmp_path):
    """sh on POSIX, cmd.exe on Windows: the shells a hook entry and a pasted command meet."""
    folder = tmp_path / "Mem AI"
    folder.mkdir()
    out = tmp_path / "argv.txt"
    if sys.platform == "win32":
        exe = folder / "memai-hook.cmd"
        exe.write_text(f'@echo %*> "{out}"\r\n', encoding="ascii")
    else:
        exe = folder / "memai-hook"
        exe.write_text(f'#!/bin/sh\necho "$@" > "{out}"\n', encoding="utf-8")
        exe.chmod(0o755)
    subprocess.run(f"{shell.quote(exe.as_posix())} session-start", shell=True, check=True, timeout=30)
    assert out.read_text(encoding="utf-8").strip() == "session-start"
