"""Tasks: creating one, and editing its goal, items, comments, notes and links."""

from __future__ import annotations

import sqlite3
from typing import cast

from starlette.routing import Route

from memai import admin_schemas as schema
from memai import tasks
from memai.admin.api import api
from memai.store import connection, memories, sections


def _lines(value) -> list[str]:
    """Items given as text (one per line) or as a list of strings."""
    if isinstance(value, str):
        return tasks.split_items(value)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _item(payload) -> int:
    """The item a request names, as an id; 0 when it names none."""
    try:
        return int(payload.get("item") or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{payload.get('item')!r} is not an item id") from None


def _task_view(conn: sqlite3.Connection, uid: str) -> dict | None:
    """The task with what each [[uid]] and [[#id]] in it names, and its notes' brief fields and dependencies."""
    task = tasks.get_task(conn, uid)
    if task is None:
        return None
    for note in task["notes"]:
        note["body_links"] = sections.body_links(conn, uid, note["body"])
        note["brief"] = tasks.brief_fields(note["body"])
        note["depends"] = tasks.dependencies(conn, note["id"])
    task["refs"] = tasks.refs(conn, uid, [task["goal"], *(n["body"] for n in task["notes"]),
                                          *(c["body"] for c in task["comments"])])
    task["body_links"] = sections.body_links(
        conn, uid, "\n".join([task["goal"], *(c["body"] for c in task["comments"])]))
    return task


def _task_answer(conn: sqlite3.Connection, uid: str) -> schema.TaskAnswer:
    """The task and its memory status after a write, so a view re-renders from one answer."""
    return cast(schema.TaskAnswer, {"task": _task_view(conn, uid), "status": memories.memory_row(conn, uid)["status"]})


# A ValueError from tasks.* can arrive after rows were written, so each handler
# lets it leave the connection block and the transaction rolls back.
def create_task(request, payload) -> schema.TaskCreated:
    with connection.connect() as conn:
        uid = tasks.create_task(
            conn,
            title=payload.get("title") or "",
            goal=payload.get("goal") or "",
            items=_lines(payload.get("items")),
            domain=(payload.get("domain") or "").strip(),
            also=payload.get("also") or "",
            tags=(payload.get("tags") or "").strip(),
            session=(payload.get("session") or "").strip(),
        )
        return cast(schema.TaskCreated, {"uid": uid, **_task_answer(conn, uid)})


def task_item_state(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.set_item_state(conn, uid, _item(payload), payload.get("state") or "")
        return _task_answer(conn, uid)


def task_item_text(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.rename_item(conn, uid, _item(payload), payload.get("text") or "")
        return _task_answer(conn, uid)


def task_delete_item(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.delete_item(conn, uid, _item(payload))
        return _task_answer(conn, uid)


def task_add_items(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.add_items(conn, uid, _lines(payload.get("items")))
        return _task_answer(conn, uid)


def task_goal(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.set_goal(conn, uid, payload.get("goal") or "")
        return _task_answer(conn, uid)


def task_comment(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.add_comment(conn, uid, payload.get("body") or "",
                          item=_item(payload), author="person")
        return _task_answer(conn, uid)


def task_note(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    items = [int(k) for k in payload.get("items") or []]
    with connection.connect() as conn:
        if payload.get("id"):
            tasks.edit_note(conn, uid, int(payload["id"]), title=payload.get("title") or "",
                            body=payload.get("body") or "",
                            items=items if "items" in payload else None)
        else:
            tasks.add_note(conn, uid, title=payload.get("title") or "",
                           body=payload.get("body") or "", items=items)
        return _task_answer(conn, uid)


def task_delete_note(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.delete_note(conn, uid, int(payload.get("id") or 0))
        return _task_answer(conn, uid)


def _targets(value) -> list[str]:
    """A link target given as one uid or as a list of them."""
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value or "")]


def task_link(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        tasks.link_item(conn, uid, _item(payload), _targets(payload.get("target")))
        return _task_answer(conn, uid)


def task_unlink(request, payload) -> schema.TaskAnswer:
    uid = request.path_params["uid"]
    with connection.connect() as conn:
        for target in _targets(payload.get("target")) or [""]:
            tasks.unlink_item(conn, uid, _item(payload), target)
        return _task_answer(conn, uid)


ROUTES = [
    Route("/api/tasks", api(create_task), methods=["POST"]),
    Route("/api/tasks/{uid}/item", api(task_item_state), methods=["POST"]),
    Route("/api/tasks/{uid}/item", api(task_delete_item), methods=["DELETE"]),
    Route("/api/tasks/{uid}/item/text", api(task_item_text), methods=["POST"]),
    Route("/api/tasks/{uid}/items", api(task_add_items), methods=["POST"]),
    Route("/api/tasks/{uid}/goal", api(task_goal), methods=["POST"]),
    Route("/api/tasks/{uid}/comment", api(task_comment), methods=["POST"]),
    Route("/api/tasks/{uid}/note", api(task_note), methods=["POST"]),
    Route("/api/tasks/{uid}/note", api(task_delete_note), methods=["DELETE"]),
    Route("/api/tasks/{uid}/link", api(task_link), methods=["POST"]),
    Route("/api/tasks/{uid}/link", api(task_unlink), methods=["DELETE"]),
]
