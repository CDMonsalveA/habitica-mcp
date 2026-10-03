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

## When the live contract tests fail

1. Open the workflow run and find the failing assertion (missing field, new error, changed status).
2. Check recent releases of [HabitRPG/habitica](https://github.com/HabitRPG/habitica/releases) and the
   relevant controller under `website/server/controllers/api-v3/`.
3. Update `src/habitica_mcp/gateway.py` or the projections, then mirror the new behavior in
   `tests/fake_habitica/server.py` and add a regression test.
4. Release a patch version.

## Releasing

Releases are automated with release-please:

1. Merge PRs with Conventional Commit titles into `main`.
2. release-please opens/updates a release PR with the version bump and changelog. Merging it:
   creates the GitHub release, publishes to PyPI with trusted publishing, then publishes `server.json`
   to the MCP Registry (GitHub OIDC).

One-time setup (done by the maintainer):

- PyPI: create the project `habitica-mcp` and add a trusted publisher for
  `CDMonsalveA/habitica-mcp`, workflow `release.yml`, environment `pypi`.
- GitHub: create the `pypi` environment; add secrets `HABITICA_TEST_USER_ID`,
  `HABITICA_TEST_API_TOKEN` (test account) and, for evals, `CLAUDE_CODE_OAUTH_TOKEN`.
- Set `MAINTAINER_USER_ID` in `src/habitica_mcp/const.py` to the maintainer's Habitica User ID so the
  `x-client` header identifies the project, as Habitica's guidelines ask.
- Enable private vulnerability reporting, branch protection on `main` (required CI checks) and
  GitHub's default code scanning.

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
