import asyncio
import json
import time
from typing import Any
from curl_cffi.requests import AsyncSession
from reddit_mcp_server.constants import (
    REDDIT_BASE_URL,
    DEFAULT_USER_AGENT,
    MAX_RETRIES,
    RETRY_BACKOFF,
)
from reddit_mcp_server.exceptions import (
    AuthenticationError,
    RateLimitError,
    SessionExpiredError,
    RedditMCPError,
)
from reddit_mcp_server.ratelimit import RateLimiter
from reddit_mcp_server.normalizer import (
    normalize_listing,
    normalize_user,
    normalize_subreddit,
    normalize_post_with_comments,
    normalize_overview,
)


class RedditAPIClient:
    def __init__(self, cookies: dict[str, str]):
        self._cookies = cookies
        self._session: AsyncSession | None = None
        self._modhash: str | None = None
        self._rate_limiter = RateLimiter(min_remaining=5)
        self._cookie_imported_at: float = time.time()

    async def _get_session(self) -> AsyncSession:
        if self._session is None:
            # Explicit timeout — curl_cffi defaults to NO timeout, so a hung
            # connection would block the MCP tool indefinitely.
            self._session = AsyncSession(impersonate="chrome", timeout=30)
            for name, value in self._cookies.items():
                self._session.cookies.set(name, value, domain=".reddit.com")
        return self._session

    async def _ensure_modhash(self) -> str:
        if self._modhash:
            return self._modhash
        data = await self._get("/api/me.json")
        self._modhash = data.get("data", {}).get("modhash", "")
        return self._modhash

    # === Core request with rate limiting + Retry-After ===

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        session = await self._get_session()
        url = f"{REDDIT_BASE_URL}{path}" if path.startswith("/") else path
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        kwargs.setdefault("headers", {}).update(headers)

        # Pre-throttle: sleep if we're near the ceiling
        await self._rate_limiter.acquire()

        for attempt in range(MAX_RETRIES):
            try:
                resp = await session.request(method, url, **kwargs)

                # Update rate limiter state from response headers
                self._rate_limiter.update_from_headers(dict(resp.headers))

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    await self._rate_limiter.wait_for_retry_after(retry_after)
                    continue
                if resp.status_code in (401, 403):
                    text = resp.text
                    if "login" in text.lower() or resp.status_code == 401:
                        raise SessionExpiredError("Session expired")
                    raise AuthenticationError(f"Auth error {resp.status_code}")
                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                        continue
                    raise RedditMCPError(f"Server error {resp.status_code}")
                resp.raise_for_status()
                raw_text = resp.text.strip()
                if not raw_text:
                    return {"status": "ok", "data": None}
                try:
                    result = json.loads(raw_text)
                except (json.JSONDecodeError, ValueError):
                    return {"status": "ok", "data": None, "raw": raw_text[:500]}
                # Reddit returns '"{}"' (string) for unauthenticated endpoints
                if isinstance(result, str):
                    return {"status": "requires_auth", "data": None, "raw": result}
                # Check for Reddit JSON error envelope
                if isinstance(result, dict) and "json" in result:
                    errors = result.get("json", {}).get("errors", [])
                    if errors:
                        error_code = errors[0][0] if errors else ""
                        error_msg = errors[0][1] if len(errors[0]) > 1 else ""
                        if error_code == "USER_REQUIRED":
                            raise SessionExpiredError(
                                f"Session expired: {error_msg or 'Please log in.'}"
                            )
                        if error_code:
                            raise RedditMCPError(f"Reddit API error [{error_code}]: {error_msg}")
                return result
            except (AuthenticationError, SessionExpiredError, RateLimitError):
                raise
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                raise RedditMCPError(f"Request failed: {e}") from e
        raise RateLimitError()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, data: dict | None = None) -> Any:
        modhash = await self._ensure_modhash()
        if data is None:
            data = {}
        data["uh"] = modhash
        data["api_type"] = "json"
        return await self._request("POST", path, data=data)

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    # === Cookie health check ===

    async def check_session(self) -> dict:
        """Cheap health check: call /api/me.json, return structured status."""
        try:
            data = await self.me()
            d = data.get("data") or {}
            username = d.get("name")
            if not username:
                # Reddit returns 200 with an anonymous payload (no name/modhash,
                # loid-based token) when cookies are not actually logged in.
                return {
                    "valid": False,
                    "reason": "Session anonymous: cookies present but not logged in (re-login required)",
                    "username": None,
                    "rate_limit_remaining": self._rate_limiter.remaining,
                }
            return {
                "valid": True,
                "username": username,
                "modhash_present": bool(d.get("modhash")),
                "rate_limit_remaining": self._rate_limiter.remaining,
            }
        except (SessionExpiredError, AuthenticationError):
            return {"valid": False, "reason": "Session expired"}
        except Exception as e:
            return {"valid": False, "reason": str(e)}

    async def check_write_session(self) -> dict:
        """Probe write capability: POST to /api/me.json (idempotent, cheap).
        Returns whether reddit_session cookie is valid for writes."""
        if "reddit_session" not in self._cookies:
            return {
                "valid": False,
                "reason": "reddit_session cookie missing — reads work but writes require re-login",
            }
        try:
            # /api/v1/me is read-only but requires valid session — acts as a write probe
            data = await self.me()
            d = data.get("data", {})
            return {
                "valid": True,
                "username": d.get("name"),
                "has_modhash": bool(d.get("modhash")),
            }
        except (SessionExpiredError, AuthenticationError):
            return {"valid": False, "reason": "reddit_session expired — re-login to restore writes"}
        except Exception as e:
            return {"valid": False, "reason": str(e)}

    # === Identity ===
    async def me(self) -> dict:
        return await self._get("/api/me.json")

    # === Browse (normalized) ===
    async def get_subreddit_posts(
        self,
        subreddit: str,
        sort: str = "hot",
        limit: int = 25,
        time_filter: str = "day",
        after: str | None = None,
    ) -> dict:
        params = {"limit": limit, "raw_json": 1, "t": time_filter}
        if after:
            params["after"] = after
        raw = await self._get(f"/r/{subreddit}/{sort}.json", params=params)
        return normalize_listing(raw, "post")

    async def get_frontpage(
        self, sort: str = "best", limit: int = 25, after: str | None = None
    ) -> dict:
        params = {"limit": limit, "raw_json": 1}
        if after:
            params["after"] = after
        raw = await self._get(f"/{sort}.json", params=params)
        return normalize_listing(raw, "post")

    async def get_popular(self, limit: int = 25) -> dict:
        raw = await self._get("/r/popular/hot.json", params={"limit": limit, "raw_json": 1})
        return normalize_listing(raw, "post")

    # === Search (normalized) ===
    async def search(
        self,
        query: str,
        subreddit: str | None = None,
        sort: str = "relevance",
        time_filter: str = "all",
        limit: int = 25,
    ) -> dict:
        params = {
            "q": query,
            "sort": sort,
            "t": time_filter,
            "limit": limit,
            "raw_json": 1,
            "type": "link",
        }
        path = f"/r/{subreddit}/search.json" if subreddit else "/search.json"
        if subreddit:
            params["restrict_sr"] = "on"
        raw = await self._get(path, params=params)
        return normalize_listing(raw, "post")

    async def search_subreddits(self, query: str, limit: int = 10) -> dict:
        raw = await self._get(
            "/subreddits/search.json",
            params={"q": query, "limit": limit, "raw_json": 1},
        )
        return normalize_listing(raw, "subreddit")

    async def search_users(self, query: str, limit: int = 10) -> dict:
        raw = await self._get(
            "/users/search.json", params={"q": query, "limit": limit, "raw_json": 1}
        )
        return normalize_listing(raw, "user")

    # === Posts (normalized) ===
    async def get_post(
        self, subreddit: str, post_id: str, sort: str = "best", limit: int = 50
    ) -> dict:
        params = {"sort": sort, "limit": limit, "raw_json": 1}
        raw = await self._get(f"/r/{subreddit}/comments/{post_id}.json", params=params)
        return normalize_post_with_comments(raw)

    async def get_post_by_id(self, post_id: str, sort: str = "best", limit: int = 50) -> dict:
        params = {"sort": sort, "limit": limit, "raw_json": 1}
        raw = await self._get(f"/comments/{post_id}.json", params=params)
        return normalize_post_with_comments(raw)

    # === User (normalized) ===
    async def get_user(self, username: str) -> dict:
        raw = await self._get(f"/user/{username}/about.json", params={"raw_json": 1})
        return normalize_user(raw)

    async def get_user_posts(self, username: str, sort: str = "new", limit: int = 25) -> dict:
        raw = await self._get(
            f"/user/{username}/submitted.json",
            params={"sort": sort, "limit": limit, "raw_json": 1},
        )
        return normalize_listing(raw, "post")

    async def get_user_comments(self, username: str, sort: str = "new", limit: int = 25) -> dict:
        raw = await self._get(
            f"/user/{username}/comments.json",
            params={"sort": sort, "limit": limit, "raw_json": 1},
        )
        return normalize_listing(raw, "comment")

    # === Submit ===
    async def submit_text(
        self,
        subreddit: str,
        title: str,
        text: str = "",
        flair_id: str | None = None,
        nsfw: bool = False,
        spoiler: bool = False,
    ) -> dict:
        data = {
            "sr": subreddit,
            "kind": "self",
            "title": title,
            "text": text,
            "nsfw": nsfw,
            "spoiler": spoiler,
        }
        if flair_id:
            data["flair_id"] = flair_id
        return await self._post("/api/submit", data=data)

    async def submit_link(
        self,
        subreddit: str,
        title: str,
        url: str,
        flair_id: str | None = None,
        nsfw: bool = False,
    ) -> dict:
        data = {
            "sr": subreddit,
            "kind": "link",
            "title": title,
            "url": url,
            "nsfw": nsfw,
        }
        if flair_id:
            data["flair_id"] = flair_id
        return await self._post("/api/submit", data=data)

    async def edit_post(self, thing_id: str, text: str) -> dict:
        return await self._post("/api/editusertext", data={"thing_id": thing_id, "text": text})

    async def delete_thing(self, thing_id: str) -> dict:
        return await self._post("/api/del", data={"id": thing_id})

    # === Comments ===
    async def comment(self, parent_id: str, text: str) -> dict:
        return await self._post("/api/comment", data={"thing_id": parent_id, "text": text})

    async def edit_comment(self, thing_id: str, text: str) -> dict:
        return await self._post("/api/editusertext", data={"thing_id": thing_id, "text": text})

    # === Vote ===
    async def vote(self, thing_id: str, direction: int) -> dict:
        return await self._post("/api/vote", data={"id": thing_id, "dir": str(direction)})

    # === Save ===
    async def save(self, thing_id: str) -> dict:
        return await self._post("/api/save", data={"id": thing_id})

    async def unsave(self, thing_id: str) -> dict:
        return await self._post("/api/unsave", data={"id": thing_id})

    async def hide(self, thing_id: str) -> dict:
        return await self._post("/api/hide", data={"id": thing_id})

    async def unhide(self, thing_id: str) -> dict:
        return await self._post("/api/unhide", data={"id": thing_id})

    async def get_saved(self, limit: int = 25) -> dict:
        data = await self.me()
        username = data["data"]["name"]
        raw = await self._get(
            f"/user/{username}/saved.json", params={"limit": limit, "raw_json": 1}
        )
        return normalize_listing(raw, "post")  # saved can be mixed, but posts are primary

    async def report(
        self,
        thing_id: str,
        reason: str = "",
        rule_reason: str = "",
        site_reason: str = "",
        other_reason: str = "",
    ) -> dict:
        data = {"thing_id": thing_id, "reason": reason}
        if rule_reason:
            data["rule_reason"] = rule_reason
        if site_reason:
            data["site_reason"] = site_reason
        if other_reason:
            data["other_reason"] = other_reason
        return await self._post("/api/report", data=data)

    async def block_user(self, username: str) -> dict:
        return await self._post("/api/block_user", data={"name": username})

    async def follow_post(self, thing_id: str, follow: bool = True) -> dict:
        return await self._post("/api/follow_post", data={"fullname": thing_id, "follow": follow})

    async def sendreplies(self, thing_id: str, state: bool = True) -> dict:
        return await self._post("/api/sendreplies", data={"id": thing_id, "state": state})

    async def marknsfw(self, thing_id: str) -> dict:
        return await self._post("/api/marknsfw", data={"id": thing_id})

    async def unmarknsfw(self, thing_id: str) -> dict:
        return await self._post("/api/unmarknsfw", data={"id": thing_id})

    async def spoiler(self, thing_id: str) -> dict:
        return await self._post("/api/spoiler", data={"id": thing_id})

    async def unspoiler(self, thing_id: str) -> dict:
        return await self._post("/api/unspoiler", data={"id": thing_id})

    # === Info (normalized) ===
    async def get_karma(self) -> dict:
        data = await self.me()
        d = data.get("data", {})
        return {
            "link_karma": d.get("link_karma", 0),
            "comment_karma": d.get("comment_karma", 0),
            "total_karma": d.get("total_karma", 0),
            "awardee_karma": d.get("awardee_karma", 0),
            "awarder_karma": d.get("awarder_karma", 0),
        }

    async def get_user_overview(self, username: str, sort: str = "new", limit: int = 25) -> dict:
        raw = await self._get(
            f"/user/{username}/overview.json",
            params={"sort": sort, "limit": limit, "raw_json": 1},
        )
        return normalize_overview(raw)

    async def get_subreddit_rules(self, subreddit: str) -> dict:
        raw = await self._get(f"/r/{subreddit}/about/rules.json", params={"raw_json": 1})
        # Rules are already pretty clean, just pass through the rules array
        rules = raw.get("rules", [])
        return {
            "rules": [
                {
                    "short_name": r.get("short_name"),
                    "description": r.get("description"),
                    "violation_reason": r.get("violation_reason"),
                }
                for r in rules
            ]
        }

    async def get_subreddit_moderators(self, subreddit: str) -> dict:
        raw = await self._get(f"/r/{subreddit}/about/moderators.json", params={"raw_json": 1})
        return normalize_listing(raw, "user")

    # === Messaging ===
    async def get_inbox(self, where: str = "inbox", limit: int = 25) -> dict:
        raw = await self._get(f"/message/{where}.json", params={"limit": limit, "raw_json": 1})
        return normalize_listing(raw, item_type="message")

    async def send_message(self, to: str, subject: str, body: str) -> dict:
        return await self._post("/api/compose", data={"to": to, "subject": subject, "text": body})

    async def read_message(self, thing_id: str) -> dict:
        return await self._post("/api/read_message", data={"id": thing_id})

    async def read_all_messages(self) -> dict:
        return await self._post("/api/read_all_messages")

    # === Subreddit ===
    async def subscribe(self, subreddit: str) -> dict:
        return await self._post("/api/subscribe", data={"sr_name": subreddit, "action": "sub"})

    async def unsubscribe(self, subreddit: str) -> dict:
        return await self._post("/api/subscribe", data={"sr_name": subreddit, "action": "unsub"})

    async def get_subreddit_info(self, subreddit: str) -> dict:
        raw = await self._get(f"/r/{subreddit}/about.json", params={"raw_json": 1})
        return normalize_subreddit(raw)

    async def get_my_subreddits(self, limit: int = 100) -> dict:
        raw = await self._get(
            "/subreddits/mine/subscriber.json", params={"limit": limit, "raw_json": 1}
        )
        return normalize_listing(raw, "subreddit")

    # === Account ===
    async def get_friends(self) -> dict:
        return await self._get("/api/v1/me/friends.json", params={"raw_json": 1})

    async def add_friend(self, username: str) -> dict:
        return await self._request("PUT", f"/api/v1/me/friends/{username}", json={"name": username})

    async def remove_friend(self, username: str) -> dict:
        return await self._request("DELETE", f"/api/v1/me/friends/{username}")

    # === Moderation ===
    async def mod_remove(self, thing_id: str, spam: bool = False) -> dict:
        return await self._post("/api/remove", data={"id": thing_id, "spam": spam})

    async def mod_approve(self, thing_id: str) -> dict:
        return await self._post("/api/approve", data={"id": thing_id})

    async def mod_distinguish(self, thing_id: str, how: str = "yes") -> dict:
        return await self._post("/api/distinguish", data={"id": thing_id, "how": how})

    async def mod_sticky(self, thing_id: str, state: bool = True) -> dict:
        return await self._post("/api/set_subreddit_sticky", data={"id": thing_id, "state": state})

    async def mod_lock(self, thing_id: str) -> dict:
        return await self._post("/api/lock", data={"id": thing_id})

    async def mod_unlock(self, thing_id: str) -> dict:
        return await self._post("/api/unlock", data={"id": thing_id})
