"""The dashboard's dependency direction: a view imports core, never another view."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parents[1] / "src" / "memai" / "webui"
VIEWS = WEBUI / "views"

SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|^\s*import\s+)(['"])(\.{1,2}/[^'"]+)\1""", re.M)


def _view(path: Path) -> str:
    """The view a file belongs to: its folder under views/, or the file itself."""
    return path.relative_to(VIEWS).parts[0]


def crossings(path: Path, source: str) -> list[str]:
    """The relative imports in `source` that reach a view other than the one `path` is part of."""
    found = []
    for match in SPECIFIER.finditer(source):
        target = (path.parent / match.group(2)).resolve()
        if target.is_relative_to(VIEWS.resolve()) and _view(target) != _view(path.resolve()):
            found.append(match.group(2))
    return found


def test_no_view_imports_another_view():
    found = {}
    for path in VIEWS.rglob("*"):
        if path.suffix in {".js", ".ts", ".vue"}:
            hits = crossings(path.resolve(), path.read_text(encoding="utf-8"))
            if hits:
                found[path.relative_to(VIEWS).as_posix()] = hits
    assert not found, f"move what these share into core/: {found}"


@pytest.mark.parametrize(("file", "source"), [
    ("memories.js", "import { openRecord } from './record/index.js';"),
    ("record/task.js", "import { go } from '../graph.js';"),
    ("diagram.js", "const m = await import('./diagrams.js');"),
])
def test_a_crossing_is_caught(file, source):
    assert crossings((VIEWS / file).resolve(), source)


@pytest.mark.parametrize(("file", "source"), [
    ("record/index.js", "import { mountTask } from './task.js';"),
    ("memories.js", "import { go } from '../core/router.ts';"),
    ("record/index.js", "import { t } from '../../i18n.ts';"),
])
def test_core_and_a_views_own_folder_are_allowed(file, source):
    assert not crossings((VIEWS / file).resolve(), source)
