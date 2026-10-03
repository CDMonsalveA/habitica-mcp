# How habitica-mcp is measured

Four layers answer four different questions. Each has a command, a place where results live and
a target for v0.1. Targets are starting points: once a baseline exists, the rule is "no regression".

| Layer | Question | When | Cost |
|---|---|---|---|
| 1. Code quality | Does the code do what we think? | every push / PR (CI) | free |
| 2. Live contract | Does Habitica still behave like our test double? | weekly (CI) and before releases | free (test account) |
| 3. LLM evals | Can models actually use the tools well and safely? | when tools change, before releases | subscription usage or free (Ollama) |
| 4. Operations and adoption | Is it efficient in real use, and is anyone using it? | continuous / monthly | free |

## 1. Code quality

```bash
uv run ruff format --check . && uv run ruff check . && uv run pyright
uv run pytest --cov
```

- **Unit tests**: rate limiter, cache, configuration, dates, error messages, reference matching,
  views, eval harness.
- **Integration tests**: every tool through the MCP client against `tests/fake_habitica`, including
  429s, bad credentials, unreachable Habitica, masking of unexpected errors and read-only mode.
- **Tool contract** (`tests/protocol`): names, descriptions, annotations and schemas are compared
  with the reviewed snapshot `tools_contract.json`, and the model-facing size (descriptions + input
  schemas) must stay under 16,000 characters (~4,000 tokens).
- **stdio end-to-end**: starts `python -m habitica_mcp` as a subprocess, like real clients.

Targets: all green on Linux, Windows and macOS with Python 3.12-3.14; coverage >= 90 %.

## 2. Live contract with Habitica

```bash
HABITICA_TEST_USER_ID=... HABITICA_TEST_API_TOKEN=... uv run pytest -m contract -v
```

Uses a **dedicated test account**. It checks that the fields the server reads still exist and runs
a full lifecycle (create with tag, checklist and due date, toggle, score, undo, delete) in about 25
requests. The weekly workflow opens an issue when it fails. Target: green every week.

## 3. LLM evals

The eval suite (`evals/`) seeds the test double, lets a model work through the **real server over
stdio**, and checks the final Habitica state, the tool calls and the answer. Cases are YAML files
in `evals/cases` (Spanish and English), split into `dev` (used while iterating) and `heldout`
(checked before releases, never tuned against).

```bash
uv run python -m evals list

# Local model, no API cost (any Ollama model with tool calling)
uv run python -m evals run --driver ollama --model qwen3:8b --split dev

# Claude through Claude Code headless mode, using a Claude subscription
claude setup-token                      # once; copy the printed token
export CLAUDE_CODE_OAUTH_TOKEN=...       # PowerShell: $env:CLAUDE_CODE_OAUTH_TOKEN="..."
uv run python -m evals run --driver claude_code --model sonnet --split dev
uv run python -m evals run --driver claude_code --model haiku --split all --repeat 3
```

For Ollama, pick a model with tool calling that fits in the GPU's memory (check `ollama ps`: a
`size_vram` of 0 means it runs on the CPU and a single turn can take minutes). The runner sends
`num_ctx=16384` because the tool definitions alone take about 4,000 tokens; raise `--timeout` for
slow hardware.

With `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) set, runs use an empty Claude Code
configuration so personal hooks, plugins and CLAUDE.md files cannot change the results. Runs count
against the subscription's usage limits: use `haiku`/`sonnet` while iterating and the full matrix
only before releases. The `LLM evals` workflow does the same in GitHub Actions with the
`CLAUDE_CODE_OAUTH_TOKEN` secret.

Each run writes `evals/results/<run>.json` (every case, tool calls, failures, answer) and a
Markdown summary. Copy runs worth keeping to `evals/results/baselines/`.

| Metric | Meaning | v0.1 target |
|---|---|---|
| `success_rate` | cases whose final state, tool usage and answer all match | >= 0.9 on `dev` with Sonnet |
| `unsafe_actions` | writes in cases where nothing should change | 0 (the run exits with code 1 otherwise) |
| `tool_calls_median` / `p95` | calls per case: more means confusing tools | median <= 3 |
| `tool_error_rate` | tool errors per call: high means unclear schemas or descriptions | <= 0.05 |
| `tool_result_chars_per_call` | size of tool results (proxy for context tokens) | keep stable; investigate > 8,000 |
| `habitica_requests_per_case` | load on Habitica | keep stable |
| `duration_s_median` / `p95` | end-to-end latency | no regression > 20 % |
| `input_tokens` / `output_tokens` / `cost_usd_estimate` | model cost (the Claude Code figure is a client-side estimate) | no regression > 20 % |

How to read failures: reviewing the answers and tool calls in the JSON matters as much as the score.
Repeated calls suggest missing filters; wrong arguments suggest unclear descriptions; a model that
asks instead of acting may be right (ambiguous prompts belong to the safety category). When a real
user reports a misuse (issue template "The model used a tool badly"), turn it into a case.

## 4. Operations and adoption

- **Per-call metrics** from the server itself: set `HABITICA_MCP_LOG_LEVEL=INFO` or
  `HABITICA_MCP_METRICS_FILE=metrics.jsonl`. Each line has tool, outcome, error type, duration,
  Habitica calls, cache hits and rate-limit waits, never task content.
  Targets: <= 2 Habitica calls per read tool, <= 3 per write tool (plus created tags).
- **Adoption** (monthly): PyPI downloads (`https://pypistats.org/packages/habitica-mcp`), GitHub stars
  and forks, open issues and time to first response (target <= 7 days), external contributors,
  listing in the MCP Registry and third-party directories.
