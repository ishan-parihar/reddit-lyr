import json
import os
import sys
import argparse

# ── AXI §3: --full flag controls text truncation in normalizer ────────────
FULL_OUTPUT = False  # Set True via --full to bypass truncation (AXI §3)


# ── TOON output helpers (AXI §1) ──────────────────────────────────────────


def _toon_quote(val: str) -> str:
    """Quote a string value per TOON spec when it contains special chars."""
    if val == "":
        return '""'
    # Must quote: looks like bool/number, contains delimiter/colon/bracket, starts with -
    needs_quote = (
        val in ("true", "false", "null")
        or val.lstrip("-").replace(".", "", 1).replace("e", "", 1).replace("+", "", 1).isdigit()
        or any(c in val for c in (":", ",", '"', "[", "]", "{", "}", "#"))
        or val.startswith("-")
        or val.startswith("#")
        or val.startswith(" ")
        or val.endswith(" ")
        or val.startswith('"')
        or val.endswith('"')
    )
    if needs_quote:
        escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return val


def _toon_kv(key: str, val) -> str:
    """Emit a single key: value TOON line."""
    if isinstance(val, bool):
        return f"{key}: {'true' if val else 'false'}"
    if isinstance(val, (int, float)):
        return f"{key}: {val}"
    return f"{key}: {_toon_quote(str(val))}"


def axi_error(msg: str, hint: str = None) -> None:
    """Print structured error in TOON format (AXI §6) and exit with code 2."""
    print(_toon_kv("error", msg))
    if hint:
        print(_toon_kv("help", hint))
    sys.exit(2)


def _toon_object(fields: dict) -> str:
    """Render a flat or one-level-nested object as TOON text."""
    lines = []
    for k, v in fields.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for sk, sv in v.items():
                lines.append(f"  {_toon_kv(sk, sv)}")
        elif isinstance(v, list):
            # list of dicts → tabular
            if v and isinstance(v[0], dict):
                keys = list(v[0].keys())
                hdr = ",".join(keys)
                lines.append(f"{k}[{len(v)}]{{{hdr}}}:")
                for item in v:
                    cells = ",".join(_toon_quote(str(item.get(fk, ""))) for fk in keys)
                    lines.append(f"  {cells}")
            else:
                # primitive list
                items = ",".join(_toon_quote(str(x)) for x in v)
                lines.append(f"{k}[{len(v)}]: {items}")
        else:
            lines.append(_toon_kv(k, v))
    return "\n".join(lines)


# AXI §2: minimal default schemas — 4 fields max for list outputs in TOON
_COMPACT_FIELDS = {
    "post": ["id", "title", "score", "num_comments"],
    "comment": ["id", "author", "body", "score"],
    "subreddit": ["name", "display_name", "subscribers"],
    "user": ["name", "link_karma", "comment_karma"],
    "message": ["id", "author", "subject", "was_read"],
}

_COMPACT_FALLBACK = 4  # default: first 4 keys if type unknown


def _detect_item_type(items: list[dict]) -> str | None:
    """Heuristically detect item type from first item's keys."""
    if not items or not isinstance(items[0], dict):
        return None
    keys = set(items[0].keys())
    if "title" in keys and "num_comments" in keys:
        return "post"
    if "depth" in keys or ("body" in keys and "parent_id" in keys):
        return "comment"
    if "subscribers" in keys:
        return "subreddit"
    if "link_karma" in keys:
        return "user"
    if "subject" in keys and "was_read" in keys:
        return "message"
    return None


def _compact_result(result: dict) -> dict:
    """Reduce list items to minimal field sets for TOON output (AXI §2)."""
    if not isinstance(result, dict) or "items" not in result:
        return result
    items = result["items"]
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        # AXI §5: definitive empty state — replace empty items with count
        if isinstance(items, list) and len(items) == 0:
            result = dict(result)
            result["items"] = []  # keep for TOON table header
        return result

    item_type = _detect_item_type(items)
    fields = _COMPACT_FIELDS.get(item_type) or list(items[0].keys())[:_COMPACT_FALLBACK]

    compact_items = []
    for item in items:
        compact_items.append({k: item[k] for k in fields if k in item})
    result = dict(result)  # shallow copy
    result["items"] = compact_items
    return result


