# habitica-mcp

An [MCP](https://modelcontextprotocol.io) server that lets LLMs (Claude, Cursor, VS Code Copilot,
local models...) read and manage your [Habitica](https://habitica.com) tasks: habits, dailies,
to-dos, rewards, checklists and tags. Written in Python with [FastMCP](https://gofastmcp.com).

[Leer en español](README.es.md)

<!-- mcp-name: io.github.CDMonsalveA/habitica-mcp -->

> Status: **alpha (v0.1)**. Not affiliated with HabitRPG, Inc.

## Why another Habitica MCP server?

Before starting this project we reviewed the existing ones (September 2026). The most popular
server stopped being maintained in 2025 and no longer works because it does not send the
`x-client` header Habitica now requires; the Python alternatives were single-file experiments.
This server focuses on being **correct, safe and maintainable**:

- **Follows Habitica's API rules**: always sends `x-client`, stays under the 30 requests/minute
  limit (and obeys `Retry-After` / `X-RateLimit-*`), never runs the day rollover (cron).
- **Safe by default**: deleting, removing checklist items or tags and buying rewards return a
  preview until the model calls again with `confirm=true`. A `read-only` mode hides every write tool.
- **Designed for LLMs**: few workflow-level tools, references by id *or* text ("mark meditation as
  done"), compact responses without Habitica's bulky history, and error messages that say what to do.
- **Tested and measured**: an in-memory Habitica test double, a snapshot of the tool contract, live
  contract tests and LLM evals (see [docs/measurement.md](docs/measurement.md)).

## Tools

| Tool | What it does | Changes Habitica |
|---|---|---|
| `habitica_get_overview` | Stats, dailies due today, overdue/today/upcoming to-dos, habits, warnings | no |
| `habitica_list_tasks` | Filter by type, status (pending, due today, overdue, completed), tag, text | no |
| `habitica_get_task` | Full details of one task (checklist, schedule, dates) | no |
| `habitica_list_tags` | Tags and how many tasks use them | no |
| `habitica_create_task` | Create a habit, daily (with schedule), to-do (due date, checklist) or reward | yes |
| `habitica_update_task` | Change text, notes, difficulty, due date, schedule, tags... | yes |
| `habitica_score_task` | Check off / undo dailies and to-dos, +/- habits, buy rewards (`confirm`) | yes |
| `habitica_manage_checklist` | Add, rename, toggle or remove (`confirm`) checklist items | yes |
| `habitica_manage_tags` | Create, rename or delete (`confirm`) tags | yes |
| `habitica_delete_task` | Delete a task (`confirm`) | yes |

Not exposed on purpose: cron, gems and payments, account resets, chat and party messages.

## Quick start

1. Install [uv](https://docs.astral.sh/uv/).
2. In Habitica go to **Settings > Site Data** and copy your **User ID** and **API Token**.
3. Check the connection:

   ```bash
   HABITICA_USER_ID=... HABITICA_API_TOKEN=... uvx habitica-mcp --check
   ```

   Until the first PyPI release, replace `uvx habitica-mcp` with
   `uvx --from git+https://github.com/CDMonsalveA/habitica-mcp habitica-mcp`.

### Claude Desktop / Cursor

Add to `claude_desktop_config.json` (Claude Desktop) or `~/.cursor/mcp.json` (Cursor):

```json
{
  "mcpServers": {
    "habitica": {
      "command": "uvx",
      "args": ["habitica-mcp"],
      "env": {
        "HABITICA_USER_ID": "your-user-id",
        "HABITICA_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add habitica -e HABITICA_USER_ID=your-user-id -e HABITICA_API_TOKEN=your-api-token -- uvx habitica-mcp
```

### VS Code

`.vscode/mcp.json` (the token is asked for once and never stored in the repository):

```json
{
  "inputs": [
    { "type": "promptString", "id": "habitica-token", "description": "Habitica API token", "password": true }
  ],
  "servers": {
    "habitica": {
      "type": "stdio",
      "command": "uvx",
      "args": ["habitica-mcp"],
      "env": {
        "HABITICA_USER_ID": "your-user-id",
        "HABITICA_API_TOKEN": "${input:habitica-token}"
      }
    }
  }
}
```

Then ask things like *"What do I still have to do today in Habitica?"*, *"Add a to-do to call the
dentist on Friday, hard difficulty, with a checklist: find the number, ask about cleaning"* or
*"I meditated, check it off"*.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `HABITICA_USER_ID` | — | Your Habitica User ID (required to call Habitica) |
| `HABITICA_API_TOKEN` | — | Your Habitica API Token (required, never logged) |
| `HABITICA_MCP_MODE` | `standard` | `read-only` exposes only the four read tools |
| `HABITICA_BASE_URL` | `https://habitica.com` | Another Habitica instance (self-hosted or a test double) |
| `HABITICA_X_CLIENT` | `<maintainer id>-habitica-mcp` | Override the `x-client` header (for forks) |
| `HABITICA_MCP_RATE_LIMIT` | `25` | Max Habitica requests per minute (Habitica allows 30) |
| `HABITICA_MCP_MAX_WAIT` | `10` | Seconds to wait for a free request slot before failing fast |
| `HABITICA_MCP_CACHE_TTL` | `15` | Seconds reads are reused between tool calls (`0` disables) |
| `HABITICA_MCP_TIMEOUT` | `20` | Request timeout in seconds |
| `HABITICA_MCP_LOG_LEVEL` | `WARNING` | Logs go to stderr; `INFO` adds one metrics line per tool call |
| `HABITICA_MCP_METRICS_FILE` | — | Append per-call metrics (no task content) as JSON lines |

Credentials stay on your machine: the server runs locally over stdio and only talks to Habitica.

## Development

```bash
uv sync                      # install dependencies
uv run pre-commit install    # checks before every commit (see CONTRIBUTING.md)
uv run pytest                # unit, integration, protocol and stdio end-to-end tests
uv run ruff check . && uv run pyright
uv run python -m tests.fake_habitica --port 8765   # the Habitica test double, standalone
uv run pytest -m contract    # live tests (needs HABITICA_TEST_USER_ID / HABITICA_TEST_API_TOKEN)
uv run python -m evals run --driver ollama --model qwen3:8b          # LLM evals, local model
uv run python -m evals run --driver claude_code --model sonnet       # LLM evals, Claude subscription
```

- Architecture decisions: [docs/adr](docs/adr)
- How the project is measured: [docs/measurement.md](docs/measurement.md)
- Maintenance runbook: [docs/maintenance.md](docs/maintenance.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) · Security: [SECURITY.md](SECURITY.md)

Built on [habiticalib](https://github.com/tr4nt0r/habiticalib) models (also used by Home Assistant).

## License

[MIT](LICENSE)
