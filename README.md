<h1 align="center">MemAI</h1>

<p align="center">
  <b>Long-term memory for agentic coding.</b><br>
  One SQLite file, keyword retrieval, MCP. Built for Claude Code.
</p>

<p align="center">
  <a href="LICENSE"><img alt="Licence: MIT" src="https://img.shields.io/badge/licence-MIT-blue.svg"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue.svg">
  <img alt="Node 20.19+" src="https://img.shields.io/badge/node-20.19%2B-5fa04e.svg">
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
  `handoff`, `diagram` — a pitfall is read back by the tool that asks for
  pitfalls, not found by luck among everything else.
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
- **One file holds all of it.** Rows, keyword index, edit history, relations,
  diagrams. Copy it, back it up, delete it.

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

It gets the latest checkpoint in full, the open handoffs and anti-patterns filed
anywhere under that path, the newest notes — that one among them — and a count
of what the scope holds that the warm-up did not show.

---

## Quickstart

```sh
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # .venv/bin/pip off Windows
npm ci && npm run build                 # the admin dashboard
.venv/Scripts/python -m pytest
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
| Claude Code — CLI and desktop UI alike | `~/.claude.json`, top-level `mcpServers` | `claude mcp add --scope user memai <command>` |
| Claude Desktop — the chat app | Windows: `%APPDATA%\Claude\claude_desktop_config.json`<br>macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` | edit the file, or Settings → Developer → Edit configuration |

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

`claude mcp list` reports what Claude Code loaded; the desktop app lists what it
loaded under Settings → Developer → local MCP servers. A host reads its config
once, at startup, so restart it after an edit.

On Windows, `install.bat` does the venv, install and dashboard-build steps, and
`run-admin.bat` starts the dashboard (both activate `.venv` themselves; extra
arguments pass through, e.g. `run-admin.bat --port 8890`). `stop-mcp.bat` and
`stop-admin.bat` stop what is running.

> [!IMPORTANT]
> Windows locks an .exe while a process is running it, so a `pip install` cannot
> rewrite `.venv\Scripts` until every MCP server and the dashboard are down.

An agent installing MemAI on a new machine follows
[.agents/install.md](.agents/install.md): requirements, the order that keeps a
running server from breaking the install, both MCP config files, and the checks
that confirm the result.

</details>

---

## The dashboard

`memai-admin` (or `python -m memai.admin`) serves the store at
`http://127.0.0.1:8888` — loopback only; `--host` / `--port` /
`MEMAI_ADMIN_PORT` to change. It is where memories are read, edited, triaged and
curated by a person, and where the diagrams are arranged.

`memai-admin --status` says where it is, `memai-admin --stop` stops it.

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

`$MEMAI_HOME/memai.db` if `MEMAI_HOME` is set, otherwise `~/.memai/memai.db`,
with `backups/`, `renders/` and `warden/` beside it. Not tracked in git — user
data, created on first run.

---

## Documentation

How the parts work lives in the [wiki](../../wiki):

| page | what it covers |
|---|---|
| [Storage](../../wiki/Storage) | the tables, and what each one is the record of |
| [Retrieval](../../wiki/Retrieval) | BM25 keyword search, and what a result carries |
| [Domains](../../wiki/Domains) | paths, subtree reads, and belonging to more than one |
| [Diagrams](../../wiki/Diagrams) | documenting a routine as a graph, and reading it back |
| [Curation](../../wiki/Curation) | confidence, decay, staged suggestions, dedup |
| [Tools](../../wiki/Tools) | every MCP tool, and the sets that trim the schema cost |
| [Hooks and the warden](../../wiki/Hooks-and-the-warden) | the four hook events, the skills, and the subagent that consults the store on a session's behalf |
| [Dashboard](../../wiki/Dashboard) | every view, and export/import |

## Licence

MemAI is MIT. Roboto is bundled in `webui/fonts/` under the SIL Open Font
License 1.1 (`webui/fonts/OFL.txt`), separate from MemAI's own licence.