def _has_truncation(result: dict) -> bool:
    """Check if any item in result has truncation markers (_chars fields)."""
    if not isinstance(result, dict):
        return False
    for item in result.get("items", []):
        if isinstance(item, dict) and any(k.endswith("_chars") for k in item):
            return True
    for key in result:
        if key.endswith("_chars"):
            return True
    return False


def _truncate(s: str, max_chars: int = 60) -> str:
    """Truncate string with ellipsis (AXI §3)."""
    if len(s) <= max_chars:
        return s
    return f"{s[:max_chars]}...\n  ... (truncated, {len(s)} chars total)"


def _get_bin_path() -> str:
    """Get executable path with home dir collapsed to ~ (AXI §10)."""
    try:
        # Use sys.argv[0] which is the actual CLI entry point
        exe = sys.argv[0] if sys.argv else "reddit-lyr"
        home = os.environ.get("HOME", "")
        if home and exe.startswith(home):
            return exe.replace(home, "~", 1)
        return exe
    except Exception:
        return "reddit-lyr"


# ── Tool registry (auto-discovered from MCP server) ────────────────────────


def _discover_tools() -> list[tuple[str, str]]:
    """Dynamically list all registered MCP tools with their docstrings."""
    try:
        import asyncio
        from reddit_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        tools_obj = asyncio.run(mcp.list_tools())
        return sorted(
            [(t.name, (t.description or "").split("\n")[0].strip()) for t in tools_obj],
            key=lambda x: x[0],
        )
    except Exception:
        # Fallback: static list if discovery fails
        return [
            ("browse_subreddit", "Browse posts from a subreddit"),
            ("browse_frontpage", "Browse the personalized frontpage"),
            ("browse_popular", "Browse r/popular"),
            ("search_posts", "Search Reddit posts"),
            ("search_subreddits", "Search for subreddits"),
            ("search_users", "Search for Reddit users"),
            ("get_post", "Get a post's details and comment tree"),
            ("get_comments", "Get comments for a post"),
            ("get_user_profile", "Get a Reddit user's profile"),
            ("get_user_posts", "Get a user's recent posts"),
            ("get_user_comments", "Get a user's recent comments"),
            ("get_my_profile", "Get the authenticated user's profile"),
        ]


# ponytail: lazy — _discover_tools() calls create_mcp_server() which logs;
# defer past dup2 so the handler captures /devnull stderr, not the original.
_TOOLS_CACHE = None


def _get_tools() -> list[tuple[str, str]]:
    global _TOOLS_CACHE
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = _discover_tools()
    return _TOOLS_CACHE


# ── AXI §8: Content-first home view ───────────────────────────────────────


