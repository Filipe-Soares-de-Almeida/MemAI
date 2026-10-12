"""Per-store settings kept in the meta table."""

from __future__ import annotations

import sqlite3

from memai.lite import TASK_ASK_MINUTES_DEFAULT, WARDEN_MINUTES_DEFAULT


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# How long a generated SVG is kept. A render is a cache of the diagram, so this is a disk budget.
SVG_RETENTION_KEY = "svg_retention"
SVG_RETENTION_MODES = ("1d", "7d", "30d", "never")
SVG_RETENTION_DEFAULT = "7d"


def get_svg_retention(conn: sqlite3.Connection) -> str:
    mode = _get_meta(conn, SVG_RETENTION_KEY)
    return mode if mode in SVG_RETENTION_MODES else SVG_RETENTION_DEFAULT


def set_svg_retention(conn: sqlite3.Connection, mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in SVG_RETENTION_MODES:
        raise ValueError(
            f"svg_retention must be one of {', '.join(SVG_RETENTION_MODES)}")
    _set_meta(conn, SVG_RETENTION_KEY, mode)
    return mode


WARDEN_ENABLED_KEY = "warden_enabled"
WARDEN_ENABLED_DEFAULT = True
WARDEN_MINUTES_KEY = "warden_minutes"
# A session is one conversation, so an interval longer than a working day
# would only ever fire once; below a minute the ask lands on every turn.
WARDEN_MINUTES_RANGE = (1, 480)


def get_warden_enabled(conn: sqlite3.Connection) -> bool:
    """Whether the Stop hook may ask a session to launch the warden.

    Read from the project `conn` is on: each project carries its own switch,
    and the hook consults the active one.
    """
    value = _get_meta(conn, WARDEN_ENABLED_KEY)
    return WARDEN_ENABLED_DEFAULT if value is None else value == "1"


def set_warden_enabled(conn: sqlite3.Connection, enabled: object) -> bool:
    """Persist the warden switch. Accepts a bool or the strings a form sends."""
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("", "0", "false", "off", "no")
    _set_meta(conn, WARDEN_ENABLED_KEY, "1" if enabled else "0")
    return bool(enabled)


def get_warden_minutes(conn: sqlite3.Connection) -> int:
    """How long a session goes before the warden is asked for again.

    A floor on the cost, not a schedule: the warden reads whole turns and
    costs a subagent run, and the ask lands on the first Stop after the
    interval, never between turns.
    """
    try:
        value = int(_get_meta(conn, WARDEN_MINUTES_KEY) or "")
    except ValueError:
        return WARDEN_MINUTES_DEFAULT
    low, high = WARDEN_MINUTES_RANGE
    return value if low <= value <= high else WARDEN_MINUTES_DEFAULT


def set_warden_minutes(conn: sqlite3.Connection, minutes: object) -> int:
    """Persist the warden interval, in minutes."""
    low, high = WARDEN_MINUTES_RANGE
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"warden_minutes must be a whole number of minutes "
                         f"between {low} and {high}") from exc
    if not low <= value <= high:
        raise ValueError(f"warden_minutes must be between {low} and {high}")
    _set_meta(conn, WARDEN_MINUTES_KEY, str(value))
    return value


TASK_ASK_ENABLED_KEY = "task_ask_enabled"
TASK_ASK_ENABLED_DEFAULT = True
TASK_ASK_MINUTES_KEY = "task_ask_minutes"
# Same bounds as the warden's: a session is one conversation, and below a
# minute the ask would land on every turn.
TASK_ASK_MINUTES_RANGE = (1, 480)


def get_task_ask_enabled(conn: sqlite3.Connection) -> bool:
    """Whether the Stop hook may block a session to ask about its open tasks.

    Read from the project `conn` is on, like the warden's switch.
    """
    value = _get_meta(conn, TASK_ASK_ENABLED_KEY)
    return TASK_ASK_ENABLED_DEFAULT if value is None else value == "1"


def set_task_ask_enabled(conn: sqlite3.Connection, enabled: object) -> bool:
    """Persist the task-ask switch. Accepts a bool or the strings a form sends."""
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("", "0", "false", "off", "no")
    _set_meta(conn, TASK_ASK_ENABLED_KEY, "1" if enabled else "0")
    return bool(enabled)


def get_task_ask_minutes(conn: sqlite3.Connection) -> int:
    """How long a session goes before the Stop hook asks about tasks again."""
    try:
        value = int(_get_meta(conn, TASK_ASK_MINUTES_KEY) or "")
    except ValueError:
        return TASK_ASK_MINUTES_DEFAULT
    low, high = TASK_ASK_MINUTES_RANGE
    return value if low <= value <= high else TASK_ASK_MINUTES_DEFAULT


def set_task_ask_minutes(conn: sqlite3.Connection, minutes: object) -> int:
    """Persist the task-ask interval, in minutes."""
    low, high = TASK_ASK_MINUTES_RANGE
    try:
        value = int(str(minutes).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"task_ask_minutes must be a whole number of minutes "
                         f"between {low} and {high}") from exc
    if not low <= value <= high:
        raise ValueError(f"task_ask_minutes must be between {low} and {high}")
    _set_meta(conn, TASK_ASK_MINUTES_KEY, str(value))
    return value


