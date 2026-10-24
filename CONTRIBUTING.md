# Contributing

Thanks for helping! Issues and pull requests are welcome in English or Spanish.

## Set up

```bash
uv sync
uv run pre-commit install           # once: checks run on every commit, message and push
uv run pre-commit run --all-files   # everything, now
```

The hooks block a commit when something breaks the rules:

| Hook | Checks |
|---|---|
| file hygiene | LF line endings, no trailing whitespace, valid YAML/TOML/JSON, no large files, no private keys |
| `no-secrets` | no Habitica tokens or API keys (including the values of `HABITICA_API_TOKEN` and similar variables in your environment or `.env`), no `.env` files |
| `uv-lock`, `ruff-format`, `ruff-check`, `pyright` | lockfile in sync, formatting, lint, types (strict for `src/`) |
| `project-rules` | one version in `__init__.py` and `server.json`, registry entry matches the package, every tool documented in both READMEs, valid eval cases, GitHub Actions pinned to commit SHAs |
| `fast-tests` | unit tests and the MCP tool contract |
| `zizmor` | GitHub Actions security |
| `conventional-commit` | commit messages like `feat(tools): ...` (they drive versions and the changelog) |
| `full-tests` (pre-push) | the whole suite, including the stdio end-to-end test |

The same checks run in CI, so a pull request cannot skip them. Tests never touch the real
Habitica: they use the in-memory test double in `tests/fake_habitica`.

## Ground rules

- **Habitica's API guidelines are non-negotiable**: keep the `x-client` header, the rate limiter and
  the "never run cron" rule. Tools that spend gold/gems or destroy data need `confirm=true`.
- **Tools are an API for models.** Changing a tool name, parameter or description changes how models
  behave. The contract test (`tests/protocol`) fails on any change; if the change is intended run
  `UPDATE_CONTRACT=1 uv run pytest tests/protocol`, commit the updated `tools_contract.json` and
  explain the change in `CHANGELOG.md`. Prefer adding over renaming; deprecate for at least one minor
  version before removing.
- **Measure tool changes.** For changes to tool descriptions or results, run the evals before and
  after (see [docs/measurement.md](docs/measurement.md)) and paste the summary in the PR.
- Keep responses small: every token returned to the model costs context.
- Coverage must stay at or above 90 %.

## Adding a tool

1. Write the logic as a plain async function taking the gateway (see `src/habitica_mcp/tools`).
2. Register it with a clear description, accurate annotations and the `read` or `write` tag.
3. If Habitica needs a new endpoint, add it to `gateway.py` and to the test double, checking the
   behavior in [HabitRPG/habitica](https://github.com/HabitRPG/habitica) (`website/server/controllers`).
4. Add integration tests through the MCP client, update the contract snapshot and the README.
5. Consider adding eval cases that exercise the new tool.

## Commits and releases

Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`...). Releases
are cut from `main` by a maintainer following [docs/maintenance.md](docs/maintenance.md).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