def show_home_view(fields: list[str] | None = None) -> None:
    """Show live state when no args provided (AXI §8)."""
    bin_path = _get_bin_path()

    # Check session state (no network call — AXI §8: content-first, instant)
    has_cookies = False
    auth_status = {"authenticated": False}
    try:
        from reddit_mcp_server.session_state import load_cookies
        from reddit_mcp_server.authentication import get_auth_status

        has_cookies = load_cookies() is not None
        if has_cookies:
            auth_status = get_auth_status()
    except Exception as e:
        # Log warning but don't fail — session check is best-effort
        print(json.dumps({"warning": f"Session check failed: {e}"}), file=sys.stderr)

    # AXI §10: Tool identity header
    print(f"bin: {bin_path}")
    print("description: Reddit MCP server — browsing, posting, commenting, voting, and moderation")
    print()

    # Live session state
    print("session:")
    if has_cookies and auth_status.get("authenticated", False):
        print("  status: authenticated")
        # Include cookies count if requested
        if fields and "cookies" in fields:
            print(f"  cookies: {auth_status.get('cookies_count', 0)}")
    else:
        print("  status: not_authenticated")
        print("  help: Run `reddit-lyr --login` or `reddit-lyr --cookies-file <path>`")
    print()

    # Tool listing in TOON format (AXI §2: minimal schema)
    tools = _get_tools()
    # Default minimal schema: name,description. Extend if fields requested.
    schema_fields = ["name", "description"]
    if fields:
        for f in fields:
            if f not in schema_fields:
                schema_fields.append(f)
    print(f"tools[{len(tools)}]{{{','.join(schema_fields)}}}:")
    for name, desc in tools:
        row = [name, _truncate(desc)]
        # Currently only name and description are available for tools
        print(f"  {','.join(row)}")
    print()

    # AXI §9: Contextual disclosure (dynamic count)
    hints = [
        "Run `reddit-lyr --tool-info <name>` for detailed parameters",
        "Run `reddit-lyr --list-tools` to see all tools",
        "Run `reddit-lyr --login` to import browser cookies",
        "Run `reddit-lyr --cookies-file <path>` to import cookies from JSON",
        "Run `reddit-lyr` to start the MCP server",
    ]
    print(f"help[{len(hints)}]:")
    for h in hints:
        print(f"  {h}")


def list_tools_and_exit(fields: list[str] | None = None) -> None:
    """List all available MCP tools and exit (AXI §8 content-first)."""
    tools = _get_tools()
    # Default minimal schema: name,description. Extend if fields requested.
    schema_fields = ["name", "description"]
    if fields:
        for f in fields:
            if f not in schema_fields:
                schema_fields.append(f)
    print(f"tools[{len(tools)}]{{{','.join(schema_fields)}}}:")
    for name, desc in tools:
        row = [name, desc]
        print(f"  {','.join(row)}")
    print()
    print("help[3]:")
    print("  Run `reddit-lyr <tool> [args]` to call a tool directly")
    print("  Run `reddit-lyr --tool-info <name>` for details")
    print("  Run `reddit-lyr` to start the MCP server")
    sys.exit(0)


def tool_info_and_exit(tool_name: str) -> None:
    """Show detailed info for a specific tool in TOON format."""
    try:
        import asyncio
        from reddit_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        tools_obj = asyncio.run(mcp.list_tools())
        tool = next((t for t in tools_obj if t.name == tool_name), None)
        if tool:
            fields = {"name": tool.name, "description": tool.description or ""}
            # Flatten schema to TOON param list (AXI §2: minimal schemas)
            schema = getattr(tool, "inputSchema", None) or getattr(tool, "parameters", None) or {}
            if isinstance(schema, dict):
                props = schema.get("properties", {})
                required = set(schema.get("required", []))
                if props:
                    params = []
                    for pname, pdef in props.items():
                        params.append({
                            "name": pname,
                            "type": pdef.get("type", "any"),
                            "required": "true" if pname in required else "false",
                            "description": _truncate(pdef.get("description", ""), 80),
                        })
                    fields["params"] = params
            print(_toon_object(fields))
            # AXI §9: Contextual next-step after detail view
            print(f"help[1]: Run `reddit-lyr` to start the MCP server and call `{tool.name}`")
        else:
            valid = sorted([t.name for t in tools_obj])
            axi_error(f"Unknown tool: '{tool_name}'", f"Valid tools: {', '.join(valid)}")
    except Exception as e:
        axi_error(f"Failed to load tool info: {e}")
    sys.exit(0)


