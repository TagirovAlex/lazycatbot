# Security policy

## Secrets

This repository must never contain Lichess tokens, GitHub tokens, API keys or
private credentials.

- `lichess-bot/config.yml` is git-ignored.
- `.env` and `*.token` are git-ignored.
- Use [`lichess/config.yml.example`](lichess/config.yml.example) as a template.
- A Lichess bot token should have only the `bot:play` scope.

If a token was committed or posted publicly:

1. Revoke it in the Lichess account settings immediately.
2. Create a new token with only the `bot:play` scope.
3. Update the local `lichess-bot/config.yml` and restart the bot.
4. If it was committed, remove it from git history before publishing again.

## Reporting a vulnerability

Please report security issues privately through GitHub Security Advisories on
this repository. Do not open a public issue for a vulnerability that could put
users or running bots at risk.

## Scope

The engine runs as a local UCI process. The dashboard binds to `127.0.0.1` by
default and keeps the Lichess token server-side. If you expose the dashboard or
the bot process to a network, review the configuration and use your own access
controls.
