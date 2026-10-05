"""The backup shelf: naming, pinning, zipping, restoring and deleting.

A backup is a whole copy of the store, so a shelf of them is the largest
thing in a MemAI home. Archiving moves the ones nobody is going to restore
into compressed files grouped by week, by month, under a chosen name or into
a zip that exists; unarchiving puts them back untouched.

Every name reaching these functions comes from an HTTP payload, so the
refusals matter as much as the round trip.
"""

from __future__ import annotations

import os
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memai import admin, db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMAI_HOME", str(tmp_path))
    with TestClient(admin.app) as c:
        yield c


def _shelve(name: str, body: bytes = b"a backup") -> Path:
    """Put a file on the active project's shelf without taking a real copy."""
    path = db.backups_dir(db.active_project()) / name
    path.write_bytes(body)
    return path


# ---------------------------------------------------------------- the round trip

def test_archiving_moves_the_files_into_one_zip(client):
    a = _shelve("General-20260901-120000.db", b"first")
    b = _shelve("General-20260902-120000.db", b"second")

    dest = db.archive_backups("General", [a.name, b.name])

    assert dest.name == db.archive_name("General")
    assert not a.exists() and not b.exists(), "the shelf keeps no copy"
    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == sorted([a.name, b.name])
        assert zf.read(a.name) == b"first"


def test_a_second_run_in_the_same_month_joins_the_same_archive(client):
    first = _shelve("General-20260901-120000.db")
    second = _shelve("General-20260902-120000.db")

    one = db.archive_backups("General", [first.name])
    two = db.archive_backups("General", [second.name])

    assert one == two
    assert len(db.archive_files("General")) == 1
    assert len(db.archive_members(one)) == 2


def test_a_different_month_gets_its_own_archive(client):
    _shelve("General-20260901-120000.db")
    _shelve("General-20260801-120000.db")

    db.archive_backups("General", ["General-20260901-120000.db"], when=date(2026, 9, 4))
    db.archive_backups("General", ["General-20260801-120000.db"], when=date(2026, 8, 4))

    assert sorted(p.name for p in db.archive_files("General")) == [
        "General-2026-08.zip", "General-2026-09.zip"]


def test_unarchiving_puts_every_file_back_and_drops_the_zip(client):
    a = _shelve("General-20260901-120000.db", b"first")
    b = _shelve("General-20260902-120000.db", b"second")
    dest = db.archive_backups("General", [a.name, b.name])

    restored = db.unarchive("General", dest.name)

    assert sorted(restored) == sorted([a.name, b.name])
    assert a.read_bytes() == b"first" and b.read_bytes() == b"second"
    assert not dest.exists()
    assert db.archive_files("General") == []


def test_a_restored_backup_keeps_the_day_it_was_taken(client):
    """extract() stamps the file with now, which would sort every restored
    backup to the top of a shelf ordered by when it was taken."""
    a = _shelve("General-20260901-120000.db")
    taken = datetime(2026, 9, 1, 12, 0, 0).timestamp()
    os.utime(a, (taken, taken))
    dest = db.archive_backups("General", [a.name])

    db.unarchive("General", dest.name)

    # a zip rounds its timestamps to two seconds
    assert abs(a.stat().st_mtime - taken) <= 2


def test_an_archive_is_not_a_backup(client):
    """backup_files globs one level, so the shelf never lists what was zipped."""
    a = _shelve("General-20260901-120000.db")
    db.archive_backups("General", [a.name])

    assert db.backup_files("General") == []


def test_deleting_an_archive_reports_what_went_with_it(client):
    a = _shelve("General-20260901-120000.db")
    b = _shelve("General-20260902-120000.db")
    dest = db.archive_backups("General", [a.name, b.name])

    assert db.delete_archive("General", dest.name) == 2
    assert not dest.exists()


# ------------------------------------------------------------- choosing the zip

def _taken(path: Path, when: datetime) -> None:
    """Stamp a shelved file with the moment its backup was taken (UTC)."""
    stamp = when.replace(tzinfo=UTC).timestamp()
    os.utime(path, (stamp, stamp))