def install_session_hook() -> None:
    """Install session hook for ambient context (AXI §7)."""
    import os
    import json
    import sys

    bin_path = _get_bin_path()
    if bin_path.startswith("~"):
        home = os.path.expanduser("~")
        bin_path = bin_path.replace("~", home, 1)

    hooks_installed = []

    # Claude Code: ~/.claude/settings.json
    claude_settings = os.path.expanduser("~/.claude/settings.json")
    try:
        if os.path.exists(claude_settings):
            with open(claude_settings) as f:
                settings = json.load(f)
        else:
            settings = {}
        hooks = settings.get("hooks", {})
        session_start = hooks.get("SessionStart", [])
        already = any(
            h.get("command") == f"{bin_path} --status" for h in session_start if isinstance(h, dict)
        )
        if not already:
            session_start.append({"command": f"{bin_path} --status"})
            hooks["SessionStart"] = session_start
            settings["hooks"] = hooks
            os.makedirs(os.path.dirname(claude_settings), exist_ok=True)
            with open(claude_settings, "w") as f:
                json.dump(settings, f, indent=2)
            hooks_installed.append("Claude Code")
    except Exception as e:
        print(f"Claude Code hook: {e}", file=sys.stderr)

    # Codex: ~/.codex/hooks.json
    codex_hooks = os.path.expanduser("~/.codex/hooks.json")
    try:
        if os.path.exists(codex_hooks):
            with open(codex_hooks) as f:
                hooks = json.load(f)
        else:
            hooks = {}
        session_start = hooks.get("SessionStart", [])
        already = any(
            h.get("command") == f"{bin_path} --status" for h in session_start if isinstance(h, dict)
        )
        if not already:
            session_start.append({"command": f"{bin_path} --status"})
            hooks["SessionStart"] = session_start
            os.makedirs(os.path.dirname(codex_hooks), exist_ok=True)
            with open(codex_hooks, "w") as f:
                json.dump(hooks, f, indent=2)
            hooks_installed.append("Codex")
    except Exception as e:
        print(f"Codex hook: {e}", file=sys.stderr)

    # OpenCode: ~/.config/opencode/plugin.json
    opencode_plugin = os.path.expanduser("~/.config/opencode/plugin.json")
    try:
        if os.path.exists(opencode_plugin):
            with open(opencode_plugin) as f:
                plugin = json.load(f)
        else:
            plugin = {"name": "reddit-lyr", "hooks": {}}
        hooks = plugin.get("hooks", {})
        session_start = hooks.get("session_start", [])
        already = any(
            h.get("command") == f"{bin_path} --status" for h in session_start if isinstance(h, dict)
        )
        if not already:
            session_start.append({"command": f"{bin_path} --status"})
            hooks["session_start"] = session_start
            plugin["hooks"] = hooks
            os.makedirs(os.path.dirname(opencode_plugin), exist_ok=True)
            with open(opencode_plugin, "w") as f:
                json.dump(plugin, f, indent=2)
            hooks_installed.append("OpenCode")
    except Exception as e:
        print(f"OpenCode hook: {e}", file=sys.stderr)

    if hooks_installed:
        print(
            _toon_object({
                "status": "success",
                "message": f"Installed hooks for: {', '.join(hooks_installed)}",
            })
        )
    else:
        print(
            _toon_object({
                "status": "info",
                "message": "Hooks already installed or no supported editors found",
            })
        )
    sys.exit(0)


