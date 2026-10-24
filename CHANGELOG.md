# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Tool names, parameters and result fields are the
public API: removing or renaming them is a breaking change.

## 0.1.0 (2026-10-24)


### Features

* habitica-mcp v0.1 - MCP server for Habitica ([9fb1f46](https://github.com/CDMonsalveA/habitica-mcp/commit/9fb1f46e25a812fd9189575a02ac6bcada0c6873))

## [Unreleased]

### Added
- Ten MCP tools: overview, list/get tasks, list tags, create/update/score/delete tasks, manage
  checklists and tags, with `confirm=true` required for destructive actions and gold purchases.
- `read-only` mode, client-side rate limiting that honors Habitica's headers, short-lived read
  cache, per-call metrics (log line or JSON-lines file), `habitica-mcp --check`.
- Test suite: in-memory Habitica test double, tool contract snapshot, stdio end-to-end test, and
  live contract tests against habitica.com (test account, weekly workflow).
- LLM eval suite (`python -m evals`) with 20 cases in Spanish and English, Claude Code (subscription)
  and Ollama drivers, and success, safety, tool-call, token and latency metrics.
- CI on Linux, Windows and macOS with Python 3.12-3.14, dependency audit, release automation to PyPI
  and the MCP Registry.

### Fixed (found by the live contract tests)
- `needs_cron` was always false: Habitica only computes `needsCron` for the whole user document.
  It is now derived from `lastCron` with Habitica's rule, falling back to the whole document when
  the time zone changed since the last cron.
- `max_hp` and `exp_to_next_level` were missing for the same reason; they are now computed like
  Habitica does (`maxMP` is only shown when Habitica sends it).