def test_grouping_by_month_follows_when_each_backup_was_taken(client):
    """Archiving in October must not pull a September backup into October."""
    a = _shelve("General-20260915-120000.db")
    b = _shelve("General-20261003-120000.db")
    _taken(a, datetime(2026, 9, 15, 12))
    _taken(b, datetime(2026, 10, 3, 12))

    made = db.archive_grouped("General", [a.name, b.name], "month")

    assert sorted(p.name for p in made) == ["General-2026-09.zip", "General-2026-10.zip"]
    assert made[next(p for p in made if p.name.endswith("09.zip"))] == [a.name]


def test_grouping_by_week_splits_on_the_iso_week(client):
    mon = _shelve("General-20260928-090000.db")
    sun = _shelve("General-20261004-210000.db")
    nxt = _shelve("General-20261005-090000.db")
    _taken(mon, datetime(2026, 9, 28, 9))
    _taken(sun, datetime(2026, 10, 4, 21))
    _taken(nxt, datetime(2026, 10, 5, 9))
    week = lambda d: "General-{}-W{:02d}.zip".format(*d.isocalendar()[:2])

    made = db.archive_grouped("General", [mon.name, sun.name, nxt.name], "week")

    assert {p.name: v for p, v in made.items()} == {
        week(date(2026, 9, 28)): [mon.name, sun.name],
        week(date(2026, 10, 5)): [nxt.name]}


def test_a_week_belongs_to_the_iso_year_it_falls_in(client):
    a = _shelve("General-20251229-120000.db")
    _taken(a, datetime(2025, 12, 29, 12))

    (dest,) = db.archive_grouped("General", [a.name], "week")

    assert dest.name == "General-2026-W01.zip"


def test_a_group_that_already_has_a_zip_is_appended_to(client):
    a = _shelve("General-20260901-120000.db")
    b = _shelve("General-20260902-120000.db")
    _taken(a, datetime(2026, 9, 1, 12))
    _taken(b, datetime(2026, 9, 2, 12))
    db.archive_grouped("General", [a.name], "month")

    db.archive_grouped("General", [b.name], "month")

    (only,) = db.archive_files("General")
    assert len(db.archive_members(only)) == 2


def test_an_unknown_grouping_is_refused(client):
    a = _shelve("General-20260901-120000.db")
    with pytest.raises(ValueError, match="group"):
        db.archive_grouped("General", [a.name], "decade")
    assert a.exists()


def test_a_zip_can_be_named_by_the_one_archiving(client):
    a = _shelve("General-20260901-120000.db", b"first")

    dest = db.archive_backups("General", [a.name], label="before the move")

    assert dest.name == "General-before the move.zip"
    assert db.archive_members(dest)[0]["name"] == a.name
    assert not a.exists()


def test_naming_a_zip_that_exists_is_refused_and_leaves_the_shelf_alone(client):
    a = _shelve("General-20260901-120000.db")
    b = _shelve("General-20260902-120000.db")
    db.archive_backups("General", [a.name], label="keep")

    with pytest.raises(ValueError, match="already exists"):
        db.archive_backups("General", [b.name], label="keep")
    assert b.exists()


def test_a_backup_can_be_added_to_an_existing_zip(client):
    a = _shelve("General-20260901-120000.db")
    b = _shelve("General-20260902-120000.db")
    first = db.archive_backups("General", [a.name], label="keep")

    dest = db.archive_backups("General", [b.name], into=first.name)

    assert dest == first
    assert sorted(m["name"] for m in db.archive_members(dest)) == sorted([a.name, b.name])
    assert not b.exists()


def test_adding_to_a_zip_that_is_not_there_is_refused(client):
    a = _shelve("General-20260901-120000.db")
    with pytest.raises(ValueError, match="not an archive"):
        db.archive_backups("General", [a.name], into="General-nope.zip")
    assert a.exists()


@pytest.mark.parametrize("into", ["../../memai.db", "../escape.zip", "General-2026-09.db"])
def test_adding_to_a_zip_outside_the_archive_folder_is_refused(client, into):
    a = _shelve("General-20260901-120000.db")
    with pytest.raises(ValueError):
        db.archive_backups("General", [a.name], into=into)
    assert a.exists()