def install_agent_skill() -> None:
    """Create installable agent skill from home view (AXI §7)."""
    from pathlib import Path

    bin_path = _get_bin_path()
    skill_dir = Path.home() / ".claude" / "skills" / "reddit-lyr"
    skill_dir.mkdir(parents=True, exist_ok=True)

    skill_content = """name: Reddit MCP Server
description: Reddit automation with browsing, posting, commenting, voting, and moderation features
triggers:
  - "reddit post"
  - "reddit search"
  - "reddit comment"
  - "reddit automation"
  - "social media posting"
  - "content creation"
  - "subreddit"

## Overview
Reddit MCP Server provides comprehensive Reddit automation:
- Browse posts, comments, and subreddits
- Search posts, users, and subreddits
- Post and comment
- Vote and save content
- User profiles and karma
- Moderation tools

## Quick Start
```bash
# Show home view with live state
reddit-lyr

# Import browser cookies
reddit-lyr --login

# Check session status
reddit-lyr --status

# List available tools
reddit-lyr --list-tools

# Start MCP server
reddit-lyr
```

## MCP Tools
- `browse_subreddit` - Browse posts from a subreddit
- `browse_frontpage` - Browse the personalized frontpage
- `browse_popular` - Browse r/popular
- `search_posts` - Search Reddit posts
- `search_subreddits` - Search for subreddits
- `search_users` - Search for Reddit users
- `get_post` - Get a post's details and comment tree
- `get_comments` - Get comments for a post
- `get_user_profile` - Get a Reddit user's profile
- `get_user_posts` - Get a user's recent posts
- `get_user_comments` - Get a user's recent comments
- `get_my_profile` - Get the authenticated user's profile
- `vote` - Vote on a post or comment
- `save_post` - Save a post
- `submit_post` - Submit a new post
- `submit_comment` - Submit a new comment

## Session Integration
Install session hooks for ambient context:
```bash
reddit-lyr --install-hook
```

This shows Reddit session state on every agent session start.

## Output Formats
- `--toon` - TOON format (default, token-efficient)
- `--json` - JSON format
- `--full` - Show complete text fields (no truncation)
- `--fields <fields>` - Comma-separated extra fields for lists

## Examples
```bash
# Browse a subreddit
reddit-lyr browse_subreddit --subreddit python --limit 5

# Search posts
reddit-lyr search_posts --query "rust lang" --limit 3

# Get post details
reddit-lyr get_post --post_id_or_url 1unctej

# Vote on a post
reddit-lyr vote --thing_id t3_1unctej --direction up
```
"""

    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(skill_content)

    print(
        _toon_object({
            "status": "success",
            "skill_path": str(skill_file),
            "help": "Agent skill installed - will load automatically on Reddit-related tasks",
        })
    )
    sys.exit(0)


def show_help() -> None:
    """Show usage information (AXI §10)."""
    print("reddit-lyr v0.1.0")
    print("Reddit MCP server — browsing, posting, commenting, voting, and moderation")
    print()
    print("Usage:")
    print("  reddit-lyr                    Show home view with live state")
    print("  reddit-lyr <tool> [args]      Call a tool directly (see examples)")
    print("  reddit-lyr --list-tools       List all available MCP tools")
    print("  reddit-lyr --tool-info <name> Show details for a specific tool")
    print("  reddit-lyr --login            Import cookies from browser")
    print("  reddit-lyr --cookies-file F   Import cookies from a JSON file")
    print("  reddit-lyr --logout           Clear stored session")
    print("  reddit-lyr --status           Check authentication status")
    print("  reddit-lyr --account NAME     Operate on a specific account")
    print("  reddit-lyr --accounts         List registered accounts")
    print("  reddit-lyr --fields <fields>  Comma-separated extra fields for lists")
    print("  reddit-lyr --full            Show complete text fields (no truncation)")
    print("  reddit-lyr --json            Render tool results as JSON (default is TOON)")
    print("  reddit-lyr --help             Show this help message")
    print()
    print("Environment:")
    print("  REDDIT_COOKIES     JSON string of cookies (loaded at server startup)")
    print("  REDDIT_ACCOUNT     Select account (same as --account; for MCP/cron)")
    print("  REDDIT_MCP_LOG_LEVEL  Log level: DEBUG, INFO, WARNING, ERROR")
    print()
    print("Examples:")
    print("  reddit-lyr browse_subreddit --subreddit python --limit 5")
    print("  reddit-lyr browse_subreddit python --limit 5   # positional shorthand")
    print("  reddit-lyr search_posts --query 'rust lang' --limit 3")
    print("  reddit-lyr get_post --post_id_or_url 1unctej")
    print("  reddit-lyr vote --thing_id t3_1unctej --direction up")
    print("  reddit-lyr get_my_subreddits --limit 5 --json")
    print("  reddit-lyr --tool-info search_posts")
    print("  reddit-lyr --list-tools")
    print("  reddit-lyr --login")


