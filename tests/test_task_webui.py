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

    own = ("task.", "nm.task.", "ov.tasks.", "mn.ta.", "mn.msg.task")
    same = [k for k in en if k.startswith(own) and en[k] == pt[k] and words(en[k])]
    assert not same, f"untranslated: {same}"


def test_the_create_health_and_reminder_views_only_ask_for_strings_that_exist():
    """The new-task fields, the open-tasks figure and the reminder panel."""
    src = "\n".join((WEBUI / "views" / name).read_text(encoding="utf-8")
                    for name in ("new-memory.js", "overview.js", "maintenance.js"))
    wanted = set(re.findall(
        r"""\bt\(\s*'((?:nm\.task|ov\.tasks|mn\.ta)\.[\w.]+|mn\.msg\.task\w+)'""", src))
    assert {"nm.task.goal", "nm.task.items", "ov.tasks.open", "mn.ta.title",
            "mn.msg.taskOn"} <= wanted
    for code in ("en", "pt-BR"):
        strings = _catalog(code)
        missing = sorted(k for k in wanted if k not in strings)
        assert not missing, f"{code} lacks {missing}"


def test_the_new_memory_form_offers_task_and_posts_it_to_its_own_endpoint():
    src = (WEBUI / "views" / "new-memory.js").read_text(encoding="utf-8")
    assert "NOT_WRITTEN_HERE = ['handoff']" in src
    assert "'/api/tasks'" in src


def test_an_item_names_its_panel_only_while_the_panel_is_drawn():
    src = (WEBUI / "views" / "task.js").read_text(encoding="utf-8")
    uses = re.findall(r"[^\n]*aria-controls[^\n]*", src)
    assert uses and all("open ?" in line for line in uses)


def _view(name: str) -> str:
    return (WEBUI / "views" / name).read_text(encoding="utf-8")


def test_an_item_menu_deletes_through_a_confirm_and_names_a_neighbour_for_focus():
    src = _view("task.js")
    assert "method: 'DELETE', errKey: 'task.err.delete'" in src
    assert "write('item', { item: key }" in src
    assert "confirmModal" in src and "danger: true" in src
    # the entry sits below a separator, after the state entries
    assert re.search(r"\{ sep: true \},\s*\{ label: t\('task\.item\.delete'\), danger: true", src)
    # next item's mark, else the previous one's, else the add control
    assert re.search(r"current\.items\[at \+ 1\], current\.items\[at - 1\]", src)
    assert "'#tkAddOpen'" in src


def test_the_delete_strings_exist_in_both_catalogs():
    for code in ("en", "pt-BR"):
        strings = _catalog(code)
        for key in ("task.item.delete", "task.delete.title", "task.delete.body",
                    "task.err.delete", "task.toast.deleted"):
            assert strings.get(key), f"{code} lacks {key}"
        assert "{text}" in strings["task.delete.body"]


def test_the_doing_mark_turns_and_reduced_motion_stops_it():
    css = (WEBUI / "admin.css").read_text(encoding="utf-8")
    assert re.search(r'\.tk-state\[data-s="doing"\] \.ico \{ animation: spin [\d.]+s linear infinite; \}', css)
    reduced = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    block = next(b for b in reduced if ".tk-state" in b)
    assert '.tk-state[data-s="doing"] .ico { animation: none; }' in block
    assert "--pop-from: 1" in block and "--leave-x: 0px" in block


def test_the_checklist_animates_only_compositor_properties_and_never_layout():
    css = (WEBUI / "admin.css").read_text(encoding="utf-8")
    start = css.index("/* A task's checklist")
    end = css.index("/* a task in the memories list")
    tk = css[start:end]
    for keyframes in re.findall(r"@keyframes tk-[\w-]+ \{[^}]*\}", tk):
        assert not re.search(r"\b(width|height|top|left|margin|padding)\s*:", keyframes), keyframes
    # the dropped hatch is clipped, not resized
    assert re.search(r"\.tk-bar-drop \{[^}]*clip-path: inset", tk, re.S)
    assert "transition: clip-path" in tk


def test_the_item_row_column_comes_from_a_token_not_a_literal():
    css = (WEBUI / "admin.css").read_text(encoding="utf-8")
    row = re.search(r"\.tk-row \{([^}]*)\}", css).group(1)
    assert "28px" not in row
    assert "var(--ctl-h-sm)" in row.split("grid-template-columns:")[1].split(";")[0]


def test_a_comment_names_its_writer_with_a_mark_and_a_word():
    src = _view("task.js")
    assert "icon(person ? 'person' : 'agent')" in src
    assert "task.author.person" in src and "task.author.agent" in src
    css = (WEBUI / "admin.css").read_text(encoding="utf-8")
    # a person's mark is round and an agent's is square: shape, not colour alone
    assert re.search(r"\.tk-c\.is-person \.tk-c-av \{[^}]*border-radius: 50%", css)
    assert re.search(r"\.tk-c\.is-agent \.tk-c-av \{[^}]*border-radius: var\(--r-sm\)", css)
    # no tinted bubble per comment
    assert not re.search(r"\.tk-c \{[^}]*background", css)


def test_a_write_made_in_place_refreshes_the_record_side_panel():
    record = _view("record.js")
    assert "onWrite: () => refreshTrail(view, m, uid)" in record
    assert 'data-rs="updated"' in record and 'data-rs="history"' in record
    assert "onWrite?.(res)" in _view("task.js")


def test_the_health_open_tasks_figure_is_formatted():
    src = _view("overview.js")
    assert "t('ov.tasks.open', { n: open })" in src and "fmtInt(n)" in src


def test_the_reminder_copy_names_the_domains_a_session_worked_in():
    for code in ("en", "pt-BR"):
        body = _catalog(code)["mn.ta.body"]
        assert "every open task" not in body.lower() and "todas as tarefas" not in body.lower()
    assert "domains a session worked in" in _catalog("en")["mn.ta.body"]
    assert "domínios em que uma sessão trabalhou" in _catalog("pt-BR")["mn.ta.body"]
    assert "similar domains" in _catalog("en")["mn.ta.body"]


def test_a_menu_dropped_from_a_button_takes_the_keyboard():
    src = (WEBUI / "core" / "ui.js").read_text(encoding="utf-8")
    assert "if (at.btn) entries[0]?.focus();" in src
    assert "ArrowDown: i + 1, ArrowUp: i - 1" in src
    assert "at.btn?.focus()" in src
