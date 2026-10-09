<h1 align="center">MemAI</h1>

<p align="center">
  <b>Long-term memory for agentic coding.</b><br>
  One SQLite file, keyword retrieval, MCP. Built for Claude Code.
</p>

<p align="center">
  <a href="LICENSE"><img alt="Licence: MIT" src="https://img.shields.io/badge/licence-MIT-blue.svg"></a>
  <img alt="Python 3.12-3.14" src="https://img.shields.io/badge/python-3.12--3.14-blue.svg">
  <img alt="Node 22.18+" src="https://img.shields.io/badge/node-22.18%2B-5fa04e.svg">
  <img alt="Dashboard: Vue 3 + TypeScript" src="https://img.shields.io/badge/dashboard-Vue%203%20%2B%20TypeScript-42b883.svg">
  <img alt="MCP server" src="https://img.shields.io/badge/MCP-server-6f4ff2.svg">
  <img alt="Storage: SQLite + FTS5" src="https://img.shields.io/badge/storage-SQLite%20%2B%20FTS5-003b57.svg">
</p>

---

Agents call MemAI's MCP tools to write memories — facts, decisions, checkpoints,
pitfalls, documented flows — during a session and read them back in later ones,
which is the state an MCP server's own process does not keep between
conversations.

The tools answer any MCP host. What surrounds them targets Claude Code: the hook
events that put the store in front of a session, the bundled skills, and the
warden subagent that consults it on a session's behalf.

## Highlights

- **Types, not one blob.** `note`, `reasoning`, `anti_pattern`, `checkpoint`,
  `task`, `diagram` — a pitfall is read back by the tool that asks for
  pitfalls, not found by luck among everything else. A `task` is a goal and a
  checklist: items move through `todo`, `doing`, `done` and `dropped`, each
  carries its own comments and linked memories, and the task archives itself
  once every item is closed.
- **Domains are paths.** A memory filed on `acme/checkout/billing` still answers
  a read of `acme`, and `also` cross-lists it under the subjects that cut across
  that tree.
- **Keyword retrieval, nothing to download.** SQLite FTS5 with BM25 over title,
  content, tags and domain. No embedding model, no network call, no GPU.
- **The store reaches a session by itself.** Hook events warm a cold session and
  prompt it to write; the warden subagent reports only the memories that bear on
  what is actually happening.
- **Curation stays a person's.** Confidence, decay dates, dedup and staged
  suggestions: an agent proposes, a human applies them in the dashboard.
- **One file holds a project.** Rows, keyword index, edit history, relations,
  diagrams: a project's whole memory in one SQLite file. Keep one for
  everything, or one per project and switch between them from the dashboard.
  Copy it, back it up, delete it.

## What it looks like

Mid-task, an agent writes down what it just paid for:

```python
note(
    title="Stripe sends charge.succeeded twice for one charge",
    domain="acme/checkout/billing",
    tags="idempotency, webhook, retry, duplicate delivery",
    content="A retry carries the same event id, so the handler has to key off "
            "the event id. Keying off the charge id lets the second delivery "
            "book the order again.",
)
```

Days later a cold session opens on that subject and asks for its bearing:

```python
pulse("acme/checkout")
```

It gets the latest checkpoint in full and a count of what is pending anywhere
under that path: open tasks, pitfalls, handoffs, notes and flows. The session
asks for the category it needs, and gets headers it can open:

```python
must_read("acme/checkout", type="note")    # titles and uids, that note among them
get_memory(uid)                          # the one the work touches, in full
```

A task is how work reaches the next session. The agent files it with a goal and
one item per line, and works it as it goes:

```python
task(
    title="Move billing webhooks to the idempotent handler",
    goal="Every billing webhook is deduplicated by event id.",
    items="Key charge.succeeded by event id\nCover refunds and disputes\n"
          "Remove the charge-id lookup",
    domain="acme/checkout/billing",
)
task_item(uid, "i1", state="done", comment="Handler keys off the event id.")
task_note(uid, title="Refund replay", items="i2",
          goal="Replay a refund once, however often it arrives.",
          context="Refunds arrive twice on retry.", steps="Key refunds by event id.",
          pitfalls="A dispute reuses the refund's event id.", done_when="The replay test passes.",
          depends_on="i1")
```

An item is a short label, at most 80 characters; its detail goes in a brief.
What only makes sense inside the task — an item's brief, a rule several items
share — is a task note, not a memory. A note on items is a brief: GOAL, CONTEXT,
STEPS, PITFALLS, DONE WHEN and DEPENDS ON, with EXTRA INFO for anything else.
DEPENDS ON is the one place an item is cited by its key: keys are positions,
and free text is not rewritten when they move. `task_item` also renames an
item or deletes one, and `edit_memory(uid, goal=...)` rewrites the goal, so a
task is corrected in place. Search, recall and the session brief never return
a note. `get_memory(uid)` on a
task returns its head (goal, progress, counts), and `task_read(uid, part)`
reads its items, notes, comments and links one page at a time. Every tool
result stays under the size Claude Code shows inline: what can grow comes back
in pages with a `next_offset`. `memai-store task-adopt` turns memories linked to
a task's items into its notes, and refuses one that would land on items
without being a brief.

