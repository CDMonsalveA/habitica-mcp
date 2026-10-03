## What and why

## Checklist

- [ ] `uv run pytest`, `uv run ruff check .` and `uv run pyright` pass
- [ ] If tools changed: contract snapshot updated (`UPDATE_CONTRACT=1 uv run pytest tests/protocol`) and a `CHANGELOG.md` entry added
- [ ] If tool descriptions or results changed: eval summary before and after pasted below
- [ ] Habitica API rules still respected (`x-client`, rate limit, no cron, `confirm` for destructive actions)

## Eval results (if applicable)
