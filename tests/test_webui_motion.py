"""The dashboard's Animations setting: one attribute on the root decides every
reduced-motion rule, and the stored choice survives a broken store.

core/motion.ts and the shell's inline script run under node against stand-ins
for localStorage, matchMedia and the document. Two checks scan the sources: no
other module reads the media query, and the entry point imports motion first.
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
        ["node", "--input-type=module", "-e", RUNNER, str(WEBUI / "core" / "motion.ts")],
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

def _sources() -> list[Path]:
    skip = {"dist", "node_modules", "public"}
    return [p for p in WEBUI.rglob("*") if p.suffix in {".js", ".ts", ".vue"}
            and not skip & set(p.relative_to(WEBUI).parts)]


def test_only_the_motion_module_reads_the_system_preference():
    readers = [p.relative_to(WEBUI).with_suffix("").as_posix() for p in _sources()
               if "prefers-reduced-motion" in p.read_text(encoding="utf-8")]
    assert readers == ["core/motion"]


def test_the_motion_module_loads_before_anything_is_drawn():
    entry = next(p for p in _sources() if p.relative_to(WEBUI).with_suffix("").as_posix() == "app")
    first = re.search(r"^import .*$", entry.read_text(encoding="utf-8"), re.M).group(0)
    assert re.fullmatch(r"""import ['"]\./core/motion(\.[jt]s)?['"];?""", first), first


# ------------------------------------------------- the shell before the module

INDEX = (WEBUI / "index.html").read_text(encoding="utf-8")

# the inline script is the code under test: run it, then run motion.ts, in the
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
         str(WEBUI / "core" / "motion.ts"), str(inline)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    cases = json.loads(out.stdout)
    assert len(cases) == 20
    for c in cases:
        if c["broken"]:
            assert c["shell"] == c["module"] == ("reduce" if c["osReduces"] else "full"), c
        else:
            assert c["shell"] == c["module"], c


