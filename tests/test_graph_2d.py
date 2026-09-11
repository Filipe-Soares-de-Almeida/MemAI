"""The geometry the relations graph arranges itself with.

The arrangements are JavaScript, so these run them: tools/graph-cases.mjs
feeds graph-geom.js, graph-force.js, graph-field.js and graph-arrange.js a
synthetic store under node and prints what came back. The same arrangement as
tests/test_richtext.py -- the browser's code is the code under test.

Skipped where node is absent; the graph still ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tools" / "graph-cases.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(scope="module")
def drawn() -> dict:
    out = subprocess.run(["node", str(CASES)], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ---------------------------------------------------------------- the modes

def test_three_arrangements_are_offered(drawn):
    assert drawn["modes"] == ["hubs", "pack", "atlas"]


def test_every_arrangement_names_the_string_that_explains_it(drawn):
    catalog = json.loads(
        (ROOT / "src" / "memai" / "webui" / "public" / "i18n" / "en.json")
        .read_text(encoding="utf-8"))["strings"]
    for key in drawn["notes"]:
        assert key in catalog, key
    for mode in drawn["modes"]:
        assert f"g.mode.{mode}" in catalog


# ------------------------------------------------------------ circle packing

def test_packed_siblings_never_overlap(drawn):
    assert drawn["pack"]["overlaps"] == 0


def test_packed_siblings_stay_inside_the_radius_that_is_returned(drawn):
    """Every arrangement sizes a parent from it, so a circle outside it is a
    child drawn outside its own domain."""
    assert drawn["pack"]["outside"] == 0
    assert drawn["pack"]["radius"] > 0


def test_the_enclosing_circle_contains_every_circle(drawn):
    assert drawn["enclose"]["misses"] == 0


# -------------------------------------------------------------------- force

def test_the_repulsion_is_capped_by_the_softening_length(drawn):
    """Softening caps the force at charge/soft^2 at every distance, zero
    included; a floored inverse square hands back a hundred times that and
    throws the two bodies out of the field."""
    assert drawn["force"]["peak"] <= drawn["force"]["cap"] + 1e-9


def test_a_run_stops_at_the_pass_ceiling(drawn):
    """With no time limit reached, the pass count is what ends a slice."""
    assert drawn["force"]["passes"] == 5


def test_a_halted_simulation_reads_as_settled(drawn):
    assert drawn["force"]["halted"] is True


# -------------------------------------------------------------------- field

def test_a_cluster_traces_one_closed_coast_around_itself(drawn):
    assert drawn["field"]["loops"] >= 1
    assert drawn["field"]["closed"] is True
    assert drawn["field"]["holdsCentre"] is True


# --------------------------------------------------------------------- hubs

def test_a_domain_holding_one_memory_and_nothing_else_is_collapsed(drawn):
    assert "acme/x100/p900" not in drawn["hubs"]["paths"]


def test_a_domain_that_only_passes_through_is_collapsed(drawn):
    """`zeta/x100` holds no memories and one child."""
    assert "zeta/x100" not in drawn["hubs"]["paths"]
    assert "zeta/x100/p200" in drawn["hubs"]["paths"]


def test_a_root_is_a_node_however_little_it_holds(drawn):
    assert "omni" in drawn["hubs"]["paths"]


def test_every_memory_hangs_off_a_domain_it_belongs_to(drawn):
    """A collapsed domain lifts its memories to the nearest ancestor that
    survived, so no memory is left without one and none lands off its own
    branch."""
    assert drawn["hubs"]["stranded"] == 0
    assert drawn["hubs"]["wrongAnchor"] == 0


def test_the_arrangement_settles_to_finite_positions(drawn):
    assert drawn["hubs"]["finite"] is True
    box = drawn["hubs"]["box"]
    assert box["x1"] > box["x0"] and box["y1"] > box["y0"]


# --------------------------------------------------------------------- pack

def test_nested_domains_hold_their_children(drawn):
    assert drawn["nest"]["escapes"] == 0


def test_nested_siblings_never_overlap(drawn):
    assert drawn["nest"]["overlaps"] == 0


def test_every_memory_is_a_leaf_of_the_nesting(drawn):
    assert drawn["nest"]["leaves"] == drawn["tree"]["nodes"]


# -------------------------------------------------------------------- atlas

def test_the_seats_are_the_same_two_builds_running(drawn):
    """A territory that moves between two openings of the same store cannot
    be learned, which is the whole of this arrangement."""
    assert drawn["atlas"]["deterministic"] is True
    assert drawn["atlas"]["seats"] == len(drawn["tree"]["roots"])


def test_a_memory_settles_nearest_its_own_root(drawn):
    assert drawn["atlas"]["athome"] >= 0.8
    assert drawn["atlas"]["finite"] is True


def test_every_root_gets_a_coast(drawn):
    assert drawn["atlas"]["coasts"] == len(drawn["tree"]["roots"])


# ------------------------------------------------------------- domain hits

def test_a_hub_answers_the_pointer_as_a_domain(drawn):
    """The engine tells a place from a record by the `domain` a hit carries."""
    assert drawn["domainHit"]["hub"] == "acme/x100"
    assert drawn["domainHit"]["hubIsMemory"] is False


def test_the_ground_inside_a_packed_domain_is_that_domain(drawn):
    assert drawn["domainHit"]["pack"] == "acme/x100"
    assert drawn["domainHit"]["packIsMemory"] is False


def test_a_territory_answers_the_pointer_standing_in_it(drawn):
    assert drawn["domainHit"]["atlas"] in ("acme", "zeta", "omni")


def test_the_settle_ceiling_still_leaves_every_coast_traced(drawn):
    """The ceiling stops the physics, which could spin; the tracing is finite
    work, and a territory with no coast is dots on nothing."""
    assert drawn["domainHit"]["haltedQueue"] == 0
    assert drawn["domainHit"]["haltedCoasts"] == len(drawn["tree"]["roots"])


# --------------------------------------------------------- what is drawn

def test_the_two_kinds_of_name_are_two_toggles(drawn):
    """Each toggle drops only its own layer: the domain names and the memory
    titles are asked for separately."""
    both = drawn["show"]["both"]
    assert both["domains"] > 0 and both["memories"] > 0
    assert drawn["show"]["namesOff"] == {"domains": both["domains"], "memories": 0}
    assert drawn["show"]["domainsOff"] == {"domains": 0, "memories": both["memories"]}
    assert drawn["show"]["neither"] == {"domains": 0, "memories": 0}


def test_a_hovered_memory_lights_its_own_relations(drawn):
    """Each is drawn as a gradient, faint at the end it leaves."""
    assert drawn["highlight"]["overMemory"] > 0
    assert drawn["highlight"]["atlasOverMemory"] > 0


def test_a_hovered_domain_lights_what_is_filed_in_it_and_no_relation(drawn):
    """Hovering a domain draws no relation, in any arrangement."""
    assert drawn["highlight"]["overDomain"] == 0
    assert drawn["highlight"]["atlasOverDomain"] == 0


# ------------------------------------------------------------------ travel

@pytest.mark.parametrize("mode", ["hubs", "nest"])
def test_an_arrangement_can_be_asked_where_a_memory_is(drawn, mode):
    """The camera travels to a selection, and only the arrangement knows."""
    assert drawn[mode]["located"] is True
