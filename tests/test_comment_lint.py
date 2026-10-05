"""tools/comment-lint.py: what it flags in each comment syntax, and that it reads
only the lines a change adds."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "comment-lint.py"

_spec = importlib.util.spec_from_file_location("comment_lint", SCRIPT)
lint = importlib.util.module_from_spec(_spec)
sys.modules["comment_lint"] = lint
_spec.loader.exec_module(lint)


def found(name: str, source: str, added: set[int] | None = None) -> list[tuple[int, str]]:
    return lint.lint_text(Path(name), source, added)


def messages(name: str, source: str, added: set[int] | None = None) -> list[str]:
    return [msg for _, msg in found(name, source, added)]


# ------------------------------------------------------------------ history

@pytest.mark.parametrize("phrase", [
    "the lantern used to blink twice",
    "the queue no longer drains on exit",
    "the pump does not stall anymore",
    "previously the valve opened first",
    "this broke when the gauge was rescaled",
    "switched from polling because the bus was slow",
    "fixed as of 2026-01-02",
    "see PR #41",
    "follow-up (#12)",
])
def test_a_history_phrase_is_flagged_in_every_syntax(phrase):
    for name, source in [
        ("a.py", f"x = 1  # {phrase}\n"),
        ("a.js", f"const x = 1; // {phrase}\n"),
        ("a.css", f"/* {phrase} */\n.a {{ color: red; }}\n"),
        ("a.html", f"<!-- {phrase} -->\n<p>hi</p>\n"),
        ("a.yml", f"key: 1  # {phrase}\n"),
        ("a.bat", f"rem {phrase}\n"),
        ("a.md", f"The pump {phrase}.\n"),
    ]:
        assert any(m.startswith("history") for m in messages(name, source)), (name, phrase)


@pytest.mark.parametrize("text", [
    "the key is used to sign the request",
    "these flags are used to skip the cache",
    "a moved file keeps its history",
    "colour #123456 comes from the theme",
])
def test_purpose_and_ordinary_words_are_not_history(text):
    assert messages("a.py", f"x = 1  # {text}\n") == []


# ------------------------------------------------------------------- length

def test_a_comment_of_two_lines_passes_and_three_does_not():
    two = "# reads the gauge\n# and keeps the last value\nx = 1\n"
    three = "# reads the gauge\n# and keeps the last value\n# for the next poll\nx = 1\n"
    assert messages("a.py", two) == []
    assert found("a.py", three) == [(1, "comment is 3 lines; keep it to 2")]


def test_comment_runs_split_on_code_and_blank_lines():
    source = "# one\n# two\nx = 1\n# three\n# four\n\n# five\n"
    assert messages("a.py", source) == []
    assert messages("a.js", source.replace("#", "//").replace("x = 1", "let x = 1;")) == []


def test_a_block_comment_counts_its_lines():
    source = "/* the arc\n   turns once\n   per lap */\nconst SPIN = 1;\n"
    assert found("a.js", source) == [(1, "comment is 3 lines; keep it to 2")]
    assert found("a.css", source.replace("const SPIN = 1;", ".a {}")) == [(1, "comment is 3 lines; keep it to 2")]


def test_a_docstring_may_be_long_but_not_historical():
    long_doc = 'def f():\n    """Reads the gauge.\n\n    Keeps the last value\n    for the next poll.\n    """\n'
    assert messages("a.py", long_doc) == []
    old_doc = 'def f():\n    """Reads the gauge; it no longer polls."""\n'
    assert messages("a.py", old_doc) == ["history: 'no longer'"]


def test_an_mcp_tool_docstring_is_api_documentation():
    source = ('@tool("core")\ndef note(text: str):\n    """Saves a note. It no longer takes a kind."""\n'
              'def helper():\n    """It no longer takes a kind."""\n')
    assert [row for row, _ in found("server.py", source)] == [5]


def test_slashes_inside_strings_and_templates_are_not_comments():
    source = ("const a = 'http://lantern.example/a';\n"
              "const b = `${'//'} and ${a}`;\n"
              'const c = "/* not a comment */";\n')
    assert messages("a.js", source) == []


def test_html_and_vue_comments_and_the_vue_script():
    vue = ("<template>\n  <!-- one\n       two\n       three -->\n  <p>x</p>\n</template>\n"
           "<script setup>\n// the pump no longer stalls\nconst x = 1;\n</script>\n")
    assert found("a.vue", vue) == [(2, "comment is 3 lines; keep it to 2"),
                                    (8, "history: 'no longer'")]


# ------------------------------------------------------------------ the diff

def test_only_comments_touching_added_lines_are_reported():
    source = "# one\n# two\n# three\nx = 1\n# the pump no longer stalls\ny = 2\n"
    assert found("a.py", source, added={4}) == []
    assert found("a.py", source, added={2}) == [(1, "comment is 3 lines; keep it to 2")]
    assert found("a.py", source, added={5}) == [(5, "history: 'no longer'")]


def test_vendored_and_generated_paths_are_skipped():
    assert not lint.checked(ROOT / "src" / "memai" / "webui" / "public" / "x.js")
    assert not lint.checked(ROOT / "src" / "memai" / "webui" / "dist" / "x.js")
    assert not lint.checked(ROOT / "CHANGELOG.md")
    assert lint.checked(ROOT / "src" / "memai" / "db.py")


def test_the_hook_reports_on_stderr_with_exit_2(tmp_path):
    target = ROOT / "tools" / "_lint_probe_unused.py"
    target.write_text("x = 1  # the pump no longer stalls\n", encoding="utf-8")
    try:
        payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
        out = subprocess.run([sys.executable, str(SCRIPT), "--hook"], input=payload, cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert out.returncode == 2, out
        assert "_lint_probe_unused.py:1: history: 'no longer'" in out.stderr
    finally:
        target.unlink()


def test_the_hook_ignores_a_file_outside_the_checkout(tmp_path):
    outside = tmp_path / "x.py"
    outside.write_text("x = 1  # the pump no longer stalls\n", encoding="utf-8")
    payload = json.dumps({"tool_input": {"file_path": str(outside)}})
    out = subprocess.run([sys.executable, str(SCRIPT), "--hook"], input=payload, cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
