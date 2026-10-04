# Installing MemAI on a new machine

For an agent doing the install. Read the whole file before the first command:
the rule below governs every install, and step 4 has two targets, not one.

## The rule that comes before everything

**Never run `pip install` into an environment whose MCP server is running.**

Windows locks an `.exe` while a process runs it. A host that has MemAI
registered holds `\.venv\Scripts\memai-mcp.exe` open for the whole session, so
an install that has to rewrite that launcher cannot. What follows is worse than
a clean failure: pip removes and rewrites files in stages, so the environment
is left half-updated — a truncated launcher, a package whose metadata says one
version and whose files are another. Every host that starts a server after that
fails to start one, and the store is unreachable until the environment is
repaired with everything stopped.

Before any `pip install` into that environment:

```bat
stop-mcp.bat
stop-admin.bat
```

Then close every host that could spawn a fresh server while you work — Claude
Desktop, open Claude Code sessions — because a host does not restart a server
it lost, but it does start a new one for the next session. Install, then reopen
the hosts.

## 1. Requirements

- Python 3.12 to 3.14 on PATH (`py -3 --version` on Windows, `python3 --version`
  elsewhere)
- Node 20.19 or newer on PATH (`node --version`) — the admin dashboard is a
  Vite build, and `memai.admin` serves that build. `.npmrc` sets
  `engine-strict`, so `npm ci` refuses an older one by name
- git

## 2. Clone and install

```sh
git clone https://github.com/Filipe-Soares-de-Almeida/MemAI.git
cd MemAI
```

On Windows, `install.bat` creates `.venv`, installs every dependency from
`requirements-dev.txt`, installs the package editable on top, and builds the
dashboard. Everywhere else:

```sh
python -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.txt
.venv/bin/pip install --no-deps -e .
npm ci
npm run build
```

`npm run build` writes `src/memai/webui/dist/`, which is what the dashboard
serves. Without it every page answers 503 naming the command to run.

`memai-admin` checks that build each time it starts, after it has the port:
when a file under `src/memai/webui/`, `package.json`, `package-lock.json` or
`vite.config.js` is newer than `dist/index.html`, it runs `npm run build`, and
`npm ci` first when the lockfile is newer than `node_modules`. Without npm on
the PATH, or when npm fails, it logs one line and serves the build it has.
`MEMAI_ADMIN_BUILD=0` turns the check off.

To iterate on the dashboard, `npm run dev` serves it with hot reload and proxies
`/api` and `/fonts.css` to a `memai-admin` on `MEMAI_ADMIN_PORT` (8888 by
default), which keeps the browser same-origin with the API.

`requirements.txt` (runtime) and `requirements-dev.txt` (runtime and the
`dev` extra) pin every package, direct and transitive, with hashes, for Windows
and Linux alike, so a fresh install resolves the set CI tested. After changing
a dependency in `pyproject.toml`, regenerate both with uv (a maintainer tool;
installing MemAI never needs it) and commit them with the change:

```sh
uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --annotation-style line -o requirements.txt
uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 --generate-hashes --annotation-style line -o requirements-dev.txt
```

`tests/test_lock.py` fails while a lock disagrees with `pyproject.toml`.

Dependabot (`.github/dependabot.yml`) opens weekly pull requests into `dev`
that refresh the locks, `package-lock.json` and the workflow actions. The
`Dependency canary` workflow runs every Monday, and on demand, against the
newest releases inside the ranges, ignoring both locks: a red canary names an
upstream release that would break the next lock refresh.

`requires-python` carries a ceiling one minor above the newest Python CI
tests; `tests/test_ci.py` fails when the two disagree, so adding a Python to
the CI matrix and raising the ceiling go together.

The checkout is the install. Do not also install MemAI into the system or user
site-packages: two copies of the same package on one machine make it impossible
to tell which one a failing server is running.

## 3. Verify the install before registering anything

```sh
.venv/Scripts/python -m pytest -q     # .venv/bin/python off Windows
```

The suite is self-contained and must pass before the server is wired into a
host. A failure here is an install problem, not a configuration problem.

## 4. Register the MCP server — two hosts, two different files

The Claude Code CLI and the Claude desktop app read **different files**. The
desktop app's Code tab runs Claude Code, and still reads the desktop app's
file, not `~/.claude.json`. Neither host reads the other's: a server registered
in one does not appear in the other, and an empty list in one says nothing
about the other.

| host | the file it reads |
|---|---|
| Claude Code CLI — `claude` in a terminal | `~/.claude.json`, top-level `mcpServers` |
| Claude desktop app — Chat and the Code tab | Windows: `%APPDATA%\Claude\claude_desktop_config.json`<br>macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |

Register in whichever the user actually uses; register in both if they use
both. Do not report the install as done after writing only one, and do not
infer from one file's contents what the other holds — open the file.

**Name the server `memai`.** A host builds its tool names out of it —
`mcp__memai__search` — and the bundled warden's tool list matches those names
literally, so a server registered under another name leaves the warden running
with no store to search.

For the Claude Code CLI, `claude mcp add` writes the entry:

```sh
claude mcp add --scope user memai "<absolute path to memai-mcp>"
```

For the desktop app, edit its file directly and restart the app.

**Use the absolute path to the executable in the venv.** A bare `memai-mcp`
resolves only if that venv's `Scripts\` (`bin/` off Windows) is on the PATH of
the process launching the server, and a GUI app inherits the desktop session's
PATH, not the shell's.

