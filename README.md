<!-- T2I HERO SPEC — Subject: a Reddit data engine — subreddit nodes (orange alien motif) feeding posts, comments, and votes through an HTTPX pipeline into structured JSON for agents; per-subreddit concurrency limiter as a valve. Composition: alien nodes → funnel → JSON output. Palette: reddit orange #ff4500 → deep slate → off-white cards. Style: dark flat vector, upvote-arrow motif, no text. 16:9. -->

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-2b6cb0?style=flat&logo=python&logoColor=white" alt="Python 3.11+">
<img src="https://img.shields.io/badge/LOC-4.4K-informational?style=flat-square" alt="LOC">
<img src="https://img.shields.io/badge/Status-Active-brightgreen" alt="Status">
<a href="https://github.com/ishan-parihar/reddit-lyr/actions/workflows/ci.yml"><img src="https://github.com/ishan-parihar/reddit-lyr/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-059669?style=flat" alt="MIT">
  <img src="https://img.shields.io/badge/mcp-1.0-c026d3?style=flat&logo=modelcontextprotocol&logoColor=white" alt="MCP 1.0">
  <img src="https://img.shields.io/badge/endpoints-50%2B-2563eb?style=flat" alt="50+ endpoints">
</p>

<h1 align="center">reddit-lyr</h1>

<p align="center">
  <strong>An async Reddit API wrapper and MCP server for AI agents</strong> — 50+ typed endpoints, zero PRAW
  dependency, and a 32-tool agent interface for browsing, posting, commenting, voting, moderating, and searching
  through the same session your browser has.
</p>

<p align="center">
  <code>pipx install reddit-lyr</code>
</p>

---

