"""The dashboard's locale catalogs: every key the sources ask for exists in both,
the keys assembled at runtime exist too, and the Portuguese is Portuguese."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from memai import contract
from memai.store import memories

WEBUI = Path(__file__).resolve().parents[1] / "src" / "memai" / "webui"
LOCALES = ("en", "pt-BR")


def _catalog(code: str) -> dict:
    return json.loads((WEBUI / "public" / "i18n" / f"{code}.json").read_text(encoding="utf-8"))["strings"]


def _sources() -> list[Path]:
    skip = {"public", "dist"}
    return [p for p in WEBUI.rglob("*") if p.suffix in {".js", ".ts", ".vue"}
            and not skip & set(p.parts)]


def _asked_keys() -> set[str]:
    """Every literal key passed to t(), and every data-i18n key in the shell.
    A key ending in '.' is a prefix the caller completes at runtime."""
    keys = set()
    for path in _sources():
        keys |= set(re.findall(r"""\bt\(\s*['"]([\w.-]+)['"]""", path.read_text(encoding="utf-8")))
    keys |= set(re.findall(r'data-i18n(?:-\w+)?="([\w.-]+)"',
                           (WEBUI / "index.html").read_text(encoding="utf-8")))
    return {k for k in keys if not k.endswith(".")}


@pytest.mark.parametrize("code", LOCALES)
def test_every_key_the_dashboard_asks_for_exists(code):
    keys = _asked_keys()
    assert len(keys) > 500, "expected the sources to ask for most of the catalog"
    strings = _catalog(code)
    missing = sorted(k for k in keys if k not in strings)
    assert not missing, f"{code} lacks {missing}"


@pytest.mark.parametrize("code", LOCALES)
def test_every_memory_type_has_a_label(code):
    strings = _catalog(code)
    missing = [tp for tp in memories.MEMORY_TYPES if f"type.{tp}" not in strings]
    assert not missing, f"{code} lacks a label for {missing}"


@pytest.mark.parametrize("code", LOCALES)
def test_every_confidence_and_pin_has_a_label(code):
    strings = _catalog(code)
    keys = [f"conf.{c}" for c in contract.CONFIDENCES] + [f"mem.pin.{p}" for p in contract.PINS]
    missing = [k for k in keys if k not in strings]
    assert not missing, f"{code} lacks {missing}"


@pytest.mark.parametrize("code", LOCALES)
def test_the_task_keys_built_from_a_state_name_exist(code):
    states = list(contract.ITEM_STATES)
    families = {
        "task.state": states + list(contract.TASK_STATES),
        "task.mark": states,
        "task.toast": ["completed", "cancelled"],
        "task.closed": ["completed", "cancelled"],
    }
    strings = _catalog(code)
    missing = sorted(f"{prefix}.{name}" for prefix, names in families.items() for name in names
                     if f"{prefix}.{name}" not in strings)
    assert not missing, f"{code} lacks {missing}"


@pytest.mark.parametrize("code", LOCALES)
def test_the_runtime_keys_of_the_maintenance_tabs_exist(code):
    strings = _catalog(code)
    tabs = ["backups", "storage", "sections", "dupes", "log", "warden", "interface"]
    wanted = [f"mn.tab.{tab}" for tab in tabs] + [f"mn.ui.{m}" for m in ("system", "always", "never")]
    assert not [k for k in wanted if k not in strings]


def test_the_portuguese_task_strings_are_not_the_english_ones():
    en, pt = _catalog("en"), _catalog("pt-BR")

    def words(s: str) -> str:
        """a string made only of placeholders and punctuation reads the same in every language"""
        return re.sub(r"\{\w+\}|[:.%\s]", "", s)

    own = ("task.", "nm.task.", "ov.tasks.", "mn.ta.", "mn.msg.task")
    same = [k for k in en if k.startswith(own) and en[k] == pt[k] and words(en[k])]
    assert not same, f"untranslated: {same}"


def test_the_delete_body_names_the_item():
    for code in LOCALES:
        assert "{text}" in _catalog(code)["task.delete.body"]


def test_the_reminder_copy_names_the_domains_a_session_worked_in():
    for code in LOCALES:
        body = _catalog(code)["mn.ta.body"]
        assert "every open task" not in body.lower() and "todas as tarefas" not in body.lower()
    en = _catalog("en")["mn.ta.body"]
    assert "domains a session worked in" in en
    assert "how many there are and in which domains" in en
    assert "similar domains" in en
    assert "names them" not in en
    assert "domínios em que uma sessão trabalhou" in _catalog("pt-BR")["mn.ta.body"]
