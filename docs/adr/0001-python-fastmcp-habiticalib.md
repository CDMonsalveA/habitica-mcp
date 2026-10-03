# ADR 0001: A new Python server on FastMCP 4 with habiticalib models

- Status: accepted
- Date: 2026-09-14

## Context

We wanted an MCP server connecting LLMs to Habitica, written in Python. A survey in September 2026
found about fifteen existing attempts:

- The most popular (iBreaker/habitica-mcp-server, JavaScript) was unmaintained since July 2025 and
  broken: it does not send the `x-client` header Habitica enforces, and fixing PRs were ignored.
- The most active (rodcordeiro/habitica-mcp) is TypeScript with a deliberately narrow scope.
- Python projects were single-file experiments without licenses, tests or CI; the `mcp-habitica`
  PyPI package's source repository had been deleted.
- The official MCP Registry listed no Habitica server.

Habitica constraints, verified in HabitRPG/habitica's source: `x-client` is required and a whole
`x-client` value can be blocked; 30 requests per minute per user; cron applies damage and must not be
triggered by tools; task lists include unbounded history unless `history=false` is sent; HTTP 401 is
reused for business rules such as "Not Enough Gold".

## Decision

1. Build a new server in Python rather than contributing to a TypeScript project or reviving the
   abandoned JavaScript one.
2. Use **FastMCP 4** (built on the official `mcp` 2.x SDK, protocol 2026-07-28) for tool
   registration, annotations, tag-based visibility, lifespan, middleware and in-memory testing.
   FastMCP-specific code stays in `server.py`, `metrics.py` and `tools/`; the gateway and projections
   do not depend on it.
3. Talk to Habitica with our own small aiohttp gateway (to send `history=false`, reach checklist and
   tag endpoints, read rate-limit headers on every response and map errors precisely) while parsing
   responses with **habiticalib**'s typed models, the library behind Home Assistant's integration.
   Gaps found in habiticalib's client are candidates for upstream contributions.
4. Expose few workflow-level tools with `confirm=true` for destructive or gold-spending actions, and
   a `read-only` mode.
5. Measure with an in-memory Habitica test double, a tool contract snapshot, weekly live contract
   tests and LLM evals driven by Claude Code (subscription) or Ollama.

## Consequences

- We own the HTTP layer and must follow Habitica API changes (mitigated by live contract tests).
- FastMCP's frequent major releases require deliberate upgrades (pinned `<5`).
- The test double must stay faithful to Habitica; contract tests are the safety net.
- Being first in the MCP Registry and on PyPI under a clear name makes collaboration easier for
  people who find the abandoned servers.
