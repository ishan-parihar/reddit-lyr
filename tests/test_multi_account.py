"""Multi-account support tests for reddit-lyr session_state + CLI.

Run: python3 -m pytest tests/test_multi_account.py -v
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Point reddit-lyr's data root at a temp dir."""
    root = tmp_path / "reddit-lyr-test"
    monkeypatch.setenv("REDDIT_MCP_PROFILE_DIR", str(root))
    monkeypatch.delenv("REDDIT_ACCOUNT", raising=False)
    monkeypatch.delenv("REDDIT_USE_DAEMON", raising=False)
    return root


def _write_cookies(path: Path, token: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": {"token_v2": token}}))


class TestSessionStateAccounts:
    def test_default_account_uses_top_level_store(self, isolated_root):
        from reddit_mcp_server.session_state import get_profile_dir, COOKIES_FILE
        _write_cookies(isolated_root / COOKIES_FILE, "tok-default")
        assert get_profile_dir() == isolated_root

    def test_named_account_uses_subdirectory(self, isolated_root, monkeypatch):
        monkeypatch.setenv("REDDIT_ACCOUNT", "alt")
        from reddit_mcp_server.session_state import get_profile_dir, COOKIES_FILE
        assert get_profile_dir() == isolated_root / "accounts" / "alt"
        _write_cookies(get_profile_dir() / COOKIES_FILE, "tok-alt")

    def test_load_cookies_isolation(self, isolated_root, monkeypatch):
        from reddit_mcp_server.session_state import load_cookies, save_cookies
        save_cookies({"token_v2": "tok-default"})
        monkeypatch.setenv("REDDIT_ACCOUNT", "alt")
        assert load_cookies() == {}  # alt has no cookies yet
        save_cookies({"token_v2": "tok-alt"})
        assert load_cookies() == {"token_v2": "tok-alt"}
        monkeypatch.delenv("REDDIT_ACCOUNT")
        assert load_cookies() == {"token_v2": "tok-default"}

    def test_list_accounts(self, isolated_root, monkeypatch):
        from reddit_mcp_server.session_state import list_accounts, save_cookies
        save_cookies({"token_v2": "tok-default"})
        monkeypatch.setenv("REDDIT_ACCOUNT", "alt")
        save_cookies({"token_v2": "tok-alt"})
        monkeypatch.delenv("REDDIT_ACCOUNT")
        accounts = list_accounts()
        names = {a["name"] for a in accounts}
        assert names == {"default", "alt"}
        default = next(a for a in accounts if a["name"] == "default")
        assert default["default"] is True and default["has_cookies"]

    def test_account_exists(self, isolated_root, monkeypatch):
        from reddit_mcp_server.session_state import save_cookies, account_exists
        monkeypatch.setenv("REDDIT_ACCOUNT", "alt")
        save_cookies({"token_v2": "x"})
        assert account_exists("alt")
        assert not account_exists("ghost")

    def test_profile_dir_env_relocates_root(self, isolated_root, monkeypatch):
        """REDDIT_MCP_PROFILE_DIR relocates the ROOT; account still selects within it."""
        monkeypatch.setenv("REDDIT_ACCOUNT", "alt")
        raw = isolated_root / "raw-override"
        monkeypatch.setenv("REDDIT_MCP_PROFILE_DIR", str(raw))
        from reddit_mcp_server.session_state import get_profile_dir
        assert get_profile_dir() == raw / "accounts" / "alt"

    def test_save_cookies_never_nests(self, isolated_root):
        from reddit_mcp_server.session_state import load_cookies, save_cookies
        save_cookies(load_cookies())  # round-trip the wrapped dict
        save_cookies(load_cookies())  # twice
        data = json.loads((isolated_root / "cookies.json").read_text())
        assert set(data.keys()) == {"cookies"}


class TestDaemonGuard:
    def test_daemon_enabled_for_default(self, isolated_root, monkeypatch):
        monkeypatch.delenv("REDDIT_ACCOUNT", raising=False)
        monkeypatch.delenv("REDDIT_MCP_PROFILE_DIR", raising=False)
        # reload module to re-evaluate flag
        import importlib
        import reddit_mcp_server.dependencies as deps
        importlib.reload(deps)
        assert deps.USE_DAEMON is True

    def test_daemon_disabled_for_named_account(self, isolated_root, monkeypatch):
        monkeypatch.setenv("REDDIT_ACCOUNT", "alt")
        monkeypatch.delenv("REDDIT_MCP_PROFILE_DIR", raising=False)
        import importlib
        import reddit_mcp_server.dependencies as deps
        importlib.reload(deps)
        assert deps.USE_DAEMON is False

    def test_daemon_disabled_for_raw_profile_dir(self, isolated_root, monkeypatch):
        """Profile-dir relocation with default account keeps daemon on; only
        named accounts disable it (the daemon still serves that root's default)."""
        monkeypatch.delenv("REDDIT_ACCOUNT", raising=False)
        monkeypatch.setenv("REDDIT_MCP_PROFILE_DIR", str(isolated_root))
        import importlib
        import reddit_mcp_server.dependencies as deps
        importlib.reload(deps)
        assert deps.USE_DAEMON is True

    def test_explicit_daemon_off_stays_off(self, isolated_root, monkeypatch):
        monkeypatch.setenv("REDDIT_USE_DAEMON", "false")
        monkeypatch.delenv("REDDIT_ACCOUNT", raising=False)
        monkeypatch.delenv("REDDIT_MCP_PROFILE_DIR", raising=False)
        import importlib
        import reddit_mcp_server.dependencies as deps
        importlib.reload(deps)
        assert deps.USE_DAEMON is False


class TestCLIAccountFlag:
    def test_accounts_listing(self, isolated_root):
        _write_cookies(isolated_root / "cookies.json", "tok-default")
        r = subprocess.run(
            [sys.executable, "-m", "reddit_mcp_server.cli_main", "--accounts"],
            capture_output=True, text=True, cwd=str(REPO),
            env={**os.environ, "REDDIT_MCP_PROFILE_DIR": str(isolated_root)},
        )
        assert "default" in r.stdout
        assert "status: ok" in r.stdout

    def test_unknown_account_fails_loud(self, isolated_root):
        r = subprocess.run(
            [sys.executable, "-m", "reddit_mcp_server.cli_main",
             "--account", "ghost", "--status"],
            capture_output=True, text=True, cwd=str(REPO),
            env={**os.environ, "REDDIT_MCP_PROFILE_DIR": str(isolated_root)},
        )
        assert r.returncode != 0
        assert "Unknown account" in (r.stdout + r.stderr)

    def test_status_scopes_to_account(self, isolated_root):
        """--account alt --status must reflect the alt store, not default."""
        _write_cookies(isolated_root / "cookies.json", "tok-default")
        _write_cookies(isolated_root / "accounts" / "alt" / "cookies.json", "tok-alt")
        r = subprocess.run(
            [sys.executable, "-m", "reddit_mcp_server.cli_main",
             "--account", "alt", "--status"],
            capture_output=True, text=True, cwd=str(REPO),
            env={**os.environ, "REDDIT_MCP_PROFILE_DIR": str(isolated_root),
                 "REDDIT_USE_DAEMON": "false"},
        )
        assert "authenticated: true" in r.stdout
        # and the default store is untouched
        assert json.loads((isolated_root / "cookies.json").read_text())["cookies"]["token_v2"] == "tok-default"

    def test_tool_invocation_accepts_account_flag(self, isolated_root):
        """--account placed before a tool name must not be treated as tool args."""
        r = subprocess.run(
            [sys.executable, "-m", "reddit_mcp_server.cli_main",
             "--account", "alt", "get_my_profile"],
            capture_output=True, text=True, cwd=str(REPO),
            env={**os.environ, "REDDIT_MCP_PROFILE_DIR": str(isolated_root),
                 "REDDIT_USE_DAEMON": "false"},
        )
        # alt store exists but has bogus cookies -> auth failure mentioning session,
        # NOT "Unknown tool" — proving the flag was consumed as an account selector.
        combined = r.stdout + r.stderr
        assert "Unknown tool" not in combined
