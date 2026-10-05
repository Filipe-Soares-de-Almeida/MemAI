"""Flags comments that narrate history or run longer than two lines.

Only lines added against a base revision are checked, so a file's older
comments are reported once an edit touches them. Standard library only.

    python tools/comment-lint.py                    # changes vs origin/dev
    python tools/comment-lint.py --base HEAD a.py   # uncommitted lines in a.py
    python tools/comment-lint.py --all a.py         # every line of a.py
    python tools/comment-lint.py --hook             # a Claude Code PostToolUse hook

Exit 1 with one `file:line: message` per finding; in --hook mode the findings
go to stderr with exit 2, which the host hands back to the agent.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

MAX_LINES = 2

ROOT = Path(__file__).resolve().parents[1]

HASH = {".py", ".yml", ".yaml", ".toml", ".sh"}
SLASH = {".js", ".mjs", ".cjs", ".ts"}
CHECKED = HASH | SLASH | {".css", ".html", ".vue", ".bat", ".md"}

SKIP_PARTS = {"node_modules", ".venv", "dist", "public", "fixtures", ".git"}
SKIP_NAMES = {"CHANGELOG.md", "package-lock.json"}
SKIP_PREFIXES = ("docs/superpowers/",)

HISTORY = [
    (re.compile(r"(?<!\bis )(?<!\bare )(?<!\bbe )(?<!\bbeen )(?<!\bget )(?<!\bgets )\bused to\b", re.I),
     "history: 'used to'"),
    (re.compile(r"\bno longer\b", re.I), "history: 'no longer'"),
    (re.compile(r"\bany ?more\b", re.I), "history: 'anymore'"),
    (re.compile(r"\b(previously|formerly)\b", re.I), "history: 'previously'"),
    (re.compile(r"\b(until|before) (now|this change|the fix)\b", re.I), "history: 'until now'"),
    (re.compile(r"\b(changed|was changed|switched|moved) (because|so that|from)\b", re.I),
     "history: what changed"),
    (re.compile(r"\bthis (broke|was broken|regressed)\b", re.I), "history: what broke"),
    (re.compile(r"\b(on|in|since|until|as of) (19|20)\d\d-\d\d(-\d\d)?\b", re.I), "history: a dated note"),
    (re.compile(r"\b(issue|ticket|PR|pull request) ?#\d+|\(#\d+\)", re.I), "history: a ticket number"),
]


@dataclass(frozen=True)
class Block:
    """One comment: its first and last line, and its text."""
    start: int
    end: int
    text: str
    docstring: bool = False

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


# ------------------------------------------------------------------ parsing

def _python_blocks(source: str, path: Path) -> list[Block]:
    blocks: list[Block] = []
    run: list[tuple[int, str]] = []

    def flush() -> None:
        if run:
            blocks.append(Block(run[0][0], run[-1][0], "\n".join(t for _, t in run)))
            run.clear()

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return blocks
    lines = source.splitlines()
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        row = tok.start[0]
        alone = lines[row - 1].strip().startswith("#")
        if alone and run and run[-1][0] == row - 1:
            run.append((row, tok.string))
            continue
        flush()
        if alone:
            run.append((row, tok.string))
        else:
            blocks.append(Block(row, row, tok.string))
    flush()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return blocks
    api_docs = path.name == "server.py"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            continue
        if api_docs and _is_tool(node):
            continue
        doc = body[0]
        blocks.append(Block(doc.lineno, doc.end_lineno, doc.value.value, docstring=True))
    return blocks


def _is_tool(node: ast.AST) -> bool:
    """A function registered as an MCP tool: its docstring is API documentation."""
    for deco in getattr(node, "decorator_list", []):
        target = deco.func if isinstance(deco, ast.Call) else deco
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "tool":
            return True
    return False


def _hash_blocks(source: str) -> list[Block]:
    """`#` comments in YAML, TOML and shell: a run of comment-only lines is one block."""
    return _line_runs(source, re.compile(r"^\s*#(.*)$"), inline=re.compile(r"\s#\s(.*)$"))


def _bat_blocks(source: str) -> list[Block]:
    return _line_runs(source, re.compile(r"^\s*(?:@?rem\b|::)(.*)$", re.I))


def _line_runs(source: str, alone: re.Pattern, inline: re.Pattern | None = None) -> list[Block]:
    blocks: list[Block] = []
    run: list[tuple[int, str]] = []
    for row, line in enumerate(source.splitlines(), 1):
        m = alone.match(line)
        if m:
            if run and run[-1][0] != row - 1:
                blocks.append(Block(run[0][0], run[-1][0], "\n".join(t for _, t in run)))
                run = []
            run.append((row, m.group(1)))
            continue
        if run:
            blocks.append(Block(run[0][0], run[-1][0], "\n".join(t for _, t in run)))
            run = []
        if inline and (m := inline.search(line)) and "'" not in line[:m.start()] and '"' not in line[:m.start()]:
            blocks.append(Block(row, row, m.group(1)))
    if run:
        blocks.append(Block(run[0][0], run[-1][0], "\n".join(t for _, t in run)))
    return blocks


def _slash_blocks(source: str, line_comments: bool = True) -> list[Block]:
    """`/* */` and `//` comments, outside strings and template literals.
    Regex literals are not recognised; a `//` inside one reads as a comment."""
    blocks: list[Block] = []
    singles: list[tuple[int, str]] = []
    i, n, row = 0, len(source), 1
    stack: list[str] = []          # open quotes and template `${` levels

    def close_singles() -> None:
        if singles:
            blocks.append(Block(singles[0][0], singles[-1][0], "\n".join(t for _, t in singles)))
            singles.clear()

    while i < n:
        c = source[i]
        top = stack[-1] if stack else ""
        if c == "\n":
            row += 1
            i += 1
            continue
        if top in ("'", '"'):
            if c == "\\":
                i += 2
                continue
            if c == top:
                stack.pop()
            i += 1
            continue
        if top == "`":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
            elif source.startswith("${", i):
                stack.append("{")
                i += 2
                continue
            i += 1
            continue
        if line_comments and source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            alone = source[source.rfind("\n", 0, i) + 1:i].strip() == ""
            text = source[i + 2:end]
            if alone and singles and singles[-1][0] == row - 1:
                singles.append((row, text))
            else:
                close_singles()
                if alone:
                    singles.append((row, text))
                else:
                    blocks.append(Block(row, row, text))
            i = end
            continue
        if source.startswith("/*", i):
            close_singles()
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            text = source[i + 2:end - 2]
            blocks.append(Block(row, row + text.count("\n"), text))
            row += source.count("\n", i, end)
            i = end
            continue
        if c.strip():
            line_start = source.rfind("\n", 0, i) + 1
            if singles and singles[-1][0] != row and source[line_start:i].strip() == "":
                close_singles()
        if c in ("'", '"', "`"):
            stack.append(c)
        elif c == "{" and top == "{":
            stack.append("{")
        elif c == "}" and top == "{":
            stack.pop()
        i += 1
    close_singles()
    return blocks


def _html_blocks(source: str) -> list[Block]:
    blocks = []
    for m in re.finditer(r"<!--(.*?)-->", source, re.S):
        start = source.count("\n", 0, m.start()) + 1
        blocks.append(Block(start, start + m.group(0).count("\n"), m.group(1)))
    return blocks


def _vue_blocks(source: str) -> list[Block]:
    blocks = _html_blocks(source)
    for m in re.finditer(r"(<script\b[^>]*>)(.*?)</script>", source, re.S):
        offset = source.count("\n", 0, m.start(2))
        blocks += [Block(b.start + offset, b.end + offset, b.text) for b in _slash_blocks(m.group(2))]
    return blocks


def blocks_of(path: Path, source: str) -> list[Block]:
    suffix = path.suffix
    if suffix == ".py":
        return _python_blocks(source, path)
    if suffix in HASH:
        return _hash_blocks(source)
    if suffix in SLASH:
        return _slash_blocks(source)
    if suffix == ".css":
        return _slash_blocks(source, line_comments=False)
    if suffix == ".html":
        return _html_blocks(source) + [
            b for m in re.finditer(r"(<(?:script|style)\b[^>]*>)(.*?)</(?:script|style)>", source, re.S)
            for b in _shift(_slash_blocks(m.group(2)), source.count("\n", 0, m.start(2)))]
    if suffix == ".vue":
        return _vue_blocks(source)
    if suffix == ".bat":
        return _bat_blocks(source)
    return []


def _shift(blocks: list[Block], offset: int) -> list[Block]:
    return [Block(b.start + offset, b.end + offset, b.text, b.docstring) for b in blocks]


# ------------------------------------------------------------------ findings

def lint_text(path: Path, source: str, added: set[int] | None = None) -> list[tuple[int, str]]:
    """Findings for `source`; with `added`, only comments touching those lines.
    Markdown is prose: every added line is checked for history, none for length."""
    findings: list[tuple[int, str]] = []
    if path.suffix == ".md":
        for row, line in enumerate(source.splitlines(), 1):
            if added is None or row in added:
                findings += [(row, msg) for pattern, msg in HISTORY if pattern.search(line)]
        return findings
    for block in blocks_of(path, source):
        rows = range(block.start, block.end + 1)
        touched = [r for r in rows if added is None or r in added]
        if not touched:
            continue
        if not block.docstring and block.lines > MAX_LINES:
            findings.append((block.start, f"comment is {block.lines} lines; keep it to {MAX_LINES}"))
        for offset, line in enumerate(block.text.splitlines() or [block.text]):
            row = block.start + offset
            if added is not None and row not in added and block.lines > 1:
                continue
            findings += [(row, msg) for pattern, msg in HISTORY if pattern.search(line)]
    return sorted(set(findings))


def checked(path: Path) -> bool:
    rel = path.resolve().relative_to(ROOT).as_posix() if path.resolve().is_relative_to(ROOT) else path.as_posix()
    return (path.suffix in CHECKED and path.name not in SKIP_NAMES
            and not SKIP_PARTS & set(Path(rel).parts) and not rel.startswith(SKIP_PREFIXES))


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def added_lines(base: str, files: list[str] | None = None) -> dict[str, set[int]]:
    """Lines each file adds against the merge base of `base` and HEAD, working tree
    included. A file git does not track is all added."""
    merge_base = _git("merge-base", base, "HEAD").strip() if base != "HEAD" else "HEAD"
    diff = _git("diff", "-U0", "--no-color", "--no-ext-diff", merge_base, "--", *(files or []))
    out: dict[str, set[int]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line[4:] == "/dev/null" else line[6:]
            if current:
                out.setdefault(current, set())
        elif line.startswith("@@") and current:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, count = int(m.group(1)), int(m.group(2) or "1")
            out[current].update(range(start, start + count))
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", *(files or [])).splitlines()
    for name in untracked:
        text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
        out[name] = set(range(1, text.count("\n") + 2))
    return out


def run(paths: dict[str, set[int] | None]) -> list[str]:
    report = []
    for name, added in sorted(paths.items()):
        path = ROOT / name if not Path(name).is_absolute() else Path(name)
        if not path.is_file() or not checked(path):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        report += [f"{name}:{row}: {msg}" for row, msg in lint_text(path, source, added)]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*")
    parser.add_argument("--base", default="origin/dev")
    parser.add_argument("--all", action="store_true", help="check every line, not only added ones")
    parser.add_argument("--hook", action="store_true", help="read a PostToolUse payload from stdin")
    args = parser.parse_args(argv)

    if args.hook:
        try:
            target = (json.load(sys.stdin).get("tool_input") or {}).get("file_path", "")
        except (ValueError, AttributeError):
            return 0
        if not target or not Path(target).resolve().is_relative_to(ROOT):
            return 0
        rel = Path(target).resolve().relative_to(ROOT).as_posix()
        report = run({rel: added_lines("HEAD", [rel]).get(rel, set())})
        if report:
            print("Comment lint (tools/comment-lint.py) on the lines just written:", file=sys.stderr)
            print("\n".join(report), file=sys.stderr)
            return 2
        return 0

    if args.all:
        targets: dict[str, set[int] | None] = {f: None for f in args.files}
    else:
        targets = dict(added_lines(args.base, args.files or None))
    report = run(targets)
    if report:
        print("\n".join(report))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
