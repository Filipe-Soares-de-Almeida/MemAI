"""Start the admin dashboard alongside the MCP server, at most once.

Opt-in: set MEMAI_ADMIN_AUTOSTART to 1/true/yes/on. Off by default -- the
dashboard has no authentication (see the warning in admin.main).

Three constraints shape the code below.

ONE MCP SERVER IS NOT ONE PROCESS. A host spawns several and they start
together at handshake, so whatever arbitrates has to be atomic across
processes. That arbiter is bind(): the kernel guarantees one listener per
port, so the losers of the race exit (see admin._bind). No lock file --
the host kills MCP servers abruptly at session end (README), and an
abandoned lock would survive and disable the dashboard silently.

A PORT THAT ANSWERS IS NOT OUR PORT. Any port number can belong to another
tool, whatever the default is, so the probe asks /api/ping and requires
MemAI to answer rather than settling for a TCP connect.

THE PORT WE ARE CONFIGURED FOR IS NOT NECESSARILY THE ONE IN USE.
run-admin.bat sets MEMAI_ADMIN_PORT inside its own process; that never
reaches an MCP server started from a desktop app's config, so probing only
the configured port would miss a dashboard started by hand and open a
second one on the same store. Hence the registry file, which the running
admin writes and this reads -- an advisory record, never a lock, and
always confirmed against a live /api/ping before it is believed. A stale
entry degrades to "start one", not to "never start one again".

Everything here is imported eagerly and on purpose: `socket` pulls in a C
extension, and embed.py documents that loading one late -- once the stdio
server's reader threads are up -- deadlocks on Windows. For the same
reason this runs from server.main() before mcp.run(), not from a
lifespan hook: the SDK enters the lifespan before the session exists, so
work there sits on the initialize path with the reader threads already
running.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from memai import db

DEFAULT_PORT = 8888
LOOPBACK = "127.0.0.1"
REGISTRY_NAME = "admin.json"
LOG_NAME = "admin.log"

# Long enough for a loopback connect that is going to succeed (measured
# well under a millisecond), short enough that a port black-holing SYNs
# cannot stall an MCP server's startup.
PROBE_TIMEOUT = 0.5

_TRUE = {"1", "true", "yes", "on"}

# Windows: run without a console and without a parent to die with. The
# dashboard is meant to outlive the agent session that opened it, the same
# way run-admin.bat does.
_DETACHED_PROCESS = 0x00000008


def _log(message: str) -> None:
    """Say it on stderr, which is where an MCP host collects server logs.

    Never stdout -- that is the protocol channel.
    """
    print(f"memai: {message}", file=sys.stderr, flush=True)


def home() -> Path:
    """MEMAI_HOME, created if needed -- db.default_db_path() does the mkdir."""
    return db.default_db_path().parent


def registry_path() -> Path:
    return home() / REGISTRY_NAME


def configured_port() -> int:
    raw = os.environ.get("MEMAI_ADMIN_PORT", "")
    try:
        port = int(raw)
    except ValueError:
        if raw:
            _log(f"MEMAI_ADMIN_PORT={raw!r} is not a number, using {DEFAULT_PORT}")
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


# What a probe found. The middle one is the case this module exists for:
# a port can be busy without being ours.
CLOSED = "closed"        # nothing accepting connections
STRANGER = "stranger"    # something is, and it is not MemAI
MEMAI = "memai"          # a dashboard, and it said so


def probe(host: str, port: int,
          timeout: float = PROBE_TIMEOUT) -> tuple[str, dict | None]:
    """Ask what is on this port. One connection answers both questions.

    Deliberately not two calls -- "is anything there" followed by "is it
    ours" would connect twice, which is slower, races against a listener
    whose backlog is full, and can disagree with itself between the two.

    Hand-rolled rather than urllib because urllib honours proxy
    environment variables, and a proxy in front of a loopback probe turns
    a local question into a network one.

    HTTP/1.0 so the server closes when it is done and the body is
    whatever arrived before EOF -- no keep-alive, no chunked framing to
    unpick for a payload this small.
    """
    try:
        conn = socket.create_connection((host, port), timeout)
    except OSError:
        return CLOSED, None
    try:
        with conn:
            conn.settimeout(timeout)
            conn.sendall(
                b"GET /api/ping HTTP/1.0\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n\r\n")
            chunks, total = [], 0
            while total < 8192:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
    except OSError:
        # Connected, then went quiet or hung up: something owns this
        # port, it just does not answer for us.
        return STRANGER, None
    head, sep, body = b"".join(chunks).partition(b"\r\n\r\n")
    if not sep or not head.startswith(b"HTTP/1"):
        return STRANGER, None
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return STRANGER, None
    if isinstance(payload, dict) and payload.get("app") == "memai":
        return MEMAI, payload
    return STRANGER, None


def ping(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> dict | None:
    """The dashboard's own reply, or None if that is not what is there."""
    state, payload = probe(host, port, timeout)
    return payload if state == MEMAI else None


