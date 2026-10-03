"""Refuse an install while something still runs from this checkout.

install.bat and update.bat run this before pip touches .venv. Windows keeps an
.exe locked while a process runs it, so pip cannot rewrite
.venv\\Scripts\\memai-mcp.exe under a live MCP server, and a half-written
launcher breaks every session after it. A dashboard serving from this checkout
would keep running code the install replaces underneath it.

It looks for memai-mcp.exe processes under this checkout's .venv and for a
MemAI dashboard answering /api/ping on MEMAI_ADMIN_PORT (8888 by default) or
on the port recorded in MEMAI_HOME/admin.json. A server or dashboard from
another checkout does not count; one whose checkout cannot be told does.

Exit code 0 lets the install go on, 1 stops it. From a console it offers to
close what it found. Standard library only: it runs before the install does.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8888
PING_TIMEOUT = 1.0
YES = {"y", "yes", "s", "sim"}
CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class Running:
    kind: str           # "mcp" or "dashboard"
    pid: int
    where: str          # the executable, or the dashboard's address
    port: int | None = None


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").casefold()


def _inside(path: str, folder: Path) -> bool:
    return _norm(path).startswith(_norm(str(folder)) + "/")


def list_mcp_processes() -> list[tuple[int, str]]:
    """(pid, executable path) of every memai-mcp.exe; the path is "" when hidden."""
    if sys.platform != "win32":
        return []
    query = ("Get-CimInstance Win32_Process -Filter \"Name='memai-mcp.exe'\" | "
             "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.ExecutablePath }")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True, text=True, timeout=30, check=False,
            creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in done.stdout.splitlines():
        pid, _, path = line.strip().partition("|")
        if pid.isdigit():
            found.append((int(pid), path.strip()))
    return found


def ping(host: str, port: int) -> dict | None:
    """The /api/ping answer of a MemAI dashboard on host:port, or None."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/ping",
                                    timeout=PING_TIMEOUT) as reply:
            body = json.loads(reply.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) and body.get("app") == "memai" else None


def _memai_home() -> Path:
    raw = os.environ.get("MEMAI_HOME", "")
    return Path(raw) if raw else Path.home() / ".memai"


def _configured_port() -> int:
    try:
        port = int(os.environ.get("MEMAI_ADMIN_PORT", ""))
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def _dashboard_addresses() -> list[tuple[str, int]]:
    """The registered dashboard first, then the configured port."""
    addresses = []
    try:
        record = json.loads((_memai_home() / "admin.json").read_text("utf-8"))
        addresses.append((str(record["host"]), int(record["port"])))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    addresses.append(("127.0.0.1", _configured_port()))
    return list(dict.fromkeys(addresses))


def find_running(root: Path, *, processes=list_mcp_processes, ping=ping) -> list[Running]:
    """What runs from the checkout at root and would break under an install."""
    found = [Running("mcp", pid, path or "(path hidden)")
             for pid, path in processes()
             if not path or _inside(path, root / ".venv")]

    seen = set()
    for host, port in _dashboard_addresses():
        body = ping(host, port)
        if body is None or body.get("pid") in seen:
            continue
        theirs = str(body.get("root") or "")
        if theirs and _norm(theirs) != _norm(str(root)):
            continue
        seen.add(body.get("pid"))
        found.append(Running("dashboard", int(body.get("pid") or 0),
                             f"http://{host}:{port}", port))
    return found


def kill(pid: int) -> None:
    """End pid and the processes it started."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/f", "/t", "/pid", str(pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


LABELS = {"mcp": "MCP server", "dashboard": "dashboard"}


def _report(found: list[Running], say) -> None:
    say("")
    say("Install stopped: MemAI is still running from this checkout.")
    for item in found:
        say(f"  {LABELS[item.kind]:<11} pid {item.pid:<7} {item.where}")
    say("")
    say("The install replaces files these processes hold open. Close them first:")
    say("  - quit Claude (Desktop and Code) to end its MCP servers")
    say("  - run stop-admin.bat to stop the dashboard server;")
    say("    closing its browser tab leaves the server running")
    say("")


def main(root: Path, *, find=find_running, ask=input,
         interactive: bool | None = None, kill=kill, say=print,
         wait=lambda: time.sleep(2)) -> int:
    found = find(root)
    if not found:
        return 0
    _report(found, say)
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        return 1
    try:
        answer = ask("Close them now? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().casefold() not in YES:
        return 1
    for item in found:
        kill(item.pid)
    wait()
    left = find(root)
    if left:
        say("Still running:")
        for item in left:
            say(f"  {LABELS[item.kind]:<11} pid {item.pid:<7} {item.where}")
        return 1
    say("Closed. Reopen Claude once the install has finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parents[1]))