**Every environment variable MemAI reads goes in the `env` block of that
entry.** A server the host launches sees that environment and no other — not
the shell's, not the user's. The variables are `MEMAI_HOME`,
`MEMAI_ADMIN_AUTOSTART`, `MEMAI_ADMIN_BUILD`, `MEMAI_ADMIN_HOST`,
`MEMAI_ADMIN_PORT` and `MEMAI_TOOLS`; all have defaults, so set only what the
user wants to differ.

`MEMAI_UPDATE_CHECK` is the one variable that does not belong in that block: it
gates the release check, which a hook process or the dashboard makes and the
server only reads the cached answer of. Turning it off means setting it in the environment
the host itself runs in.

```json
{
  "mcpServers": {
    "memai": {
      "command": "C:\\path\\to\\MemAI\\.venv\\Scripts\\memai-mcp.exe",
      "args": [],
      "env": {
        "MEMAI_HOME": "C:\\path\\to\\store",
        "MEMAI_ADMIN_AUTOSTART": "1",
        "MEMAI_ADMIN_PORT": "8888"
      }
    }
  }
}
```

Back up a config file before editing it, and preserve every key already in it —
these files hold host settings that are not yours to drop. The desktop app also
rewrites its config when it exits, so an edit made while it is running can be
reverted: close it first, then edit, then reopen.

## 5. Hooks, skills, the warden

```sh
memai-hook install            # the four hook events
memai-hook install --skills   # the bundled skills
memai-hook install --agents   # the warden subagent
```

These write into the user's `~/.claude` directory (`settings.json`, `skills/`,
`agents/`) and back up what they replace. A SessionStart hook fires for the
next session, not the running one.

## 6. Verify end to end

- `claude mcp list` → the memai row says connected, with the venv path
- desktop app → Settings → Developer → local MCP servers → memai, running
- `memai-hook --check` → four hooks registered, six skills and the warden
  installed, nothing marked outdated
- the store file exists at `MEMAI_HOME` (`~/.memai` by default), holding
  `memai.db` and `warden/`

Confirm a server is live by what the host reports, never by the presence of a
line in a config file.

## 7. Updating an existing install

The install is editable: the checkout is what runs, so a `git pull` already
changes what the next server process imports. Which of the two updates below
it is depends on whether the environment moved with it.

**Source only** — the common case, and no `pip install` in it. A pull that
leaves `pyproject.toml` and the two lock files alone is this one:

1. `git pull`
2. `memai-hook install --check`, and reinstall the skills or agents it reports
   as outdated — file copies into `~/.claude`, which no server holds open
3. `stop-admin.bat`, then restart the hosts

A process keeps the code it imported at startup, and there are two kinds of
them. Restarting a host replaces its server; the dashboard is its own process
and outlives that, so it has to be stopped by name. Leaving it up is not just a
stale screen — it opens the store and WRITES it with the code it holds, so a
schema change the newer code applies is undone by the older one. The next
server autostarts a fresh dashboard from the checkout. A host also reads its
agents and skills once, when it starts.

**The environment moved** — a dependency added, removed or repinned, a new
entry under `[project.scripts]`, or a different Python under the venv. This is
what `pip install` is for, and the rule at the top of this file applies to it:

1. `stop-mcp.bat` and `stop-admin.bat`; close the hosts
2. `update.bat`, which pulls and runs `install.bat` (elsewhere: `git pull`,
   then the pip line for the platform)
3. `memai-hook install --check`, and reinstall what it reports as outdated
4. reopen the hosts

`install.bat` and `update.bat` run `tools/install-guard.py` before they touch
`.venv`. It refuses while a `memai-mcp.exe` runs from this checkout's `.venv`,
or while a MemAI dashboard serving this checkout answers on `MEMAI_ADMIN_PORT`
or on the port in `MEMAI_HOME/admin.json`; a server or dashboard of another
checkout does not count. From a console it lists what is open and offers to
end those processes.

## 8. Cutting a release

`.github/workflows/release-please.yml` runs on every push to `dev` and keeps
one release pull request open, updating it as commits land. Merging that
pull request tags the commit, publishes the release, and fast-forwards
`main` to it.

The workflow needs a fine-grained personal access token in the repository
secret `RELEASE_PLEASE_TOKEN`, scoped to this repository with Contents:
write and Pull requests: write.

**The token is a PAT and not `GITHUB_TOKEN`.** A pull request opened under
the built-in token triggers no further workflow runs, so the release pull
request would never get a CI run — a branch rule on `dev` that requires one
would leave it permanently unmergeable. Keep the PAT even while debugging a
failing run; swapping in `github.token` reintroduces exactly that deadlock.

## Failure modes

| symptom | cause | fix |
|---|---|---|
| pip errors on `memai-mcp.exe`, "access is denied" or "used by another process" | a host is running a server from this venv | stop the servers and hosts, run the install again |
| the server fails to start after an install that reported success | the launcher was rewritten while locked, leaving the environment half-updated | stop everything, reinstall into the same venv, restart the hosts |
| host lists no MCP servers although the config has one | wrong file for that host, or the host was not restarted after the edit | check the file that host actually reads, then restart it |
| a pulled change does not show up in a session | the server that session runs was started before the pull | restart the host; the checkout is editable, so nothing else is needed |
| a schema change reappears in the store after the servers were restarted | the dashboard is older than the servers and rebuilds what they removed | `stop-admin.bat`; the next server autostarts one from the checkout |
| server starts but the store is empty or in the wrong place | `MEMAI_HOME` set in a shell instead of the entry's `env` block | move it into the config entry |