def test_a_zip_can_be_renamed_and_keeps_its_files(client):
    a = _shelve("General-20260901-120000.db", b"first")
    old = db.archive_backups("General", [a.name], label="draft")

    new = db.rename_archive("General", old.name, "final name")

    assert new.name == "General-final name.zip"
    assert not old.exists()
    assert [m["name"] for m in db.archive_members(new)] == [a.name]


def test_renaming_onto_another_zip_is_refused(client):
    a = _shelve("General-20260901-120000.db")
    b = _shelve("General-20260902-120000.db")
    one = db.archive_backups("General", [a.name], label="one")
    db.archive_backups("General", [b.name], label="two")

    with pytest.raises(ValueError, match="already exists"):
        db.rename_archive("General", one.name, "two")
    assert one.exists()


def test_renaming_a_zip_to_its_own_name_changes_nothing(client):
    a = _shelve("General-20260901-120000.db")
    zipped = db.archive_backups("General", [a.name], label="same")
    assert db.rename_archive("General", zipped.name, "same") == zipped
    assert zipped.exists()


@pytest.mark.parametrize("label", [
    "", "   ", "a/b", "a\\b", "..", "a..b/", ".hidden", "ends.", "x" * 61,
    "tab\there", "colon:name", "star*",
])
def test_a_zip_name_that_is_not_a_plain_name_is_refused(client, label):
    a = _shelve("General-20260901-120000.db")
    with pytest.raises(ValueError, match="name"):
        db.archive_backups("General", [a.name], label=label)
    assert a.exists()


def test_accented_names_are_plain_names(client):
    a = _shelve("General-20260901-120000.db")
    dest = db.archive_backups("General", [a.name], label="antes da migração")
    assert dest.name == "General-antes da migração.zip"


def test_renaming_something_that_is_not_an_archive_is_refused(client):
    with pytest.raises(ValueError):
        db.rename_archive("General", "../../memai.db", "x")
    with pytest.raises(ValueError, match="not an archive"):
        db.rename_archive("General", "General-missing.zip", "x")


# -------------------------------------------------------------------- refusals

@pytest.mark.parametrize("name", [
    "../memai.db",
    "archive/../../memai.db",
    "../../secret.db",
])
def test_a_name_that_climbs_out_of_the_shelf_is_refused(client, name):
    with pytest.raises(ValueError):
        db.archive_backups("General", [name])


def test_a_name_that_is_not_on_the_shelf_is_refused(client):
    with pytest.raises(ValueError, match="not a backup"):
        db.archive_backups("General", ["General-does-not-exist.db"])


def test_archiving_nothing_is_refused(client):
    with pytest.raises(ValueError, match="no backups"):
        db.archive_backups("General", [])


def test_a_file_that_is_not_a_db_is_refused(client):
    (db.backups_dir("General") / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a backup"):
        db.archive_backups("General", ["notes.txt"])


def test_archiving_the_same_name_twice_is_refused(client):
    """The archive would hold two members with one name, and unzip one."""
    a = _shelve("General-20260901-120000.db")
    db.archive_backups("General", [a.name])
    _shelve(a.name)

    with pytest.raises(ValueError, match="already archived"):
        db.archive_backups("General", [a.name])


def test_unarchiving_onto_a_name_the_shelf_already_holds_is_refused(client):
    """Nothing is written: the check runs over every member first."""
    a = _shelve("General-20260901-120000.db", b"archived")
    b = _shelve("General-20260902-120000.db")
    dest = db.archive_backups("General", [a.name, b.name])
    _shelve(a.name, b"a newer file with the same name")

    with pytest.raises(ValueError, match="already on the shelf"):
        db.unarchive("General", dest.name)
    assert dest.exists(), "the archive is intact"
    assert not b.exists(), "and nothing was extracted"


def test_an_archive_holding_a_path_is_not_extracted(client):
    """A zip written elsewhere can name `../x`; extracting it would write
    outside the shelf."""
    archive = db.archives_dir("General") / "General-2026-09.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.db", b"x")

    with pytest.raises(ValueError, match="a path, not a name"):
        db.unarchive("General", archive.name)
    assert not (db.backups_dir("General").parent / "escaped.db").exists()