def run_tool_direct(tool_name: str, args: list[str], use_json: bool = False) -> None:
    """Execute a tool directly from CLI without MCP protocol."""
    import asyncio
    from reddit_mcp_server.server import create_mcp_server
    from reddit_mcp_server.bootstrap import initialize_bootstrap

    # Parse --key value pairs into a dict
    kwargs = {}
    positional = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                kwargs[key] = args[i + 1]
                i += 2
            else:
                kwargs[key] = "true"
                i += 1
        else:
            positional.append(arg)
            i += 1

    # Get tool object
    mcp = create_mcp_server()
    tools = asyncio.run(mcp.list_tools())
    tool = next((t for t in tools if t.name == tool_name), None)
    if not tool:
        valid = sorted(t.name for t in tools)
        axi_error(f"Unknown tool: '{tool_name}'", f"Valid tools: {', '.join(valid)}")

    # Map positional args to required params
    schema = tool.parameters or {}
    props = schema.get("properties", {})
    required = schema.get("required", [])
    required_params = [p for p in required if p in props]

    for idx, val in enumerate(positional):
        if idx < len(required_params):
            kwargs[required_params[idx]] = val
        else:
            axi_error(
                f"Unexpected positional arg: '{val}'",
                f"Tool `{tool_name}` expects: {', '.join(required_params)}",
            )

    # Type coercion from schema
    for key, val in list(kwargs.items()):
        if key in props:
            prop = props[key]
            prop_type = prop.get("type", "string")
            try:
                if prop_type == "integer":
                    kwargs[key] = int(val)
                elif prop_type == "number":
                    kwargs[key] = float(val)
                elif prop_type == "boolean":
                    kwargs[key] = val.lower() in ("true", "1", "yes")
            except (ValueError, TypeError):
                axi_error(
                    f"Invalid value for `{key}`: '{val}' (expected {prop_type})",
                    f"Tool `{tool_name}` parameter `{key}` expects type {prop_type}",
                )

    # Initialize bootstrap (loads cookies)
    initialize_bootstrap()

    # Call the tool
    try:
        result = asyncio.run(tool.fn(**kwargs))
    except SystemExit:
        raise
    except TypeError as e:
        # Catch missing required args (e.g. "missing 1 required positional argument")
        axi_error(
            f"Tool `{tool_name}` missing required argument",
            f"Run `reddit-lyr --tool-info {tool_name}` to see required parameters",
        )
    except Exception as e:
        axi_error(f"Tool `{tool_name}` failed: {e}")

    # Output
    if use_json:
        print(json.dumps(result, indent=2))
    else:
        # AXI §1: TOON is default, §2: compact schemas for lists
        compact = _compact_result(result)
        # AXI §5: definitive empty state — message instead of empty table
        count = compact.get("count", 0) if isinstance(compact, dict) else 0
        if isinstance(compact, dict) and "items" in compact and count == 0:
            tool_label = tool_name.replace("_", " ")
            print(f"items: 0 {tool_label} results found")
        else:
            print(_toon_object(compact))
        # AXI §3: hint when content was truncated
        if _has_truncation(compact):
            print(_toon_kv("help", f"Run `reddit-lyr {tool_name} --full` to see complete text"))
        # AXI §9: contextual next-step hints (only for lists with items)
        if isinstance(compact, dict) and "items" in compact and count > 0:
            next_cursor = compact.get("next")
            hints = []
            if next_cursor:
                hints.append(f"More results available — pass the `next` cursor to paginate")
            if count < 10:
                hints.append(f"Showing all {count} results")
            if hints:
                print(f"help[{len(hints)}]:")
                for h in hints:
                    print(f"  {h}")


