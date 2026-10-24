# Maintenance runbook

## Routine

| Every | Task |
|---|---|
| PR | CI must be green. Review contract snapshot diffs as API changes. For tool changes, compare eval summaries. |
| Week | Merge grouped Dependabot PRs when CI is green. Check the Monday live contract run. |
| Month | Read release notes of FastMCP, the MCP specification and habiticalib; record adoption metrics (see measurement.md); triage issues. |
| Release | Run evals on `dev` and `heldout` with at least two models; update CHANGELOG. |

## Dependencies

- `fastmcp` is pinned below the next major (`<5`). FastMCP ships majors often: before widening the
  range, read the upgrade guide, run the full suite and the evals.
- `habiticalib` is used for its typed models (`<0.5`). If it adds `history=false` support or
  checklist endpoints, consider using its client directly; contribute such improvements upstream.
- Python support follows CI (3.12-3.14). Drop a version only in a minor release.
- Pre-commit hooks: `uv run pre-commit autoupdate --freeze` updates the pinned hook repositories
  (keep the `# frozen: vX` comments). The `uv==` pin inside `.pre-commit-config.yaml` is updated by
  hand when uv releases a new minor version.

## When the live contract tests fail

1. Open the workflow run and find the failing assertion (missing field, new error, changed status).
2. Check recent releases of [HabitRPG/habitica](https://github.com/HabitRPG/habitica/releases) and the
   relevant controller under `website/server/controllers/api-v3/`.
3. Update `src/habitica_mcp/gateway.py` or the projections, then mirror the new behavior in
   `tests/fake_habitica/server.py` and add a regression test.
4. Release a patch version.

## Releasing

Releases are automated with release-please in manifest mode (`release-please-config.json`):

1. Merge commits with Conventional Commit messages into `main` (the `conventional-commit` hook
   enforces them; `feat` bumps the minor version, `fix` the patch, `!` a breaking change).
2. release-please opens or updates a release PR. It bumps the version in its only source,
   `src/habitica_mcp/__init__.py`, and in `server.json`, and writes the changelog. The version is
   dynamic in `pyproject.toml`, so `uv.lock` never goes stale on a bump.
3. Merging the release PR creates the GitHub release; the `pypi` job waits for your approval in the
   `pypi` environment, publishes with trusted publishing, and then `server.json` is published to the
   MCP Registry (GitHub OIDC). GitHub asks to approve the CI run of release PRs because a bot
   opened them: approve it from the PR's Checks tab.

Repository setup already in place: public repo, secret scanning with push protection, Dependabot
alerts and security updates, CodeQL default setup, private vulnerability reporting, read-only
`GITHUB_TOKEN` by default, only GitHub-owned/verified/allow-listed actions pinned to SHAs, `main`
protected against deletion and force pushes, and the `pypi` environment (deploys only from `main`,
manual approval). `MAINTAINER_USER_ID` in `src/habitica_mcp/const.py` holds the maintainer's
Habitica User ID for the `x-client` header.

Still to do by the maintainer:

- PyPI: add a pending trusted publisher for project `habitica-mcp`, owner `CDMonsalveA`,
  repository `habitica-mcp`, workflow `release.yml`, environment `pypi`.
- Secrets (optional): `HABITICA_TEST_USER_ID` and `HABITICA_TEST_API_TOKEN` from a dedicated test
  account for the weekly contract tests; `CLAUDE_CODE_OAUTH_TOKEN` for the evals workflow.

## Rotating credentials

- Test account token: Habitica > Settings > Site Data > reset API token, then update the GitHub secret.
- `CLAUDE_CODE_OAUTH_TOKEN`: expires after one year. Run `claude setup-token` again and update the secret.
- If a token leaks in an issue or log: reset it first, then delete the content.

## Changing tools safely

Tool names, parameters and result fields are a public API for models and scripts:

- Additive changes (new optional parameter, new result field, new tool): minor version.
- Renames or removals: keep the old name working for at least one minor version, then remove in a
  major version. Note it in CHANGELOG.
- Always update the contract snapshot and run evals before and after.