@pytest.mark.parametrize("name", ["../../memai.db", "General-2026-09.db", "nope.zip"])
def test_only_a_zip_in_the_archive_folder_can_be_unarchived(client, name):
    with pytest.raises(ValueError):
        db.unarchive("General", name)


# -------------------------------------------------------------------- the API

def test_the_shelf_endpoint_lists_both_halves(client):
    a = _shelve("General-20260901-120000.db", b"first")
    _shelve("General-20260902-120000.db", b"second")
    client.post("/api/maintenance/archive", json={"names": [a.name]})

    body = client.get("/api/maintenance/backups").json()

    assert [f["name"] for f in body["shelf"]] == ["General-20260902-120000.db"]
    assert len(body["archives"]) == 1
    arc = body["archives"][0]
    assert arc["count"] == 1 and arc["raw"] == len(b"first")
    assert [m["name"] for m in arc["members"]] == [a.name]


def test_the_shelf_endpoint_is_not_truncated(client):
    """health caps its list for the summary strip; a file this one does not
    show cannot be selected, so it shows all of them."""
    for i in range(15):
        _shelve(f"General-2026090{i // 10}{i % 10}-120000.db")

    assert len(client.get("/api/maintenance/backups").json()["shelf"]) == 15
    assert len(client.get("/api/maintenance/health").json()["backups"]) == 12


def test_the_archive_endpoint_reports_what_it_saved(client):
    _shelve("General-20260901-120000.db", b"a" * 4096)
    res = client.post("/api/maintenance/archive",
                      json={"names": ["General-20260901-120000.db"]})

    body = res.json()
    assert body["ok"] and body["added"] == 1
    assert body["raw"] == 4096
    assert body["size"] < body["raw"], "a zip of one repeated byte compresses"


def test_the_endpoints_round_trip(client):
    _shelve("General-20260901-120000.db", b"first")
    name = client.post("/api/maintenance/archive",
                       json={"names": ["General-20260901-120000.db"]}).json()["archive"]

    res = client.post("/api/maintenance/unarchive", json={"name": name})

    assert res.json()["restored"] == ["General-20260901-120000.db"]
    assert client.get("/api/maintenance/backups").json()["archives"] == []


def test_the_delete_endpoint_takes_the_archive_and_its_contents(client):
    _shelve("General-20260901-120000.db")
    name = client.post("/api/maintenance/archive",
                       json={"names": ["General-20260901-120000.db"]}).json()["archive"]

    assert client.post("/api/maintenance/archive-delete",
                       json={"name": name}).json()["count"] == 1
    body = client.get("/api/maintenance/backups").json()
    assert body["archives"] == [] and body["shelf"] == []


def test_archiving_with_no_names_is_an_error(client):
    res = client.post("/api/maintenance/archive", json={"names": []})
    assert res.status_code >= 400


def test_the_archive_endpoint_groups_by_week(client):
    a = _shelve("General-20260928-090000.db", b"a")
    b = _shelve("General-20261005-090000.db", b"b")
    _taken(a, datetime(2026, 9, 28, 9))
    _taken(b, datetime(2026, 10, 5, 9))

    body = client.post("/api/maintenance/archive",
                       json={"names": [a.name, b.name], "group": "week"}).json()

    assert body["ok"] and body["added"] == 2
    assert [x["name"] for x in body["archives"]] == [
        "General-2026-W40.zip", "General-2026-W41.zip"]
    assert all(x["added"] == 1 and x["size"] > 0 for x in body["archives"])
    assert body["archive"] == "General-2026-W40.zip"


def test_a_dry_run_reports_the_plan_and_moves_nothing(client):
    a = _shelve("General-20260928-090000.db")
    b = _shelve("General-20261005-090000.db")
    _taken(a, datetime(2026, 9, 28, 9))
    _taken(b, datetime(2026, 10, 5, 9))
    client.post("/api/maintenance/archive", json={"names": [a.name], "group": "week"})
    c = _shelve("General-20260929-090000.db")
    _taken(c, datetime(2026, 9, 29, 9))

    body = client.post("/api/maintenance/archive",
                       json={"names": [b.name, c.name], "group": "week", "dry_run": True}).json()

    assert body["plan"] == [
        {"name": "General-2026-W41.zip", "added": 1, "exists": False},
        {"name": "General-2026-W40.zip", "added": 1, "exists": True}]
    assert b.exists() and c.exists()
    assert [x.name for x in db.archive_files("General")] == ["General-2026-W40.zip"]


