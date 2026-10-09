"""A store whose task rows name items by position key opens migrated onto item ids."""

import sqlite3

import pytest

from memai import tasks
from memai.store import connection, item_ids, memories

OLD_TABLES = """
DROP TABLE task_note_depends; DROP TABLE task_note_items; DROP TABLE task_comments;
DROP TABLE task_item_links; DROP TABLE task_items;
CREATE TABLE task_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, memory_uid TEXT NOT NULL REFERENCES memories(uid),
    item_key TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'todo', updated_at TEXT NOT NULL,
    updated_session TEXT NOT NULL DEFAULT '', UNIQUE (memory_uid, item_key));
CREATE TABLE task_item_links (
    memory_uid TEXT NOT NULL REFERENCES memories(uid), item_key TEXT NOT NULL,
    target_uid TEXT NOT NULL REFERENCES memories(uid), created_at TEXT NOT NULL,
    PRIMARY KEY (memory_uid, item_key, target_uid));
CREATE TABLE task_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, memory_uid TEXT NOT NULL REFERENCES memories(uid),
    item_key TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, author TEXT NOT NULL DEFAULT 'agent',
    session TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE task_note_items (
    note_id INTEGER NOT NULL REFERENCES task_notes(id), item_key TEXT NOT NULL,
    PRIMARY KEY (note_id, item_key));
ALTER TABLE tasks ADD COLUMN item_seq INTEGER NOT NULL DEFAULT 0;
"""

BRIEF = ("GOAL: aim the beam\nCONTEXT: bench two\nSTEPS: aim, fire\nPITFALLS: glare\n"
         "DONE WHEN: it lights\nDEPENDS ON: {depends}")


def _old_store(path, keys=("i1", "i2", "i3")):
    """A store with one task written the way the release before item ids wrote it."""
    with connection.connect(path) as conn:
        uid = memories.insert_memory(conn, type="task", content="GOAL: Light the bench",
                                     title="Ship the lantern", domain="acme")
        target = memories.insert_memory(conn, type="note", content="flux first", title="Flux",
                                        domain="acme")
        conn.execute("INSERT INTO tasks (memory_uid, goal) VALUES (?, 'Light the bench')", (uid,))
        conn.executescript(OLD_TABLES)
        for n, key in enumerate(keys, start=1):
            conn.execute("INSERT INTO task_items (memory_uid, item_key, seq, text, updated_at) "
                         "VALUES (?, ?, ?, ?, '2026-01-01')", (uid, key, n, f"step {key}"))
        conn.execute("INSERT INTO task_item_links VALUES (?, ?, ?, '2026-01-01')", (uid, keys[0], target))
        conn.execute("INSERT INTO task_comments (memory_uid, item_key, body, created_at) "
                     "VALUES (?, ?, 'on the item', '2026-01-01')", (uid, keys[1]))
        conn.execute("INSERT INTO task_comments (memory_uid, item_key, body, created_at) "
                     "VALUES (?, '', 'on the task', '2026-01-02')", (uid,))
        conn.execute("INSERT INTO task_comments (memory_uid, item_key, body, created_at) "
                     "VALUES (?, 'i99', 'stray', '2026-01-03')", (uid,))
        note = conn.execute(
            "INSERT INTO task_notes (memory_uid, title, body, created_at, updated_at) "
            "VALUES (?, 'Beam', ?, '2026-01-01', '2026-01-01')",
            (uid, BRIEF.format(depends=f'{keys[0]} (power), deleted "Old step"'))).lastrowid
        conn.execute("INSERT INTO task_note_items VALUES (?, ?)", (note, keys[2]))
        loose = conn.execute(
            "INSERT INTO task_notes (memory_uid, title, body, created_at, updated_at) "
            "VALUES (?, 'Loose', 'after i2, see the bench', '2026-01-01', '2026-01-01')",
            (uid,)).lastrowid
        stamp = memories.get_memory(conn, uid)["updated_at"]
    return uid, target, note, loose, stamp


def test_a_store_keyed_by_position_opens_on_item_ids(tmp_path):
    uid, target, note, loose, _ = _old_store(tmp_path / "old.db")
    with connection.connect(tmp_path / "old.db") as conn:
        assert not item_ids.needs_migration(conn)
        ids = tasks.item_ids(conn, uid)
        task = tasks.get_task(conn, uid)
        assert [i["text"] for i in task["items"]] == ["step i1", "step i2", "step i3"]
        assert task["items"][0]["links"][0]["uid"] == target
        assert [(c["item"], c["body"]) for c in task["comments"]] == [
            (ids[1], "on the item"), (None, "on the task")]
        assert tasks.dependencies(conn, note) == [
            {"item": ids[0], "text": "", "reason": "power"},
            {"item": None, "text": "Old step", "reason": ""}]
        assert task["notes"][0]["items"] == [ids[2]]
        assert tasks.note(conn, uid, loose)["body"] == "after i2, see the bench"
        assert tasks.dependencies(conn, loose) is None


def test_the_migration_drops_item_seq_and_writes_content_by_id(tmp_path):
    uid, *_ = _old_store(tmp_path / "old.db")
    with connection.connect(tmp_path / "old.db") as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
        assert "item_seq" not in cols
        ids = tasks.item_ids(conn, uid)
        assert f"[ ] {ids[0]} step i1" in memories.get_memory(conn, uid)["content"]


def test_the_migration_keeps_the_task_updated_stamp(tmp_path):
    uid, *_, stamp = _old_store(tmp_path / "old.db")
    with connection.connect(tmp_path / "old.db") as conn:
        assert memories.get_memory(conn, uid)["updated_at"] == stamp