def main():
    global FULL_OUTPUT
    # ── Account selection: global --account flag, before anything else ──
    # Accepts both `--account NAME` and `--account=NAME`, anywhere in argv.
    _acct = None
    _argv = list(sys.argv)
    i = 1
    while i < len(_argv):
        a = _argv[i]
        if a == "--account" and i + 1 < len(_argv):
            _acct = _argv[i + 1]
            del _argv[i:i + 2]
            continue
        if a.startswith("--account="):
            _acct = a.split("=", 1)[1]
            del _argv[i]
            continue
        i += 1
    if _acct:
        from reddit_mcp_server.session_state import account_exists, list_accounts
        if not account_exists(_acct) and _acct != "default":
            names = ", ".join(a["name"] for a in list_accounts())
            axi_error(
                f"Unknown account: '{_acct}'",
                f"Known accounts: {names}. To register one, run "
                f"`REDDIT_ACCOUNT={_acct} reddit-lyr --login` (or --cookies-file) once.",
            )
        os.environ["REDDIT_ACCOUNT"] = _acct
        sys.argv = _argv

    # ── Direct tool invocation: reddit-lyr <tool_name> [args...] ──────
    # Intercept before argparse so tool names aren't parsed as flags.
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        tool_name = sys.argv[1]
        tool_names = [t[0] for t in _get_tools()]
        if tool_name in tool_names:
            # Filter out CLI-only flags, keep tool args
            use_json = "--json" in sys.argv
            if "--full" in sys.argv:
                FULL_OUTPUT = True
            remaining = [a for a in sys.argv[2:] if a not in ("--json", "--full")]
            run_tool_direct(tool_name, remaining, use_json=use_json)
            return
        # Looks like a tool name but doesn't match — fail early with valid list
        axi_error(
            f"Unknown tool: '{tool_name}'",
            f"Valid tools: {', '.join(tool_names)}",
        )

    parser = argparse.ArgumentParser(description="Reddit MCP Server", add_help=False)
    parser.add_argument("--login", action="store_true", help="Import cookies from browser")
    parser.add_argument(
        "--cookies-file",
        type=str,
        metavar="PATH",
        help="Import cookies from a JSON file",
    )
    parser.add_argument("--logout", action="store_true", help="Clear stored session")
    parser.add_argument("--status", action="store_true", help="Check authentication status")
    parser.add_argument(
        "--accounts",
        action="store_true",
        help="List all registered accounts",
    )
    parser.add_argument("--list-tools", action="store_true", help="List all available MCP tools")
    parser.add_argument(
        "--tool-info", type=str, metavar="TOOL", help="Show details for a specific tool"
    )
    parser.add_argument(
        "--fields",
        type=str,
        metavar="FIELDS",
        help="Comma-separated list of extra fields to include in list outputs (e.g., --fields cookies)",
    )
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="Install session hook for ambient context (Claude Code, Codex, OpenCode)",
    )
    parser.add_argument(
        "--install-skill",
        action="store_true",
        help="Create installable agent skill for Claude Code (AXI §7)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show complete text fields without truncation (AXI §3)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for HTTP transport")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Render tool results as JSON instead of default TOON format",
    )
    parser.add_argument("--help", "-h", action="store_true", help="Show help message")
    args, unknown = parser.parse_known_args()

    # AXI §6: Fail loud on unrecognized input
    if unknown:
        axi_error(f"Unknown flag: '{unknown[0]}'", "Run `reddit-lyr --help` for usage")

    # Parse --fields into list
    extra_fields = []
    if args.fields:
        extra_fields = [f.strip() for f in args.fields.split(",") if f.strip()]

    # AXI §3: --full bypasses text truncation in normalizer
    if args.full:
        FULL_OUTPUT = True

    if args.help:
        show_help()
        return

    if args.install_hook:
        install_session_hook()
        return

    if args.install_skill:
        install_agent_skill()
        return

    if args.list_tools:
        list_tools_and_exit(extra_fields)

    if args.tool_info:
        tool_info_and_exit(args.tool_info)

    if args.accounts:
        from reddit_mcp_server.session_state import list_accounts, get_active_account

        print(_toon_object({"status": "ok", "accounts": list_accounts()}))
        active = get_active_account()
        print(f"active: {active}")
        print("help[2]:")
        print("  Use `--account NAME` to operate on a specific account")
        print("  Register a new account: REDDIT_ACCOUNT=NAME reddit-lyr --cookies-file <file>")
        return

    if args.login:
        from reddit_mcp_server.cookie_import import import_cookies_interactive
        from reddit_mcp_server.session_state import save_cookies

        cookies = import_cookies_interactive()
        if cookies:
            save_cookies(cookies)
            print(_toon_object({"status": "success", "message": "Cookies saved."}))
        else:
            axi_error(
                "Login failed. No cookies extracted.",
                "Ensure you are logged into Reddit in your browser.",
            )
        return

    if args.cookies_file:
        from reddit_mcp_server.session_state import save_cookies
        from pathlib import Path

        path = Path(args.cookies_file)
        if not path.exists():
            axi_error(f"File not found: {args.cookies_file}", "Check the path and try again.")
        try:
            data = json.loads(path.read_text())
            cookies = data.get("cookies", data) if isinstance(data, dict) else data
            if not isinstance(cookies, dict) or not cookies:
                axi_error(
                    "Invalid cookies file.",
                    "Expected JSON object with cookie names as keys.",
                )
            save_cookies(cookies)
            print(
                _toon_object({
                    "status": "success",
                    "message": f"Imported {len(cookies)} cookies from {path.name}.",
                })
            )
        except json.JSONDecodeError:
            axi_error("Invalid JSON in cookies file.", "File must contain valid JSON.")
        return

    if args.logout:
        from reddit_mcp_server.session_state import clear_cookies

        clear_cookies()
        print(_toon_object({"status": "success", "message": "Logged out. Cookies cleared."}))
        return

    if args.status:
        from reddit_mcp_server.bootstrap import initialize_bootstrap
        from reddit_mcp_server.authentication import get_auth_status

        initialize_bootstrap()  # loads REDDIT_COOKIES env var if present
        status = get_auth_status()
        print(_toon_object({"status": "ok", "auth": status}))
        # AXI §9: Contextual hints based on auth state
        mode = status.get("mode", "none")
        hints = []
        if mode == "full":
            hints.append("Session ready — reads and writes supported")
        elif mode == "reads_only":
            hints.append("Writes blocked: reddit_session cookie missing")
            hints.append("Run `reddit-lyr --login` to restore full access")
        elif mode == "degraded":
            hints.append("Writes may fail: token_v2 cookie missing")
            hints.append("Run `reddit-lyr --login` to restore full access")
        else:
            hints.append("Not authenticated")
            hints.append("Run `reddit-lyr --login` or `reddit-lyr --cookies-file <path>`")
        if hints:
            print(f"help[{len(hints)}]:")
            for h in hints:
                print(f"  {h}")
        return

    # Detect interactive vs MCP-client invocation early so we can suppress
    # logging noise before any server code runs (AXI §1: clean stderr for agents).
    interactive = sys.stdin.isatty()
    is_mcp_stdio = (not interactive) or (args.transport == "stdio")

    # In stdio MCP mode, silence ALL stderr output at the OS level.
    # MCP clients read stdout (JSON-RPC) only — all stderr is noise.
    if is_mcp_stdio:
        os.environ["FASTMCP_LOG_LEVEL"] = "WARNING"
        os.environ["REDDIT_MCP_LOG_LEVEL"] = "WARNING"
        # Redirect fd 2 (stderr) to /devnull at the OS level before any imports.
        # This silences logging, FastMCP banner, curl_cffi warnings — everything.
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, sys.stderr.fileno())
        os.close(devnull_fd)
        sys.stderr = open(os.devnull, "w")  # Python-level redirect too

    # Start MCP server
    from reddit_mcp_server.bootstrap import initialize_bootstrap
    from reddit_mcp_server.server import create_mcp_server

    initialize_bootstrap()
    mcp = create_mcp_server()

    if len(sys.argv) == 1:
        if interactive:
            # Interactive terminal: show home view (CLI mode)
            show_home_view(extra_fields)
            return
        # Pipe from MCP client (e.g. Claude Desktop): start server
        mcp.run(transport="stdio", show_banner=False)
    elif is_mcp_stdio:
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