![Reddit-Httpx MCP](https://github.com/ishan-parihar/reddit-lyr/raw/main/assets/readme/reddit-lyr-mcp.png)

---

## What it is

| Layer | Description |
|-------|-------------|
| **Core** | `RedditClient` — async, typed, 50+ endpoints |
| **MCP Server** | 32 tools for agents (browse, post, comment, vote, mod) |
| **CLI** | `reddit-lyr browse`, `reddit-lyr post`, `reddit-lyr inbox` |

**Endpoints:** Frontpage, Subreddits, Posts, Comments, Users, Messages, Moderation, Search, Wiki, Live, Collections, Awards, Polls, Predictions

---

## Quick start

```bash
# Install


pipx install reddit-lyr

# Auth (interactive)
reddit-lyr auth login

# Use as library
from reddit_httpx import RedditClient
client = RedditClient()
posts = await client.browse_subreddit("rust", sort="hot", limit=25)

# MCP server for agents
reddit-lyr mcp
```

## Multiple accounts

reddit-lyr supports any number of named accounts, each with an isolated cookie
store under `~/.reddit-lyr/accounts/<name>/`. The default account (the legacy
top-level `~/.reddit-lyr/cookies.json`) keeps working unchanged for every
existing workflow.

```bash
# List accounts
reddit-lyr --accounts

# Register a new account (import its cookies once)
REDDIT_ACCOUNT=workmode reddit-lyr --cookies-file workmode-cookies.json

# Operate on it — the flag works before any tool name
reddit-lyr --account workmode --status
reddit-lyr --account workmode browse_subreddit rust --limit 5
REDDIT_ACCOUNT=workmode reddit-lyr mcp   # or via env, for MCP/cron contexts

# Logout only that account
reddit-lyr --account workmode --logout
```

Non-interactive contexts (MCP servers, cron jobs) select the account via the
`REDDIT_ACCOUNT` environment variable. The shared Obscura cookie daemon is
automatically bypassed for non-default accounts — it caches one session per
platform and has no account awareness, so it is never allowed to serve a named
account's requests.

---

## MCP Tools (32)

| Category | Tools |
|----------|-------|
| **Browse** | `browse_frontpage`, `browse_popular`, `browse_subreddit`, `get_post`, `get_comments` |
| **Post** | `submit_text`, `submit_link`, `edit_post`, `delete_post` |
| **Comment** | `comment`, `reply`, `edit_comment`, `delete_comment` |
| **Vote** | `vote_post`, `vote_comment` |
| **Message** | `get_inbox`, `send_message`, `mark_read` |
| **User** | `get_profile`, `get_karma`, `get_saved` |
| **Mod** | `mod_approve`, `mod_remove`, `mod_lock`, `mod_sticky` |
| **Search** | `search_posts`, `search_subreddits`, `search_users` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    RedditClient                               │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │  Auth    │  │  Models  │  │  Endpoints│  │  MCP     │     │
│  │  (OAuth) │  │  (Pydantic)│  │  (50+)   │  │  Server  │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
└─────────────────────────────────────────────────────────────┘
                    │
                    ▼
           httpx.AsyncClient
           (connection pool, retry, timeout)
```

---

## Example: Agent browsing Reddit

```python
# Agent uses MCP tools:
# 1. browse_subreddit("MachineLearning", sort="hot", limit=10)
# 2. get_post("t3_abc123") → full post + comments
# 3. search_posts("transformer architecture", subreddit="MachineLearning")
# 4. submit_text("r/MySub", "Title", "Body") — with auth
```

---


## Visual proof

| MCP tools | Comment tree | Post detail |
|:---:|:---:|:---:|
| ![Tools](https://github.com/ishan-parihar/reddit-lyr/raw/main/assets/readme/tools.png) | ![Comments](https://github.com/ishan-parihar/reddit-lyr/raw/main/assets/readme/comments.png) | ![Post](https://github.com/ishan-parihar/reddit-lyr/raw/main/assets/readme/post.png) |

| Search results | Mod tools | Message inbox |
|:---:|:---:|:---:|
| ![Search](https://github.com/ishan-parihar/reddit-lyr/raw/main/assets/readme/search.png) | ![Mod](https://github.com/ishan-parihar/reddit-lyr/raw/main/assets/readme/mod.png) | ![Messages](https://github.com/ishan-parihar/reddit-lyr/raw/main/assets/readme/messages.png) |

## Requirements

- Python 3.11+
- Reddit app credentials (client_id, client_secret)

---

## Limitations

| Capability | Status |
|---|---|
| Submit text/link/comment posts, delete own posts, reply | 🟢 working (verified live) |
| Vote / mod tools / search / posts / comments reads | 🟢 working |
| **`send_message` (private message)** | 🔴 **hard 401 on `/api/compose`** — Reddit `api/compose` rejects every auth scheme tried (modhash param, `X-Modhash` header, Bearer `token_v2`) even though submit/read/delete work with the same session. Endpoint-level auth gating, not a tool bug. |

Notes:

- Cookie resolution order: obscura daemon (port 9999) → local file → browser re-extraction. A dead daemon adds latency before the file fallback is reached. The on-disk cookie file must stay single-wrap `{"cookies": {flat}}`; nested wraps read as empty and block authed tools.

---

## Related CLI Tools

This project is part of a family of agent-friendly CLI tools for social platforms:

| Tool | CLI | Repo |
|------|-----|------|
| Instagram | `instagram-lyr` | [ishan-parihar/instagram-lyr](https://github.com/ishan-parihar/instagram-lyr) |
| Reddit | `reddit-lyr` | [ishan-parihar/reddit-lyr](https://github.com/ishan-parihar/reddit-lyr) |
| LinkedIn | `linkedin-lyr` | [ishan-parihar/linkedin-lyr](https://github.com/ishan-parihar/linkedin-lyr) |
| Twitter/X | `twitter-lyr` | [ishan-parihar/twitter-lyr](https://github.com/ishan-parihar/twitter-lyr) |
| Discord | `discord` | [ishan-parihar/discord-cli](https://github.com/ishan-parihar/discord-cli) |
| Telegram | `tg` | [ishan-parihar/tg-cli](https://github.com/ishan-parihar/tg-cli) |

---

## License

MIT — see [LICENSE](LICENSE).

---

## ☕ Support & Sponsorship

If you find this project useful, consider supporting ongoing development:

[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa?style=flat-square&logo=github)](https://github.com/sponsors/ishan-parihar)
[![Donate](https://img.shields.io/badge/Donate-Razorpay-3395FF?style=flat-square)](https://rzp.io/rzp/ishan-parihar)

Your support funds new features, releases, and infrastructure for the whole ecosystem.