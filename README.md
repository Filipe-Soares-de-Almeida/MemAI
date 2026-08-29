# MemAI

Long-term memory for agentic coding, built for Claude Code. Agents call its MCP
tools to write memories — facts, decisions, checkpoints, pitfalls, documented
flows — during a session and read them back in later ones, which is the state an
MCP server's own process does not keep between conversations.

The tools answer any MCP host. What surrounds them targets Claude Code: the hook
events that put the store in front of a session, the bundled skills, and the
warden subagent that consults it on a session's behalf.

One SQLite file holds all of it: rows, keyword index, vectors, edit history,
relations, diagrams. A local admin dashboard serves the same store as the human
curation surface.

## Setup

An agent installing MemAI on a new machine follows
[.agents/install.md](.agents/install.md): requirements, the order that keeps a
running server from breaking the install, both MCP config files, and the checks
that confirm the result. What follows here is the short version.

```sh
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # or .venv/bin/pip on non-Windows
pytest
```

On Windows, `install.bat` does the venv + install steps and `run-admin.bat`
starts the dashboard (both activate `.venv` themselves; extra arguments pass
through, e.g. `run-admin.bat --port 8890`). `stop-mcp.bat` and
`stop-admin.bat` stop what is running: Windows locks an .exe while a
process is running it, so a `pip install` cannot rewrite `.venv\Scripts` until
every MCP server and the dashboard are down.

Register it as an MCP server. **Two hosts, two different files.** Neither reads
the other's, so a server registered in one is invisible to the other, and an
empty list in one says nothing about the other:

| host | the file it reads | how to write it |
|---|---|---|
| Claude Code — CLI and desktop UI alike | `~/.claude.json`, top-level `mcpServers` | `claude mcp add --scope user memai <command>` |
| Claude Desktop — the chat app | Windows: `%APPDATA%\Claude\claude_desktop_config.json`<br>macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` | edit the file, or Settings → Developer → Edit configuration |

Both take the same block, pointing at the console script the install put in the
environment:

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

Then let it reach a session by itself — the hooks put the store in front of an
agent that did not ask for it, and the bundled skills and subagent teach it what
to do with them:

```sh
memai-hook install            # the four hook events
memai-hook install --skills   # the bundled skills
memai-hook install --agents   # the warden subagent
memai-hook install --check    # what is registered, and what is out of date
```

See [Hooks and the warden](../../wiki/Hooks-and-the-warden) for what each event
emits and how to turn the warden off.

## The dashboard

`memai-admin` (or `python -m memai.admin`) serves the store at
`http://127.0.0.1:8888` — loopback only; `--host` / `--port` /
`MEMAI_ADMIN_PORT` to change. It is where memories are read, edited, triaged and
curated by a person, and where the diagrams are arranged.

`memai-admin --status` says where it is, `memai-admin --stop` stops it.

The MCP server can bring it up with the session instead. Off unless asked:

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
placeholder — drop the line to keep the store at `~/.memai`, and on Windows
mind that JSON wants its backslashes doubled.

A host starts several MCP servers per session, so the dashboard is started
once and shared: each server asks `/api/ping` whether one already answers
before trying to bind, and the one that wins keeps the port. It is detached on
purpose, so it outlives the session that opened it.

## Data location

`$MEMAI_HOME/memai.db` if `MEMAI_HOME` is set, otherwise `~/.memai/memai.db`,
with `backups/`, `renders/` and `warden/` beside it. Not tracked in git — user
data, created on first run.

## Documentation

How the parts work lives in the [wiki](../../wiki):

| page | what it covers |
|---|---|
| [Storage and embeddings](../../wiki/Storage-and-embeddings) | the tables, and the bundled model behind the vectors |
| [Retrieval](../../wiki/Retrieval) | hybrid BM25 + vector search, what a result carries, how to re-measure it |
| [Domains](../../wiki/Domains) | paths, subtree reads, and belonging to more than one |
| [Diagrams](../../wiki/Diagrams) | documenting a routine as a graph, and reading it back |
| [Curation](../../wiki/Curation) | confidence, decay, staged suggestions, dedup |
| [Tools](../../wiki/Tools) | every MCP tool, and the sets that trim the schema cost |
| [Hooks and the warden](../../wiki/Hooks-and-the-warden) | the four hook events, the skills, and the subagent that consults the store on a session's behalf |
| [Dashboard](../../wiki/Dashboard) | every view, and export/import |

## Licence

MemAI is MIT. Roboto is bundled in `webui/fonts/` under the SIL Open Font
License 1.1 (`webui/fonts/OFL.txt`), separate from MemAI's own licence.
