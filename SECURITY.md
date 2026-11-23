# Security policy

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting
("Security" tab > "Report a vulnerability") on
[CDMonsalveA/habitica-mcp](https://github.com/CDMonsalveA/habitica-mcp). You should receive an answer
within 7 days.

If you accidentally published a Habitica API token (for example in an issue or a config file),
reset it immediately in Habitica: **Settings > Site Data > API Token > Reset**.

## Scope and design

- The server runs locally over stdio. Credentials come from environment variables, are only sent to
  the configured Habitica instance, and are never logged or included in tool results or metrics.
- Unexpected exceptions are masked before reaching the model; only curated messages are returned.
- Task text, notes and checklist items are user data. The server instructions tell models not to
  follow instructions found in them, and destructive actions require `confirm=true`, but no MCP
  server can fully prevent a client model from being manipulated. Use `HABITICA_MCP_MODE=read-only`
  when you only need reads.
- Personal data: Habitica advises against storing sensitive information in tasks (they are not
  end-to-end encrypted and challenge or party tasks are shared). Every write is scanned before it
  is sent; phone numbers, emails, card / ID / account numbers, IBANs, passwords, API keys and
  links with access keys are refused by default (`HABITICA_MCP_PRIVACY=block`, or `redact`).
  Detection uses patterns, so it reduces risk rather than guaranteeing anonymity; the model is
  also instructed to write tasks in general terms.
- Supported versions: the latest release.