def test_a_dry_run_still_refuses_what_a_real_run_would(client):
    res = client.post("/api/maintenance/archive",
                      json={"names": ["General-gone.db"], "group": "week", "dry_run": True})
    assert res.status_code >= 400


def test_the_archive_endpoint_names_a_zip(client):
    a = _shelve("General-20260901-120000.db")
    body = client.post("/api/maintenance/archive",
                       json={"names": [a.name], "group": "name",
                             "label": "before the move"}).json()
    assert body["archive"] == "General-before the move.zip"


def test_the_archive_endpoint_adds_to_an_existing_zip(client):
    a = _shelve("General-20260901-120000.db")
    b = _shelve("General-20260902-120000.db")
    first = client.post("/api/maintenance/archive",
                        json={"names": [a.name], "group": "name", "label": "keep"}).json()

    body = client.post("/api/maintenance/archive",
                       json={"names": [b.name], "group": "existing",
                             "into": first["archive"]}).json()

    assert body["archive"] == first["archive"]
    arcs = client.get("/api/maintenance/backups").json()["archives"]
    assert len(arcs) == 1 and arcs[0]["count"] == 2


@pytest.mark.parametrize("body", [
    {"group": "decade"},
    {"group": "name"},
    {"group": "name", "label": "../x"},
    {"group": "existing"},
    {"group": "existing", "into": "General-gone.zip"},
])
def test_the_archive_endpoint_refuses_a_bad_target_and_keeps_the_file(client, body):
    a = _shelve("General-20260901-120000.db")
    res = client.post("/api/maintenance/archive", json={"names": [a.name], **body})
    assert res.status_code >= 400
    assert a.exists()


def test_the_rename_endpoint_renames_a_zip(client):
    a = _shelve("General-20260901-120000.db")
    old = client.post("/api/maintenance/archive",
                      json={"names": [a.name], "group": "name", "label": "draft"}).json()["archive"]

    res = client.post("/api/maintenance/archive-rename",
                      json={"name": old, "label": "final"}).json()

    assert res == {"ok": True, "name": "General-final.zip"}
    arcs = client.get("/api/maintenance/backups").json()["archives"]
    assert [x["name"] for x in arcs] == ["General-final.zip"]


def test_the_rename_endpoint_refuses_a_bad_name(client):
    a = _shelve("General-20260901-120000.db")
    old = client.post("/api/maintenance/archive",
                      json={"names": [a.name], "group": "name", "label": "draft"}).json()["archive"]
    res = client.post("/api/maintenance/archive-rename", json={"name": old, "label": "a/b"})
    assert res.status_code >= 400


# ----------------------------------------------------------- names and pins

def test_a_name_and_a_pin_are_written_beside_the_shelf(client):
    a = _shelve("General-20260901-120000.db")

    db.set_shelf_meta("General", a.name, label="before the move")
    db.set_shelf_meta("General", a.name, pinned=True)

    assert db.shelf_meta("General") == {
        a.name: {"label": "before the move", "pinned": True}}
    assert a.exists(), "the backup itself is untouched"


def test_clearing_the_last_field_takes_the_entry_and_the_file_with_it(client):
    """The sidecar holds what was WRITTEN about the shelf, not a row per
    backup ever taken."""
    a = _shelve("General-20260901-120000.db")
    db.set_shelf_meta("General", a.name, label="temporary")

    db.set_shelf_meta("General", a.name, label="")

    assert db.shelf_meta("General") == {}
    assert not (db.backups_dir("General") / db.SHELF_META_FILE).exists()