def _flag(conn: sqlite3.Connection, key: str, default: bool) -> bool:
    value = _get_meta(conn, key)
    return default if value is None else value == "1"


def _set_flag(conn: sqlite3.Connection, key: str, enabled: object) -> bool:
    """Persist a switch. Accepts a bool or the strings a form sends."""
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("", "0", "false", "off", "no")
    _set_meta(conn, key, "1" if enabled else "0")
    return bool(enabled)


def _whole(conn: sqlite3.Connection, key: str, default: int, bounds: tuple[int, int]) -> int:
    try:
        value = int(_get_meta(conn, key) or "")
    except ValueError:
        return default
    low, high = bounds
    return value if low <= value <= high else default


def _set_whole(conn: sqlite3.Connection, key: str, value: object, bounds: tuple[int, int],
               unit: str) -> int:
    low, high = bounds
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a whole number of {unit} "
                         f"between {low} and {high}") from exc
    if not low <= number <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    _set_meta(conn, key, str(number))
    return number


CHECKPOINT_BLOCK_ENABLED_KEY = "checkpoint_block_enabled"
CHECKPOINT_BLOCK_ENABLED_DEFAULT = True
CHECKPOINT_DAYS_KEY = "checkpoint_days"
CHECKPOINT_DAYS_DEFAULT = 14
CHECKPOINT_DAYS_RANGE = (1, 365)


def get_checkpoint_block_enabled(conn: sqlite3.Connection) -> bool:
    """Whether a checkpoint past its age limit refuses new writes in its domain.

    Off keeps the overview's symptom and drops the refusal and the alerts.
    """
    return _flag(conn, CHECKPOINT_BLOCK_ENABLED_KEY, CHECKPOINT_BLOCK_ENABLED_DEFAULT)


def set_checkpoint_block_enabled(conn: sqlite3.Connection, enabled: object) -> bool:
    return _set_flag(conn, CHECKPOINT_BLOCK_ENABLED_KEY, enabled)


def get_checkpoint_days(conn: sqlite3.Connection) -> int:
    """How many days a checkpoint may stay active before it counts as overdue."""
    return _whole(conn, CHECKPOINT_DAYS_KEY, CHECKPOINT_DAYS_DEFAULT, CHECKPOINT_DAYS_RANGE)


def set_checkpoint_days(conn: sqlite3.Connection, days: object) -> int:
    return _set_whole(conn, CHECKPOINT_DAYS_KEY, days, CHECKPOINT_DAYS_RANGE, "days")


CONFIDENCE_ASK_ENABLED_KEY = "confidence_ask_enabled"
CONFIDENCE_ASK_ENABLED_DEFAULT = True
UNVERIFIED_DAYS_KEY = "unverified_days"
UNVERIFIED_DAYS_DEFAULT = 30
UNVERIFIED_DAYS_RANGE = (1, 365)
CONFIDENCE_ASK_MINUTES_KEY = "confidence_ask_minutes"
CONFIDENCE_ASK_MINUTES_DEFAULT = 60
CONFIDENCE_ASK_MINUTES_RANGE = (1, 480)


def get_confidence_ask_enabled(conn: sqlite3.Connection) -> bool:
    """Whether MCP results now and then list memories left unverified too long."""
    return _flag(conn, CONFIDENCE_ASK_ENABLED_KEY, CONFIDENCE_ASK_ENABLED_DEFAULT)


def set_confidence_ask_enabled(conn: sqlite3.Connection, enabled: object) -> bool:
    return _set_flag(conn, CONFIDENCE_ASK_ENABLED_KEY, enabled)


def get_unverified_days(conn: sqlite3.Connection) -> int:
    """How many days a memory may stay unverified before it counts as stale.

    One span for the overview's symptom, the freshness axis and the MCP
    reminder, so the number the dashboard shows is the set the agent is
    asked about.
    """
    return _whole(conn, UNVERIFIED_DAYS_KEY, UNVERIFIED_DAYS_DEFAULT, UNVERIFIED_DAYS_RANGE)


def set_unverified_days(conn: sqlite3.Connection, days: object) -> int:
    return _set_whole(conn, UNVERIFIED_DAYS_KEY, days, UNVERIFIED_DAYS_RANGE, "days")


def get_confidence_ask_minutes(conn: sqlite3.Connection) -> int:
    """How long one server process waits between two confidence reminders."""
    return _whole(conn, CONFIDENCE_ASK_MINUTES_KEY, CONFIDENCE_ASK_MINUTES_DEFAULT,
                  CONFIDENCE_ASK_MINUTES_RANGE)


def set_confidence_ask_minutes(conn: sqlite3.Connection, minutes: object) -> int:
    return _set_whole(conn, CONFIDENCE_ASK_MINUTES_KEY, minutes, CONFIDENCE_ASK_MINUTES_RANGE,
                      "minutes")
