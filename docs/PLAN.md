# reddit-lyr: Implementation Plan

## Overview

A Python MCP/CLI tool for full Reddit account automation using browser cookies + `curl_cffi` to simulate real browser requests. Modeled after `instagram-httpx-mcp` architecture.

**Key difference from instagram-httpx-mcp**: Uses `curl_cffi` instead of `httpx` because Reddit's anti-bot detection (fingerprinting TLS, JA3/JA4 signatures) requires impersonating a real browser at the TLS level — something httpx cannot do.

---

## Planned Architecture

```
reddit-lyr/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── reddit_mcp_server/
│   ├── __init__.py                 # Package metadata
│   ├── __main__.py                 # python -m entry
│   ├── cli_main.py                 # CLI entry: --login, --logout, --status, transport selection
│   ├── server.py                   # FastMCP server + tool registration
│   ├── bootstrap.py                # Session init, env cookie loading
│   ├── authentication.py           # Auth validation, session check
│   ├── session_state.py            # Cookie paths, profile dirs
│   ├── cookie_import.py            # Browser cookie extraction (Brave/Chrome/Firefox/Edge)
│   ├── dependencies.py             # DI: builds RedditAPIClient from cookies
│   ├── constants.py                # Timeouts, Reddit app IDs, user agents
│   ├── sequential_middleware.py    # One-tool-at-a-time execution
│   ├── error_handler.py            # Centralized error handling
│   ├── exceptions.py               # Custom exceptions
│   ├── logging_config.py           # Logging setup
│   ├── scraping/
│   │   ├── __init__.py
│   │   └── api_client.py           # RedditAPIClient — core HTTP via curl_cffi
│   └── tools/
│       ├── __init__.py
│       ├── browse.py               # Subreddit browsing, feed, frontpage
│       ├── search.py               # Search posts, subreddits, users
│       ├── posts.py                # Get post details, comments
│       ├── user.py                 # User profiles, karma, history
│       ├── submit.py               # Create posts (text, link, image, video, poll)
│       ├── comments.py             # Comment, reply, edit, delete comments
│       ├── vote.py                 # Upvote, downvote, unvote
│       ├── messaging.py            # DMs: inbox, send, read, reply
│       ├── subreddit.py            # Subscribe, unsubscribe, get info
│       ├── save.py                 # Save/unsave posts and comments
│       ├── moderation.py           # Basic mod actions (remove, approve, flair)
│       └── account.py              # Account info, preferences, friends
└── tests/
    ├── __init__.py
    ├── test_client.py              # API client unit tests
    ├── test_tools.py               # Tool integration tests
    └── conftest.py                 # Fixtures
```

---

## Phase 1: Core Infrastructure

### 1.1 Project Setup
- `pyproject.toml` with dependencies:
  - `fastmcp>=3.0.0`
  - `curl_cffi>=0.7.0`
  - `python-dotenv>=1.1.1`
  - `inquirer>=3.4.0`
- Entry point: `reddit-mcp = reddit_mcp_server.cli_main:main`
- Python >=3.12

### 1.2 RedditAPIClient (`scraping/api_client.py`)

The core HTTP client using `curl_cffi.requests.AsyncSession`:

```python
class RedditAPIClient:
    BASE_URL = "https://www.reddit.com"
    OAUTH_URL = "https://oauth.reddit.com"

    def __init__(self, cookies: dict[str, str]):
        self.session = AsyncSession(impersonate="chrome")
        # Set cookies on session
        for name, value in cookies.items():
            self.session.cookies.set(name, value, domain=".reddit.com")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ...",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
```

**Key design decisions:**
- `impersonate="chrome"` — curl_cffi will use Chrome's TLS fingerprint (JA3)
- Targets `www.reddit.com` internal API (not oauth.reddit.com) since we're using browser cookies
- Reddit's internal API uses `https://www.reddit.com/svc/shreddit/` and `https://gql.reddit.com/` endpoints
- Also supports legacy `https://www.reddit.com/api/` endpoints
- CSRF token extracted from cookies (`csrf_token` cookie) or fetched from page

**Methods:**
```python
async def _get(self, path: str, params: dict = None) -> dict
async def _post(self, path: str, data: dict = None, json: dict = None) -> dict
async def _request(self, method: str, url: str, **kwargs) -> dict
```

**Error handling:**
- 429 → RateLimitError (sleep + retry)
- 403 → AuthenticationError (session expired)
- 500/502/503 → RetryableError (exponential backoff, 3 retries)

### 1.3 Cookie Management