Open tasks come first in the session-start brief. The PreToolUse hook notes the
domains a session names in its memai calls, and at the end of a turn the `stop`
hook blocks once per interval to ask the agent to update the open tasks of those
domains, and to check similar domains for a task filed under another path. A
session that named no domain is not asked. The
dashboard's Maintenance view switches that reminder off and sets its interval;
a task's checklist, comments and linked memories are worked there as well. A
closed task is an archived memory, so `list_by_domain(domain, type="task",
status="archived")` lists the completed and cancelled ones.

---

## Quickstart

```sh
python -m venv .venv
.venv/Scripts/pip install --no-deps -e .   # .venv/bin/pip off Windows
.venv/Scripts/pip install --require-hashes -r requirements-dev.txt
.venv/Scripts/python tools/install-webui.py   # the admin dashboard
.venv/Scripts/python -m pytest
npm run typecheck && npm test           # the dashboard's own checks
```

Register the server with your host, then let it reach a session by itself — the
hooks put the store in front of an agent that did not ask for it, and the
bundled skills and subagent teach it what to do with them:

```sh
claude mcp add --scope user memai C:\path\to\MemAI\.venv\Scripts\memai-mcp.exe

memai-hook install            # the four hook events
memai-hook install --skills   # the bundled skills
memai-hook install --agents   # the warden subagent
memai-hook install --check    # what is registered, and what is out of date
```

See [Hooks and the warden](../../wiki/Hooks-and-the-warden) for what each event
emits and how to turn the warden off.

> [!NOTE]
> **Two hosts, two different files.** Neither reads the other's, so a server
> registered in one is invisible to the other, and an empty list in one says
> nothing about the other.

| host | the file it reads | how to write it |
|---|---|---|
| Claude Code CLI — `claude` in a terminal | `~/.claude.json`, top-level `mcpServers` | `claude mcp add --scope user memai <command>` |
| Claude desktop app — Chat and the Code tab | Windows: `%APPDATA%\Claude\claude_desktop_config.json`<br>macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` | edit the file, or Settings → Developer → Edit configuration |

<details>
<summary><b>Install details</b> — the config block, the PATH trap, and the Windows batch files</summary>

Both hosts take the same block, pointing at the console script the install put
in the environment:

```json
{
  "mcpServers": {
    "memai": {
      "command": "memai-mcp"
    }
  }
}
```

A bare `memai-mcp` resolves only if that environment's `Scripts\` (`bin/` off
Windows) is on the PATH of the process that launches the server — and a GUI app
inherits the desktop session's PATH, not your shell's. Unless you know it is
there, give the absolute path instead:

```json
{
  "mcpServers": {
    "memai": {
      "command": "C:\\path\\to\\MemAI\\.venv\\Scripts\\memai-mcp.exe"
    }
  }
}
```

`claude mcp list` reports what the CLI loaded; the desktop app lists what it
loaded under Settings → Developer → local MCP servers. A host reads its config
once, at startup, so restart it after an edit.

On Windows, `install.bat` does the venv, install and dashboard-build steps,
`update.bat` pulls the checkout and then runs `install.bat`, and `run-admin.bat`
starts the dashboard (it activates `.venv` itself; extra arguments pass
through, e.g. `run-admin.bat --port 8890`). `stop-mcp.bat` and `stop-admin.bat`
stop what is running. `install.bat` and `update.bat` refuse to start while an
MCP server or the dashboard still runs from the checkout, list what is open,
and offer to close it.

> [!IMPORTANT]
> Windows locks an .exe while a process is running it, so a `pip install` cannot
> rewrite `.venv\Scripts` until every MCP server and the dashboard are down.

An agent installing MemAI on a new machine follows
[.agents/install.md](.agents/install.md): requirements, the order that keeps a
running server from breaking the install, both MCP config files, and the checks
that confirm the result.

**Staying current.** The `stop` hook asks GitHub, at most once per interval
(a day unless chosen otherwise), which releases are published and caches them —
tag, page and notes, one record per release — in `MEMAI_HOME/update.json`. A session that starts behind is told which version it
runs, how many releases came after it, what each of them changed (their notes,
flattened to a few plain lines) and the commands that update this checkout
— for the person to run once every session and the dashboard are closed, for
the reason above. The dashboard shows the same thing at leisure: the version
mark in its app bar carries the count and opens **Releases**, which renders the
packaged `CHANGELOG.md` with the running version marked and anything published
since it at the top. There, **Check now** asks GitHub at once, and **Check every**
sets the interval, from an hour to a week (the server accepts up to 30 days);
the choice is kept in `MEMAI_HOME/update-settings.json` and holds for every
project. `memai-hook install --check` prints the same comparison.
`MEMAI_UPDATE_CHECK=0` stops the request, and belongs in the environment the
host itself runs in: the hook and the dashboard make it, not the MCP server,
which only reads what they cached.

</details>

---

## The dashboard

`memai-admin` (or `python -m memai.admin`) serves the store at
`http://127.0.0.1:8888` — loopback only; `--host` / `--port` /
`MEMAI_ADMIN_PORT` to change. It is where memories are read, edited, triaged and
curated by a person, where the diagrams are arranged, and — behind the version
mark in its app bar — where the release history is read.