def _from_registry() -> tuple[str, int] | None:
    """Where a running admin said it was, if it is still there.

    Read before the configured port, because the operator's own
    dashboard is the one this environment is least likely to know about.
    """
    try:
        record = json.loads(registry_path().read_text("utf-8"))
        host, port = str(record["host"]), int(record["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (host, port) if ping(host, port) else None


def find_running() -> tuple[str, int] | None:
    """An address a MemAI dashboard is answering on, or None."""
    found = _from_registry()
    if found:
        return found
    port = configured_port()
    return (LOOPBACK, port) if ping(LOOPBACK, port) else None


def interpreter() -> str:
    """The Python to start the dashboard with: pythonw where there is one.

    DETACHED_PROCESS is not enough to keep a console window off the
    screen. It denies the child its parent's console, but a venv's
    python.exe is a redirector that launches the real interpreter itself,
    with ordinary flags -- and Windows gives a console-subsystem process
    with no inherited console a brand new one. The result is a terminal
    that flashes up on the desktop every time an agent session starts.

    CREATE_NO_WINDOW does not fix it either: the documentation is
    explicit that the flag is ignored when combined with
    DETACHED_PROCESS. What fixes it is not being a console program.
    pythonw.exe is the same interpreter built for the GUI subsystem, so
    no console is ever allocated, for it or for what it spawns. Output
    still reaches the log file, because that is an explicit handle rather
    than an inherited console.

    Falls back to sys.executable where pythonw is absent -- some
    embedded and Linux-style layouts ship only the one binary, and a
    console nobody sees beats a dashboard that never starts.
    """
    if sys.platform != "win32":
        return sys.executable
    windowless = Path(sys.executable).with_name("pythonw.exe")
    return str(windowless) if windowless.is_file() else sys.executable


def _spawn_admin(host: str, port: int) -> None:
    """Start the dashboard as a process of its own.

    Detached, because the point is to outlive this MCP server: hosts kill
    their MCP subprocesses at session end, and a dashboard that vanished
    with whichever of the three happened to have started it would be
    worse than not having one.

    stdout and stderr go to a file and never to inherited handles. This
    process's stdout IS the MCP protocol stream; a child writing a uvicorn
    banner into it would corrupt the session. close_fds stays at its
    default of True for the same reason -- that is what makes CPython pass
    only the handles named here.
    """
    workdir = home()
    env = dict(os.environ)
    # cwd is MEMAI_HOME, not whatever directory the agent was started in:
    # on Windows a live process's cwd cannot be renamed or deleted, and an
    # orphaned dashboard would pin the operator's project folder. That
    # move is also why src/ is spelled out on PYTHONPATH.
    src_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (src_root, env.get("PYTHONPATH")) if p)

    argv = [interpreter(), "-u", "-X", "utf8", "-m", "memai.admin",
            "--host", host, "--port", str(port), "--autostarted"]

    # CREATE_BREAKAWAY_FROM_JOB is deliberately absent. It looks like
    # insurance against a host that puts its children in a kill-on-close
    # job, but on a job without BREAKAWAY_OK the CreateProcess call fails
    # outright -- trading "the dashboard dies with the session" for "the
    # MCP server does not start". Measured here: each host process gets
    # its own job with SILENT_BREAKAWAY_OK, so DETACHED_PROCESS is enough.
    flags = {"creationflags": _DETACHED_PROCESS} if sys.platform == "win32" \
        else {"start_new_session": True}

    log = None
    try:
        log = open(workdir / LOG_NAME, "ab", buffering=0)  # noqa: SIM115
    except OSError:
        pass
    try:
        subprocess.Popen(  # noqa: S603 - argv is ours, no shell
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if log else subprocess.DEVNULL,
            cwd=str(workdir),
            env=env,
            **flags)
    finally:
        if log is not None:
            log.close()   # Popen duplicated the handle it needed


def ensure_admin_running() -> None:
    """Start the dashboard if it is wanted and not already there.

    Best-effort by construction: nothing in here may raise. An optional
    convenience must never cost the caller the memory tools it actually
    came for, so every failure is a line on stderr and a return.
    """
    try:
        if os.environ.get("MEMAI_ADMIN_AUTOSTART", "").strip().lower() not in _TRUE:
            return

        host = os.environ.get("MEMAI_ADMIN_HOST", LOOPBACK).strip() or LOOPBACK
        if host not in ("127.0.0.1", "::1", "localhost"):
            # main() prints a warning for this and carries on, because a
            # person typed it and can read the warning. Nobody reads the
            # stderr of an autostarted process, so refuse instead.
            _log(f"refusing to autostart on {host}: the dashboard has no "
                 f"authentication, so autostart is loopback-only")
            return

        # The registry first: it is the only way to see a dashboard the
        # operator started with a port this environment never heard of.
        elsewhere = _from_registry()
        if elsewhere:
            _log(f"dashboard already at http://{elsewhere[0]}:{elsewhere[1]}")
            return

        port = configured_port()
        state, _ = probe(LOOPBACK, port)
        if state == MEMAI:
            _log(f"dashboard already at http://{LOOPBACK}:{port}")
            return
        if state == STRANGER:
            _log(f"port {port} answers but is not memai -- set MEMAI_ADMIN_PORT "
                 f"to a free port to autostart the dashboard")
            return

        _spawn_admin(LOOPBACK, port)
        _log(f"starting dashboard on http://{LOOPBACK}:{port} "
             f"(log: {home() / LOG_NAME})")
    except Exception as exc:  # noqa: BLE001 - deliberate: see the docstring
        _log(f"could not start the dashboard ({type(exc).__name__}: {exc})")
