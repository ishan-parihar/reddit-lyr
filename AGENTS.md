# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

- Use `uv` for dependency management: `uv sync` (dev: `uv sync --extra dev`)
- Lint: `uv run ruff check .` (auto-fix with `--fix`)
- Format: `uv run ruff format .`
- Tests: `uv run pytest`
- Run the CLI locally: `uv run reddit-lyr --status`
- Run server locally: `uv run -m reddit_mcp_server`

*(Commands reviewed 2026-10-19: uv, ruff and pytest are still the toolchain, so the list stands.)*

## Tool Return Format

All scraping tools return structured results with `url`, `sections: {name: raw_text}`, and optional `references`, `section_errors`, `unknown_sections`, and `post_ids` keys.

## Verifying Bug Reports

Always verify scraping bugs end-to-end against live Reddit, not just code analysis. Use `uv run`, not `uvx`, so the running process reflects your workspace. Assume a valid login profile exists; `--status` verifies the session.

## Release Process

There is **no CI release workflow** in this repo. Installs are source-based: `install.sh` clones `main` and installs from the local tree, so **pushing to `main` is the release**.

```bash
git checkout main && git pull
uv version --bump patch          # update version in pyproject.toml AND uv.lock
uv run ruff check . && uv run pytest   # verify before release
# commit + push to main (or via a PR, then merge)
```

## Commit Messages

- Follow conventional commits: `type(scope): subject`
- Types: feat, fix, docs, style, refactor, test, chore, perf, ci
- Keep subject <50 chars, imperative mood

## Development Workflow

- Include the model used for code generation in PR descriptions (e.g. "Generated with Claude Opus 4.6")
- Include a short prompt from the user messages that reproduces the PR diff
- When implementing a new feature/fix: branch from `main` as `feature/issue-number-short-description`, implement and test, update README.md if relevant, create a draft PR, review with AI agents first, then manual review. Do not squash commits.