`memai-admin --status` says where it is, `memai-admin --stop` stops it.

The dashboard is a Vue 3 and TypeScript app that Vite builds into
`src/memai/webui/dist/`. Without Node 22.18+ or a reachable npm registry, the
install takes the prebuilt one from the release instead. On start, `memai-admin`
rebuilds it when its sources differ from the ones the build was stamped with,
running `npm ci` too when the lockfile changed; it serves the build it has when
npm is missing or fails. `MEMAI_ADMIN_BUILD=0` turns that off.

A memory can be pinned from its record: for every domain, or for its own
domain only (its subdomains and cross-listed paths included). `must_read()` and
`pulse()` count pinned memories beside the categories, the session brief
counts the global ones, and an agent lists them with
`must_read(type=..., pinned=true)` and reads every one before acting.

A host starts several MCP servers per session, so the dashboard is started once
and shared: each server asks `/api/ping` whether one already answers before
trying to bind, and the one that wins keeps the port. It is detached on purpose,
so it outlives the session that opened it.

<details>
<summary><b>Let the MCP server open it with the session</b> — off unless asked</summary>

```json
{
  "mcpServers": {
    "memai": {
      "command": "memai-mcp",
      "env": {
        "MEMAI_HOME": "/path/to/your/memai-store",
        "MEMAI_ADMIN_AUTOSTART": "1",
        "MEMAI_ADMIN_PORT": "8888"
      }
    }
  }
}
```

Every variable MemAI reads belongs in that block: a server the host launches
sees this environment and no other, not your shell's. `MEMAI_HOME` is a
placeholder — drop the line to keep the store at `~/.memai`, and on Windows mind
that JSON wants its backslashes doubled.

</details>

## Where the data lives

Under `$MEMAI_HOME` if it is set, otherwise `~/.memai`. Not tracked in git —
user data, created on first run.

| path | what it is |
|---|---|
| `memai.db` | the `General` project, the one every install starts with |
| `projects/<name>.db` | every other project, one file each, named as you named it |
| `active` | one line naming the project in use; absent means `General` |
| `backups/` | `VACUUM INTO` copies of `General`, named `General-<stamp>.db`; any other project's go in `backups/<name>/` |
| `renders/`, `warden/` | generated SVGs, and the warden's per-session state |

A project is one whole memory: its own domains, relations, diagrams and
settings. Any name that works as a Windows file name works as a project
name, and two names that differ only in case are one project. The switch is
in the dashboard's top bar, and every MCP server, hook and dashboard on the
machine opens the active project on its next call — no restart. Every
write's result and every `pulse()` name the project they touched, and
`list_projects()` lists them all.

Memories move between projects from the dashboard — a selection in
Memories, or a whole domain in Domains — with `move_to_project()`, or with
`memai-store move`. A move copies each memory with its history into the
target, checks it there and only then removes the original, after a backup
of the source is written. What the copy cannot carry — a relation to a
memory outside the selection, a diagram jump across it — is reported before
anything moves.

---

## Documentation

How the parts work lives in the [wiki](../../wiki):

| section | pages |
|---|---|
| getting started | [Getting started](../../wiki/Getting-started) · [Updating](../../wiki/Updating) · [Troubleshooting](../../wiki/Troubleshooting) |
| concepts | [How a session uses memory](../../wiki/How-a-session-uses-memory) · [Memory types](../../wiki/Memory-types) · [Domains](../../wiki/Domains) · [Projects](../../wiki/Projects) |
| guides | [Tasks](../../wiki/Tasks) · [Diagrams](../../wiki/Diagrams) · [Curation](../../wiki/Curation) · [Dashboard](../../wiki/Dashboard) · [Backups and export](../../wiki/Backups-and-export) |
| reference | [Tools](../../wiki/Tools) · [Hooks and the warden](../../wiki/Hooks-and-the-warden) · [Configuration](../../wiki/Configuration) · [Storage](../../wiki/Storage) · [Retrieval](../../wiki/Retrieval) |

## Licence

MemAI is MIT. Roboto is bundled in `webui/fonts/` under the SIL Open Font
License 1.1 (`webui/fonts/OFL.txt`), separate from MemAI's own licence.