**Browser extraction** (same pattern as instagram-httpx-mcp):
- Read cookies from Brave/Chrome/Firefox/Edge SQLite DBs
- Filter for `.reddit.com` domain
- Required cookies: `reddit_session`, `token_v2`, `csrf_token`
- Store at `~/.reddit-mcp/cookies.json`

**Environment variable fallback:**
- `REDDIT_COOKIES` — JSON dict of cookies
- Written to disk on bootstrap

### 1.4 CLI Entry (`cli_main.py`)

```
reddit-mcp --login          # Interactive browser cookie import
reddit-mcp --logout         # Clear stored session
reddit-mcp --status         # Check session validity
reddit-mcp --transport stdio        # MCP stdio (default)
reddit-mcp --transport streamable-http  # HTTP transport
```

---

## Phase 2: Read Tools (Parity with reddit-cli)

### 2.1 `tools/browse.py` — `register_browse_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_browse_subreddit` | Get posts from a subreddit (sort: hot/new/top/rising/controversial, limit, time_filter) |
| `reddit_browse_frontpage` | Get user's personalized frontpage |
| `reddit_browse_popular` | Get r/popular posts |
| `reddit_browse_all` | Get r/all posts |

### 2.2 `tools/search.py` — `register_search_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_search_posts` | Search posts (query, subreddit, sort, time_filter, limit) |
| `reddit_search_subreddits` | Search for subreddits by name/topic |
| `reddit_search_users` | Search for users |

### 2.3 `tools/posts.py` — `register_post_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_get_post` | Get post details + comment tree (post_id/url, sort, depth, limit) |
| `reddit_get_comments` | Get comments for a post (sort: best/top/new/controversial/old) |

### 2.4 `tools/user.py` — `register_user_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_get_user_profile` | Get user profile (karma, cake day, trophies) |
| `reddit_get_user_posts` | Get user's submitted posts |
| `reddit_get_user_comments` | Get user's comment history |
| `reddit_get_my_profile` | Get authenticated user's own profile |

---

## Phase 3: Write Tools (NEW — beyond reddit-cli)

### 3.1 `tools/submit.py` — `register_submit_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_submit_text_post` | Submit a text/self post (subreddit, title, body, flair_id, nsfw, spoiler) |
| `reddit_submit_link_post` | Submit a link post (subreddit, title, url, flair_id) |
| `reddit_submit_image_post` | Submit an image post (subreddit, title, image_path) |
| `reddit_submit_poll` | Submit a poll post (subreddit, title, options[], duration_days) |
| `reddit_edit_post` | Edit a post's body text |
| `reddit_delete_post` | Delete own post |

### 3.2 `tools/comments.py` — `register_comment_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_comment` | Comment on a post (post_id, body) |
| `reddit_reply` | Reply to a comment (comment_id, body) |
| `reddit_edit_comment` | Edit own comment |
| `reddit_delete_comment` | Delete own comment |

### 3.3 `tools/vote.py` — `register_vote_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_upvote` | Upvote a post or comment (thing_id) |
| `reddit_downvote` | Downvote a post or comment |
| `reddit_unvote` | Remove vote from a post or comment |

### 3.4 `tools/messaging.py` — `register_messaging_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_get_inbox` | Get inbox messages (filter: all/unread/messages/mentions) |
| `reddit_send_message` | Send a private message (to_user, subject, body) |
| `reddit_reply_message` | Reply to a message |
| `reddit_mark_read` | Mark messages as read |

### 3.5 `tools/subreddit.py` — `register_subreddit_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_subscribe` | Subscribe to a subreddit |
| `reddit_unsubscribe` | Unsubscribe from a subreddit |
| `reddit_get_subreddit_info` | Get subreddit details (rules, description, subscribers) |
| `reddit_get_my_subreddits` | List user's subscribed subreddits |

### 3.6 `tools/save.py` — `register_save_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_save` | Save a post or comment |
| `reddit_unsave` | Unsave a post or comment |
| `reddit_get_saved` | Get user's saved items |
| `reddit_hide` | Hide a post |
| `reddit_unhide` | Unhide a post |

### 3.7 `tools/account.py` — `register_account_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_get_account_info` | Get own account details (karma breakdown, email, prefs) |
| `reddit_get_notifications` | Get notifications |
| `reddit_get_friends` | List friends |
| `reddit_add_friend` | Add a friend |
| `reddit_remove_friend` | Remove a friend |

### 3.8 `tools/moderation.py` — `register_moderation_tools(mcp)`
| Tool | Description |
|------|-------------|
| `reddit_mod_remove` | Remove a post/comment (as moderator) |
| `reddit_mod_approve` | Approve a post/comment |
| `reddit_mod_distinguish` | Distinguish a comment as mod |
| `reddit_mod_sticky` | Sticky/unsticky a post |
| `reddit_mod_lock` | Lock/unlock a post |
| `reddit_set_flair` | Set post/user flair |