def test_a_name_follows_its_backup_into_an_archive_and_back(client):
    """The sidecar is keyed by filename, so archiving does not lose it."""
    a = _shelve("General-20260901-120000.db")
    db.set_shelf_meta("General", a.name, label="the one worth keeping")
    dest = db.archive_backups("General", [a.name])

    assert db.shelf_meta("General")[a.name]["label"] == "the one worth keeping"
    db.unarchive("General", dest.name)
    assert db.shelf_meta("General")[a.name]["label"] == "the one worth keeping"


def test_writing_about_a_name_outside_the_shelf_is_refused(client):
    with pytest.raises(ValueError):
        db.set_shelf_meta("General", "../memai.db", label="x")


def test_an_unreadable_sidecar_reads_as_an_unwritten_shelf(client):
    (db.backups_dir("General") / db.SHELF_META_FILE).write_text("{oops", encoding="utf-8")
    assert db.shelf_meta("General") == {}


def test_the_shelf_endpoint_carries_the_name_and_the_pin(client):
    a = _shelve("General-20260901-120000.db")
    client.post("/api/maintenance/backup-name",
                json={"name": a.name, "label": "before the move"})
    client.post("/api/maintenance/backup-pin", json={"name": a.name, "pinned": True})

    row = client.get("/api/maintenance/backups").json()["shelf"][0]

    assert row["label"] == "before the move" and row["pinned"] is True


def test_a_backup_with_nothing_written_about_it_carries_neither(client):
    _shelve("General-20260901-120000.db")
    row = client.get("/api/maintenance/backups").json()["shelf"][0]
    assert "label" not in row and "pinned" not in row


# ------------------------------------------------------------------ deleting

def test_deleting_takes_the_files_and_what_was_written_about_them(client):
    a = _shelve("General-20260901-120000.db")
    b = _shelve("General-20260902-120000.db")
    db.set_shelf_meta("General", a.name, label="gone with it")

    assert db.delete_backups("General", [a.name, b.name]) == 2

    assert not a.exists() and not b.exists()
    assert db.shelf_meta("General") == {}


def test_one_bad_name_deletes_nothing(client):
    a = _shelve("General-20260901-120000.db")

    with pytest.raises(ValueError):
        db.delete_backups("General", [a.name, "../memai.db"])

    assert a.exists()


def test_the_delete_endpoint_reports_what_it_freed(client):
    _shelve("General-20260901-120000.db", b"x" * 2048)
    res = client.post("/api/maintenance/backup-delete",
                      json={"names": ["General-20260901-120000.db"]})
    assert res.json() == {"ok": True, "deleted": 1, "freed": 2048}


# ----------------------------------------------------------------- restoring

def test_restoring_puts_the_backup_over_the_live_store(client):
    """The store is replaced through SQLite, not by swapping the file: the
    live database has a WAL beside it and readers open on it."""
    uid = client.post("/api/memories", json={
        "title": "before the restore", "type": "note", "content": "the old fact"}).json()["uid"]
    taken = client.post("/api/maintenance/backup", json={}).json()["path"]
    name = Path(taken).name
    client.post("/api/memories", json={
        "title": "after the backup", "type": "note", "content": "a newer fact"})

    res = client.post("/api/maintenance/backup-restore", json={"name": name})

    assert res.json()["ok"]
    titles = [m["title"] for m in client.get("/api/memories").json()["items"]]
    assert "before the restore" in titles
    assert "after the backup" not in titles, "the newer memory is not in the backup"
    assert client.get(f"/api/memories/{uid}").status_code == 200


def test_restoring_keeps_the_state_it_replaced(client):
    """Restoring is not undoable from here, so the current file is copied
    first and the copy says what it is."""
    client.post("/api/memories", json={
        "title": "the first", "type": "note", "content": "a fact"})
    name = Path(client.post("/api/maintenance/backup", json={}).json()["path"]).name
    client.post("/api/memories", json={
        "title": "the second", "type": "note", "content": "another fact"})

    kept = client.post("/api/maintenance/backup-restore", json={"name": name}).json()["kept"]

    assert "pre-restore" in kept
    assert (db.backups_dir("General") / kept).is_file()


def test_restoring_something_that_is_not_on_the_shelf_is_refused(client):
    with pytest.raises(ValueError):
        db.restore_backup("General", "../memai.db")
