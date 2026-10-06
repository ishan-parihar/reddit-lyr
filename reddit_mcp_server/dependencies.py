import os

from reddit_mcp_server.bootstrap import ensure_ready_or_raise
from reddit_mcp_server.logging_config import logger
from reddit_mcp_server.obscura_integration import (
    get_valid_reddit_cookies,
    get_write_reddit_cookies,
    force_reddit_cookie_refresh,
    invalidate_reddit_auth,
)
from reddit_mcp_server.obscura_daemon_integration import (
    get_valid_reddit_cookies_from_daemon,
    get_write_reddit_cookies_from_daemon,
)
from reddit_mcp_server.scraping.api_client import RedditAPIClient
from reddit_mcp_server.exceptions import SessionExpiredError, AuthenticationError

# Daemon integration flag
# NOTE: the Obscura daemon serves ONE cached cookie set per platform (the
# default account's). It has no account awareness, so it MUST NOT be used for
# non-default accounts — it would silently supply the wrong session.
# Auto-disable unless we are on the default account.
USE_DAEMON = (
    os.getenv("REDDIT_USE_DAEMON", "true").lower() in ("1", "true", "yes", "on")
    and os.getenv("REDDIT_ACCOUNT", "default") == "default"
)

_client = None
_client_cookies_hash = None


def _cookies_hash(cookies: dict[str, str]) -> str:
    """Generate a hash of cookies to detect changes."""
    import hashlib

    # Only hash the important cookies
    important = {
        k: v for k, v in cookies.items() if k in ("token_v2", "reddit_session", "csrf_token")
    }
    return hashlib.sha256(str(sorted(important.items())).encode()).hexdigest()[:16]


async def get_reddit_client():
    """Get Reddit API client with auto-refreshing cookies."""
    global _client, _client_cookies_hash

    # Try daemon first if enabled
    if USE_DAEMON:
        try:
            result = await get_valid_reddit_cookies_from_daemon()
            if result.valid:
                logger.info("Got valid cookies from daemon")
            else:
                logger.debug(f"Daemon cookies invalid: {result.error}, falling back to local")
                result = await get_valid_reddit_cookies()
        except Exception as e:
            logger.debug(f"Failed to get cookies from daemon: {e}, falling back to local")
            result = await get_valid_reddit_cookies()
    else:
        result = await get_valid_reddit_cookies()

    if not result.valid:
        # Cookies are invalid, try to force refresh
        logger.warning(f"Cookie validation failed: {result.error}")
        result = await force_reddit_cookie_refresh()

        if not result.valid:
            raise SessionExpiredError(
                f"Reddit session expired: {result.error}. "
                "Run 'reddit-lyr --login' to re-authenticate."
            )

    # Check if cookies have changed since last client creation
    new_hash = _cookies_hash(result.cookies)
    if _client is not None and _client_cookies_hash == new_hash:
        return _client

    # Create new client with fresh cookies
    if _client is not None:
        await _client.close()

    _client = RedditAPIClient(result.cookies)
    _client_cookies_hash = new_hash
    _source = getattr(result.source, "value", result.source)
    logger.info(f"Reddit API client initialized (source: {_source})")

    # Validate session on first init
    health = await _client.check_session()
    if not health.get("valid"):
        reason = health.get("reason") or "unknown"
        logger.warning(f"Session health check failed: {reason}")
        # Try one more time with forced refresh
        result = await force_reddit_cookie_refresh()
        if result.valid:
            await _client.close()
            _client = RedditAPIClient(result.cookies)
            _client_cookies_hash = _cookies_hash(result.cookies)
            # Re-check the refreshed client too: fresh cookies can still be
            # anonymous (daemon cache from a not-logged-in browser session).
            health2 = await _client.check_session()
            if not health2.get("valid"):
                await _client.close()
                _client = None
                _client_cookies_hash = None
                raise SessionExpiredError(
                    f"Reddit session invalid after forced refresh: "
                    f"{health2.get('reason', 'unknown')}. "
                    "Run 'reddit-lyr --login' to re-authenticate."
                )
            logger.info("Reddit API client re-initialized after forced refresh")
        else:
            await _client.close()
            _client = None
            _client_cookies_hash = None
            raise SessionExpiredError(
                "Reddit session expired after forced refresh. "
                "Run 'reddit-lyr --login' to re-authenticate."
            )

    return _client


async def get_write_reddit_client():
    """Get Reddit API client validated for write operations."""
    global _client, _client_cookies_hash

    # Try daemon first if enabled
    if USE_DAEMON:
        try:
            result = await get_write_reddit_cookies_from_daemon()
            if result.valid:
                logger.info("Got valid write cookies from daemon")
            else:
                logger.debug(f"Daemon write cookies invalid: {result.error}, falling back to local")
                result = await get_write_reddit_cookies()
        except Exception as e:
            logger.debug(f"Failed to get write cookies from daemon: {e}, falling back to local")
            result = await get_write_reddit_cookies()
    else:
        result = await get_write_reddit_cookies()

    if not result.valid:
        logger.warning(f"Write cookie validation failed: {result.error}")
        result = await force_reddit_cookie_refresh()

        if not result.valid or "reddit_session" not in result.cookies:
            raise SessionExpiredError(
                f"Reddit write session expired: {result.error}. "
                "Run 'reddit-lyr --login' to re-authenticate for write access."
            )

    # Check if cookies have changed
    new_hash = _cookies_hash(result.cookies)
    if _client is not None and _client_cookies_hash == new_hash:
        return _client

    # Create new client
    if _client is not None:
        await _client.close()

    _client = RedditAPIClient(result.cookies)
    _client_cookies_hash = new_hash
    _source = getattr(result.source, "value", result.source)
    logger.info(f"Reddit write API client initialized (source: {_source})")

    return _client


async def refresh_client():
    """Invalidate cached client so next call re-reads and validates cookies."""
    global _client, _client_cookies_hash
    if _client is not None:
        await _client.close()
    _client = None
    _client_cookies_hash = None
    logger.info("Reddit API client reset — will re-read and validate cookies on next call")


def reset_client():
    """Synchronous reset for non-async contexts."""
    global _client, _client_cookies_hash
    if _client is not None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_client.close())
        except RuntimeError:
            pass  # no event loop running
    _client = None
    _client_cookies_hash = None


async def handle_auth_error(error: Exception) -> None:
    """Handle authentication errors by triggering cookie refresh."""
    if isinstance(error, (SessionExpiredError, AuthenticationError)):
        logger.warning(f"Auth error detected, forcing cookie refresh: {error}")
        await force_reddit_cookie_refresh()
    else:
        raise
