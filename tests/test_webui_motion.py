"""The dashboard's Animations setting: one attribute on the root decides every
reduced-motion rule, and the stored choice survives a broken store.

core/motion.js runs under node against stand-ins for localStorage, matchMedia
and the document; admin.css and the other sources are read as text.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "src" / "memai" / "webui"

# A fresh copy of the module per scenario: it reads the store and the media
# query once, at import.
RUNNER = """
import { pathToFileURL } from 'node:url';
const url = pathToFileURL(process.argv[1]).href;
let n = 0;
const load = async ({ stored, osReduces, broken = false }) => {
  const listeners = [];
  const mq = { matches: osReduces, addEventListener: (_, fn) => listeners.push(fn) };
  const store = new Map(stored === undefined ? [] : [['memai.motion', stored]]);
  globalThis.localStorage = broken
    ? { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } }
    : { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  globalThis.matchMedia = () => mq;
  globalThis.document = { documentElement: { dataset: {} } };
  const m = await import(url + '?n=' + (n++));
  const attr = () => document.documentElement.dataset.motion;
  return { m, attr, store, flip: v => { mq.matches = v; listeners.forEach(f => f()); } };
};
const out = {};
let c = await load({ osReduces: true });
out.systemOsReduce = c.attr();
c = await load({ osReduces: false });
out.systemOsFull = c.attr();
c.flip(true); out.systemFollowsChange = c.attr();
c = await load({ stored: 'always', osReduces: true });
out.alwaysOverridesOs = c.attr();
c.flip(true); out.alwaysIgnoresChange = c.attr();
c = await load({ stored: 'never', osReduces: false });
out.neverOverridesOs = c.attr();
c = await load({ stored: 'sometimes', osReduces: true });
out.unknownIsSystem = [c.m.getMotion(), c.attr()];
c = await load({ osReduces: true });
c.m.setMotion('always');
out.setApplies = [c.attr(), c.store.get('memai.motion'), c.m.motionOn()];
c.m.setMotion('bogus');
out.badIgnored = [c.m.getMotion(), c.store.get('memai.motion')];
c = await load({ stored: 'always', osReduces: true, broken: true });
out.brokenStoreIsSystem = [c.m.getMotion(), c.attr()];
c.m.setMotion('never');
out.brokenStoreStillApplies = [c.attr(), c.m.getMotion()];
c.flip(false);
out.brokenStoreKeepsSession = c.attr();
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def ran() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    out = subprocess.run(
        ["node", "--input-type=module", "-e", RUNNER, str(WEBUI / "core" / "motion.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_system_follows_the_os_and_its_change_event(ran):
    assert ran["systemOsReduce"] == "reduce"
    assert ran["systemOsFull"] == "full"
    assert ran["systemFollowsChange"] == "reduce"


def test_always_and_never_decide_for_themselves(ran):
    assert ran["alwaysOverridesOs"] == "full"
    assert ran["alwaysIgnoresChange"] == "full"
    assert ran["neverOverridesOs"] == "reduce"


def test_an_unknown_stored_value_reads_as_system(ran):
    assert ran["unknownIsSystem"] == ["system", "reduce"]


def test_a_choice_is_applied_and_stored_and_a_bad_one_is_ignored(ran):
    assert ran["setApplies"] == ["full", "always", True]
    assert ran["badIgnored"] == ["always", "always"]


def test_a_blocked_store_reads_as_system_and_the_choice_lasts_the_session(ran):
    assert ran["brokenStoreIsSystem"] == ["system", "reduce"]
    assert ran["brokenStoreStillApplies"] == ["reduce", "never"]
    assert ran["brokenStoreKeepsSession"] == "reduce"


# ------------------------------------------------------------- the sources

def _css() -> str:
    return (WEBUI / "admin.css").read_text(encoding="utf-8")


def test_no_stylesheet_rule_reads_the_media_query_directly():
    assert "prefers-reduced-motion" not in _css()
    assert ':root[data-motion="reduce"]' in _css()


def test_only_the_motion_module_reads_the_system_preference():
    readers = [p.relative_to(WEBUI).as_posix() for p in WEBUI.rglob("*.js")
               if not {"dist", "node_modules", "public"} & set(p.relative_to(WEBUI).parts)
               and "prefers-reduced-motion" in p.read_text(encoding="utf-8")]
    assert readers == ["core/motion.js"]


def test_the_graph_asks_the_root_not_the_media_query():
    src = (WEBUI / "graph-2d.js").read_text(encoding="utf-8")
    assert "matchMedia" not in src
    assert "get motion() { return motionOn(); }" in src


def test_the_reduced_spinner_outranks_the_spinner_declared_after_it():
    css = _css()
    assert ':root[data-motion="reduce"] .spin { animation: breathe' in css
    assert re.search(r"^\.spin \{[^}]*animation: spin", css, re.M | re.S)


def test_the_motion_module_loads_before_anything_is_drawn():
    src = (WEBUI / "app.js").read_text(encoding="utf-8")
    first = re.search(r"^import .*$", src, re.M).group(0)
    assert first == "import './core/motion.js';"


def test_the_setting_lives_in_maintenance_with_both_languages():
    src = (WEBUI / "views" / "maintenance.js").read_text(encoding="utf-8")
    assert "'interface'" in src and "setMotion" in src
    wanted = {"mn.tab.interface", "mn.ui.title", "mn.ui.aside", "mn.ui.motion", "mn.ui.body",
              "mn.ui.system", "mn.ui.always", "mn.ui.never"}
    for code in ("en", "pt-BR"):
        strings = json.loads((WEBUI / "public" / "i18n" / f"{code}.json")
                             .read_text(encoding="utf-8"))["strings"]
        assert wanted <= set(strings), f"{code} lacks {sorted(wanted - set(strings))}"


# ----------------------------------------------------------- the item panel

def _srgb(v: float) -> float:
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _lum(rgb) -> float:
    r, g, b = (_srgb(c / 255) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex(css: str, name: str) -> tuple[int, int, int]:
    h = re.search(rf"--{name}:\s*#([0-9a-f]{{6}})", css).group(1)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _alpha(css: str, name: str) -> float:
    return float(re.search(rf"--{name}:\s*rgba\(255, 255, 255, ([\d.]+)\)", css).group(1))


def _ratio(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_the_item_panel_sits_between_the_page_and_its_row_and_its_text_reads_on_it():
    css = _css()
    panel = re.search(r"^\.tk-panel \{([^}]*)\}", css, re.M).group(1)
    assert "background: var(--inset)" in panel
    assert "margin" not in panel and "border-radius" not in panel
    page, well, rows = _hex(css, "bg"), _hex(css, "inset"), _hex(css, "surface")
    assert _lum(page) < _lum(well) < _lum(rows)
    for tier in ("ink", "ink-2", "ink-3"):
        a = _alpha(css, tier)
        text = tuple(a * 255 + (1 - a) * c for c in well)
        assert _ratio(_lum(text), _lum(well)) >= 4.5, tier


def test_the_wash_sits_on_the_row_header_and_never_on_the_panel():
    css = _css()
    assert not re.search(r"^\.tk-item[^{>]*\{[^}]*background", css, re.M)
    assert re.search(r'^\.tk-item\[data-s="doing"\] > \.tk-row \{ background: color-mix\(in srgb, var\(--f-live\) 7%', css, re.M)
    assert re.search(r"^\.tk-item\.is-open > \.tk-row \{ background: var\(--hover-2\)", css, re.M)


def test_the_panes_that_scroll_keep_room_for_their_scrollbar():
    css = _css()
    rule = re.search(r"([^{}]*)\{ scrollbar-gutter: stable; \}", css).group(1)
    selectors = [s.strip() for s in rule.split("*/")[-1].split(",")]
    assert selectors == [
        ".view", ".rec-main", ".rec-index", ".rs-body", ".rf-body", ".mem-list #memList",
        ".mi-body", ".dom-col-body", ".dom-detail-body", ".opt-rail", ".opt-rows",
        ".mnt-log", ".mnt-shelf-body", ".mnt-panel .panel > .panel-body"]


def test_a_view_that_clips_instead_of_scrolling_reserves_no_strip():
    css = _css()
    for sel in (".view.wide", ".view.fill"):
        body = re.search(rf"^  {re.escape(sel)} \{{([^}}]*)\}}", css, re.M).group(1)
        assert "overflow: hidden" in body and "scrollbar-gutter: auto" in body, sel


def test_the_graph_view_reserves_no_strip_because_its_canvas_never_scrolls_the_page():
    css = _css()
    assert ".view:has(> .anim > .graph-wrap) { scrollbar-gutter: auto; }" in css
    src = (WEBUI / "views" / "graph.js").read_text(encoding="utf-8")
    assert re.search(r'<div class="anim">.*?<div class="graph-wrap" id="gWrap">', src, re.S)


# ------------------------------------------------- the shell before the module

INDEX = (WEBUI / "index.html").read_text(encoding="utf-8")

# the inline script is the code under test: run it, then run motion.js, in the
# same stand-in environment
SHELL_RUNNER = """
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
const [, motionPath, inlinePath] = process.argv;
const inline = readFileSync(inlinePath, 'utf8');
const out = [];
let n = 0;
for (const stored of [undefined, 'system', 'always', 'never', 'bogus']) {
  for (const osReduces of [true, false]) {
    for (const broken of [false, true]) {
      const env = () => {
        const store = new Map(stored === undefined ? [] : [['memai.motion', stored]]);
        globalThis.localStorage = broken
          ? { getItem() { throw new Error('blocked'); } }
          : { getItem: k => store.get(k) ?? null };
        globalThis.matchMedia = () => ({ matches: osReduces, addEventListener() {} });
        globalThis.document = { documentElement: { dataset: {} } };
      };
      env();
      new Function(inline)();
      const shell = document.documentElement.dataset.motion;
      env();
      await import(pathToFileURL(motionPath).href + '?n=' + (n++));
      out.push({ stored: stored ?? null, osReduces, broken, shell, module: document.documentElement.dataset.motion });
    }
  }
}
console.log(JSON.stringify(out));
"""


def _inline_motion_script() -> str:
    head = INDEX[:INDEX.index("</head>")]
    scripts = re.findall(r"<script>(.*?)</script>", head, re.S)
    found = [s for s in scripts if "memai.motion" in s]
    assert len(found) == 1, "the shell resolves data-motion in one inline <script> in <head>"
    return found[0]


def test_the_shell_resolves_data_motion_in_the_head_before_any_stylesheet():
    head = INDEX[:INDEX.index("</head>")]
    at = head.index("memai.motion")
    assert head.rfind("<script>", 0, at) != -1
    assert "<link rel=\"stylesheet\"" not in head[:at]
    assert "type=\"module\"" not in head


def test_the_shell_script_and_the_module_agree_on_every_input(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    inline = tmp_path / "inline.js"
    inline.write_text(_inline_motion_script(), encoding="utf-8")
    out = subprocess.run(
        ["node", "--input-type=module", "-e", SHELL_RUNNER,
         str(WEBUI / "core" / "motion.js"), str(inline)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    cases = json.loads(out.stdout)
    assert len(cases) == 20
    for c in cases:
        if c["broken"]:
            assert c["shell"] == c["module"] == ("reduce" if c["osReduces"] else "full"), c
        else:
            assert c["shell"] == c["module"], c


def test_the_shell_script_names_the_modules_storage_key():
    key = re.search(r"const STORAGE_KEY = '([^']+)'", (WEBUI / "core" / "motion.js").read_text(encoding="utf-8")).group(1)
    assert f"localStorage.getItem('{key}')" in _inline_motion_script()