def test_one_backup_is_taken_beside_a_file_outside_the_store_home(tmp_path):
    _old_store(tmp_path / "old.db")
    with connection.connect(tmp_path / "old.db"):
        pass
    with connection.connect(tmp_path / "old.db"):
        pass
    backups = sorted(p.name for p in tmp_path.glob("old-item-ids-*.db"))
    assert len(backups) == 1
    raw = sqlite3.connect(tmp_path / backups[0])
    try:
        assert "item_key" in {r[1] for r in raw.execute("PRAGMA table_info(task_items)")}
    finally:
        raw.close()


def test_a_second_migration_finds_nothing_to_do(tmp_path):
    _old_store(tmp_path / "old.db")
    with connection.connect(tmp_path / "old.db") as conn:
        assert item_ids.migrate(conn, tmp_path / "old.db") is None


def test_gapped_keys_migrate_by_key(tmp_path):
    uid, *_ = _old_store(tmp_path / "old.db", keys=("i1", "i2", "i4"))
    with connection.connect(tmp_path / "old.db") as conn:
        ids = tasks.item_ids(conn, uid)
        assert tasks.get_task(conn, uid)["notes"][0]["items"] == [ids[2]]


def test_ids_handed_out_before_the_migration_never_come_back(tmp_path):
    uid, *_ = _old_store(tmp_path / "old.db")
    raw = sqlite3.connect(tmp_path / "old.db")
    try:
        raw.execute("UPDATE sqlite_sequence SET seq = 500 WHERE name = 'task_items'")
        raw.commit()
    finally:
        raw.close()
    with connection.connect(tmp_path / "old.db") as conn:
        new = tasks.add_items(conn, uid, ["pack it"])["ids"]
        assert new[0] > 500


def test_a_failed_migration_leaves_the_old_schema(tmp_path, monkeypatch):
    _old_store(tmp_path / "old.db")

    def boom(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(item_ids, "_split_briefs", boom)
    with pytest.raises(RuntimeError, match="boom"), connection.connect(tmp_path / "old.db"):
        pass
    raw = sqlite3.connect(tmp_path / "old.db")
    try:
        assert "item_key" in {r[1] for r in raw.execute("PRAGMA table_info(task_items)")}
    finally:
        raw.close()


def test_a_legacy_export_record_imports_by_key(tmp_path):
    record = {
        "record": "task", "uid": "0123456789abcdef", "goal": "Light the bench", "state": "open",
        "completed_at": "", "item_seq": 3,
        "items": [{"key": "i1", "seq": 1, "text": "solder", "state": "done"},
                  {"key": "i2", "seq": 2, "text": "flash", "state": "todo"}],
        "links": [],
        "comments": [{"item_key": "i2", "body": "careful", "author": "agent"},
                     {"item_key": "", "body": "overall", "author": "person"}],
        "notes": [{"title": "Flash", "items": ["i2"], "body": BRIEF.format(depends="i1 (power)")}],
    }
    with connection.connect(tmp_path / "new.db") as conn:
        memories.restore_memory(conn, {"record": "memory", "uid": record["uid"], "type": "task",
                                       "content": "GOAL: Light the bench", "domain": "acme"})
        tasks.restore_task(conn, record)
        ids = tasks.item_ids(conn, record["uid"])
        task = tasks.get_task(conn, record["uid"])
        assert [c["item"] for c in task["comments"]] == [ids[1], None]
        assert task["notes"][0]["items"] == [ids[1]]
        note_id = task["notes"][0]["id"]
        assert tasks.dependencies(conn, note_id) == [{"item": ids[0], "text": "", "reason": "power"}]


def test_a_store_from_before_task_notes_opens_migrated(tmp_path):
    uid, target, *_ = _old_store(tmp_path / "old.db")
    raw = sqlite3.connect(tmp_path / "old.db")
    try:
        raw.executescript("DROP TABLE task_note_items; DROP TABLE task_notes;")
        raw.commit()
    finally:
        raw.close()
    with connection.connect(tmp_path / "old.db") as conn:
        assert not item_ids.needs_migration(conn)
        task = tasks.get_task(conn, uid)
        assert [i["text"] for i in task["items"]] == ["step i1", "step i2", "step i3"]
        assert task["items"][0]["links"][0]["uid"] == target
        assert task["notes"] == []


def test_a_migration_that_keeps_failing_takes_one_backup(tmp_path, monkeypatch):
    _old_store(tmp_path / "old.db")

    def boom(conn):
        raise RuntimeError("boom")

    monkeypatch.setattr(item_ids, "_split_briefs", boom)
    for _ in range(3):
        with pytest.raises(RuntimeError), connection.connect(tmp_path / "old.db"):
            pass
    assert len(list(tmp_path.glob("old-item-ids-*.db"))) == 1


def test_a_brief_of_a_task_whose_memory_is_gone_does_not_stop_the_store_opening(tmp_path):
    uid, *_ = _old_store(tmp_path / "old.db")
    raw = sqlite3.connect(tmp_path / "old.db")
    try:
        raw.execute("PRAGMA foreign_keys = OFF")
        raw.execute("DELETE FROM memories WHERE uid = ?", (uid,))
        raw.commit()
    finally:
        raw.close()
    with connection.connect(tmp_path / "old.db") as conn:
        assert not item_ids.needs_migration(conn)
        assert conn.execute("SELECT COUNT(*) FROM task_note_depends").fetchone()[0] == 0
