"""The dashboard's task surface, read off the source: the type's colour tokens,
and every string the checklist view asks the catalogs for."""

from __future__ import annotations

import json
import re
from pathlib import Path

WEBUI = Path(__file__).resolve().parents[1] / "src" / "memai" / "webui"


def _catalog(code: str) -> dict:
    path = WEBUI / "public" / "i18n" / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8"))["strings"]


def _type_order() -> list[str]:
    src = (WEBUI / "core" / "shared.js").read_text(encoding="utf-8")
    body = re.search(r"TYPE_ORDER = \[([^\]]*)\]", src).group(1)
    return re.findall(r"'(\w+)'", body)


def test_every_type_has_its_colour_tokens_and_class():
    css = (WEBUI / "admin.css").read_text(encoding="utf-8")
    order = _type_order()
    assert "task" in order
    for tp in order:
        assert re.search(rf"--t-{tp}:\s*#[0-9a-f]{{6}}", css), f"--t-{tp} is not declared"
        assert re.search(rf"--t-{tp}-ink:\s*#[0-9a-f]{{6}}", css), f"--t-{tp}-ink is not declared"
        assert re.search(rf"\.t-{tp}\s*\{{", css), f".t-{tp} is not declared"


def test_every_type_has_a_label_in_both_catalogs():
    for code in ("en", "pt-BR"):
        strings = _catalog(code)
        missing = [tp for tp in _type_order() if f"type.{tp}" not in strings]
        assert not missing, f"{code} lacks a label for {missing}"


def test_the_task_view_only_asks_for_strings_that_exist():
    """Literal keys and the families built from a state name, both catalogs."""
    src = "\n".join((WEBUI / "views" / name).read_text(encoding="utf-8")
                    for name in ("task.js", "memories.js"))
    literal = set(re.findall(r"""\bt\(\s*'((?:task|mem\.task)\.[\w.]+)'""", src))
    literal |= {"common.undo", "common.save", "common.cancel", "common.all"}
    states = ["todo", "doing", "done", "dropped"]
    families = {
        "task.state": states + ["open", "completed", "cancelled"],
        "task.mark": states,
        "task.toast": ["completed", "cancelled"],
        "task.closed": ["completed", "cancelled"],
    }
    wanted = literal | {f"{prefix}.{name}" for prefix, names in families.items() for name in names}
    assert len(wanted) > 30
    for code in ("en", "pt-BR"):
        strings = _catalog(code)
        missing = sorted(k for k in wanted if k not in strings)
        assert not missing, f"{code} lacks {missing}"


def test_the_portuguese_task_strings_are_not_the_english_ones():
    en, pt = _catalog("en"), _catalog("pt-BR")
    # a string made only of placeholders reads the same in every language

    def words(s: str) -> str:
        return re.sub(r"\{\w+\}|[:.\s]", "", s)

    same = [k for k in en if k.startswith("task.") and en[k] == pt[k] and words(en[k])]
    assert not same, f"untranslated: {same}"
