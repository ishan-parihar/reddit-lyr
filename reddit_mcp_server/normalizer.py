"""Flatten Reddit's deeply nested responses into clean objects.

Reddit wraps everything in data.children[].data. This module extracts
the important fields, dramatically reducing token usage for LLM consumption.

Before: {"data": {"children": [{"data": {"title": "...", "selftext": "...", "score": 1, ...100 fields...}}]}}
After:  {"posts": [{"id": "...", "title": "...", "score": 1, "num_comments": 5, ...}], "next": "t3_xxx"}
"""

POST_FIELDS = {
    "id",
    "title",
    "author",
    "subreddit",
    "score",
    "num_comments",
    "upvote_ratio",
    "url",
    "selftext",
    "permalink",
    "created_utc",
    "link_flair_text",
    "is_self",
    "domain",
    "thumbnail",
    "over_18",
    "spoiler",
    "locked",
    "stickied",
    "distinguished",
    "name",  # name = fullname (t3_xxx)
}

COMMENT_FIELDS = {
    "id",
    "author",
    "body",
    "score",
    "created_utc",
    "depth",
    "parent_id",
    "permalink",
    "name",
    "distinguished",
    "stickied",
    "controversiality",
}

SUBREDDIT_FIELDS = {
    "id",
    "display_name",
    "title",
    "public_description",
    "subscribers",
    "active_user_count",
    "created_utc",
    "over18",
    "wiki_enabled",
    "rules",
    "name",
}

USER_FIELDS = {
    "id",
    "name",
    "link_karma",
    "comment_karma",
    "total_karma",
    "created_utc",
    "has_verified_email",
    "is_gold",
    "is_mod",
}

MESSAGE_FIELDS = {
    "id",
    "author",
    "body",
    "subject",
    "name",
    "created_utc",
    "was_read",
    "score",
    "dest",
    "context",
    "replies",
}

# AXI §3: truncate long text fields to save tokens
MAX_TEXT_LENGTH = 500


def _truncate_text(text: str, key: str, out: dict) -> None:
    """Truncate text field if too long. Sets out[key] in-place.
    Respects --full flag: when FULL_OUTPUT is True, skip truncation (AXI §3)."""
    try:
        from reddit_mcp_server.cli_main import FULL_OUTPUT
    except ImportError:
        FULL_OUTPUT = False

    if FULL_OUTPUT:
        out[key] = text
        return
    if not isinstance(text, str) or len(text) <= MAX_TEXT_LENGTH:
        out[key] = text
    else:
        out[key] = text[:MAX_TEXT_LENGTH] + "..."
        out[f"{key}_chars"] = len(text)


def _extract_fields(data: dict, allowed: set) -> dict:
    """Extract only the allowed fields from a Reddit data dict."""
    out = {}
    for k, v in data.items():
        if k not in allowed or v is None:
            continue
        # Truncate long body/selftext fields (AXI §3)
        if k in ("selftext", "body") and isinstance(v, str) and len(v) > MAX_TEXT_LENGTH:
            _truncate_text(v, k, out)
        else:
            out[k] = v
    return out


def normalize_listing(raw: dict, item_type: str = "post") -> dict:
    """Normalize a Reddit listing response (data.children[]).

    Returns: {"items": [...], "next": "t3_xxx" | None, "before": "t3_xxx" | None}
    """
    allowed = (
        POST_FIELDS
        if item_type == "post"
        else COMMENT_FIELDS
        if item_type == "comment"
        else MESSAGE_FIELDS
        if item_type == "message"
        else SUBREDDIT_FIELDS
    )

    data = raw.get("data") or {}
    children = (data or {}).get("children", []) or []
    items = [_extract_fields(child.get("data", {}), allowed) for child in children]

    return {
        "items": items,
        "next": data.get("after"),
        "before": data.get("before"),
        "count": len(items),
    }


def normalize_user(raw: dict) -> dict:
    """Normalize a Reddit user about response."""
    data = raw.get("data", {})
    return _extract_fields(data, USER_FIELDS)


def normalize_subreddit(raw: dict) -> dict:
    """Normalize a subreddit about response."""
    data = raw.get("data", {})
    return _extract_fields(data, SUBREDDIT_FIELDS)


def normalize_post_with_comments(raw: list) -> dict:
    """Normalize the two-element array from /comments/{id}.json.

    raw[0] = post listing, raw[1] = comments listing
    """
    if not isinstance(raw, list) or len(raw) < 2:
        return {"post": None, "comments": [], "next": None}

    post_listing = raw[0]
    comment_listing = raw[1]

    # Extract post
    post_children = post_listing.get("data", {}).get("children", [])
    post_data = post_children[0].get("data", {}) if post_children else {}
    post = _extract_fields(post_data, POST_FIELDS) if post_data else None

    # Extract comments
    comment_children = comment_listing.get("data", {}).get("children", [])
    comments = [
        _extract_fields(c.get("data", {}), COMMENT_FIELDS)
        for c in comment_children
        if c.get("kind") == "t1"
    ]
    next_comment = comment_listing.get("data", {}).get("after")

    return {"post": post, "comments": comments, "next_comments": next_comment}


def normalize_overview(raw: dict) -> dict:
    """Normalize user overview (mixed posts + comments)."""
    data = raw.get("data", {})
    children = data.get("children", [])
    items = []
    for child in children:
        kind = child.get("kind", "")
        item_data = child.get("data", {})
        if kind == "t3":
            item = _extract_fields(item_data, POST_FIELDS)
            item["type"] = "post"
            items.append(item)
        elif kind == "t1":
            item = _extract_fields(item_data, COMMENT_FIELDS)
            item["type"] = "comment"
            items.append(item)

    return {"items": items, "next": data.get("after"), "count": len(items)}