---

## Phase 4: Reddit API Endpoints Reference

### Internal API (browser-cookie authenticated)

These are the endpoints Reddit's web app uses internally:

| Action | Method | Endpoint |
|--------|--------|----------|
| Get subreddit posts | GET | `/r/{sub}/{sort}.json` |
| Get post + comments | GET | `/r/{sub}/comments/{id}.json` |
| Search | GET | `/search.json?q={query}` |
| Submit post | POST | `/api/submit` |
| Comment/Reply | POST | `/api/comment` |
| Vote | POST | `/api/vote` (dir: 1, -1, 0) |
| Save | POST | `/api/save` |
| Unsave | POST | `/api/unsave` |
| Hide | POST | `/api/hide` |
| Delete | POST | `/api/del` |
| Edit | POST | `/api/editusertext` |
| Subscribe | POST | `/api/subscribe` (action: sub/unsub) |
| Send message | POST | `/api/compose` |
| Get inbox | GET | `/message/{where}.json` |
| Mark read | POST | `/api/read_message` |
| User about | GET | `/user/{name}/about.json` |
| My subreddits | GET | `/subreddits/mine/{where}.json` |
| Mod remove | POST | `/api/remove` |
| Mod approve | POST | `/api/approve` |
| Distinguish | POST | `/api/distinguish` |
| Set flair | POST | `/api/selectflair` |
| Get saved | GET | `/user/{me}/saved.json` |
| Get friends | GET | `/api/v1/me/friends` |

**Key headers for all requests:**
```
User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36
X-Modhash: {modhash_from_cookie_or_api}
Cookie: reddit_session=...; token_v2=...; csrf_token=...
```

**CSRF/Modhash**: Reddit requires a `modhash` or `uh` parameter for POST requests. Obtained from `/api/me.json` response or from page HTML.

---

## Phase 5: Testing Strategy

### 5.1 Unit Tests
- Mock curl_cffi responses
- Test each API client method
- Test cookie extraction logic
- Test error handling (429, 403, 500)

### 5.2 Integration Tests (live, gated by env var)
- `REDDIT_TEST_LIVE=1` enables live tests
- Test read operations against real Reddit
- Test write operations against a test subreddit (e.g., own profile subreddit)
- Verify session validity check

### 5.3 MCP Protocol Tests
- Test tool registration
- Test tool input validation
- Test sequential middleware
- Test error propagation to MCP clients

---

## Phase 6: Implementation Order

1. **Skeleton** — pyproject.toml, package structure, cli_main.py, server.py
2. **Cookie system** — cookie_import.py, session_state.py, bootstrap.py
3. **API Client** — RedditAPIClient with curl_cffi, auth validation
4. **Read tools** — browse, search, posts, user (parity with reddit-cli)
5. **Write tools** — submit, comments, vote (core engagement)
6. **Social tools** — messaging, subreddit, save, account
7. **Mod tools** — moderation actions
8. **Tests** — unit + integration test suite
9. **Polish** — error messages, rate limit handling, retry logic, docs

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `curl_cffi` over `httpx` | Reddit fingerprints TLS; curl_cffi impersonates Chrome's JA3 |
| Browser cookies over OAuth | Full access to all features without app registration; no scope limitations |
| `.json` suffix on endpoints | Reddit serves JSON when URL ends in `.json` — simpler than Accept headers |
| Sequential middleware | Prevents rate limiting from concurrent tool calls |
| `www.reddit.com` over `oauth.reddit.com` | Browser cookies authenticate against www, not oauth |
| Modhash for writes | Reddit's CSRF protection; extracted once per session |
| FastMCP framework | Same as instagram-httpx-mcp; proven MCP protocol handling |

---

## Dependencies

```toml
[project]
dependencies = [
    "fastmcp>=3.0.0",
    "curl_cffi>=0.7.0",
    "python-dotenv>=1.1.1",
    "inquirer>=3.4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
]
```

---

## Environment Variables

```bash
# Option 1: Direct cookie injection
REDDIT_COOKIES='{"reddit_session": "...", "token_v2": "...", "csrf_token": "..."}'

# Option 2: Browser extraction (interactive --login)
# No env vars needed; cookies extracted from browser DB

# Optional
REDDIT_MCP_PROFILE_DIR="~/.reddit-mcp"  # Override config dir
REDDIT_MCP_LOG_LEVEL="INFO"
```
