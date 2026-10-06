import os
import json
import shutil
from pathlib import Path
from reddit_mcp_server.constants import DEFAULT_PROFILE_DIR, COOKIES_FILE

OLD_PROFILE_DIR = "~/.reddit-httpx"
DEFAULT_ACCOUNT = "default"
ACCOUNTS_SUBDIR = "accounts"


def get_profile_root() -> Path:
    """The reddit-lyr data root (default ~/.reddit-lyr). REDDIT_MCP_PROFILE_DIR
    overrides the whole root for advanced/raw use (unversioned compatibility)."""
    d = Path(os.environ.get("REDDIT_MCP_PROFILE_DIR", DEFAULT_PROFILE_DIR)).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_active_account() -> str:
    """Named account for this invocation. Precedence:
    1. REDDIT_ACCOUNT env var (set by the CLI --account flag or by callers)
    2. "default" (the legacy top-level cookie store — full back-compat)
    """
    return os.environ.get("REDDIT_ACCOUNT", DEFAULT_ACCOUNT) or DEFAULT_ACCOUNT


def account_dir(root: Path, name: str) -> Path:
    if name == DEFAULT_ACCOUNT:
        return root
    return root / ACCOUNTS_SUBDIR / name


def list_accounts() -> list[dict]:
    """All known accounts: the default store plus every directory under
    <root>/accounts/ that holds a cookies file."""
    root = get_profile_root()
    accounts = []
    default_cookies = root / COOKIES_FILE
    accounts.append({
        "name": DEFAULT_ACCOUNT,
        "dir": str(root),
        "has_cookies": default_cookies.exists(),
        "default": True,
    })
    acc_root = root / ACCOUNTS_SUBDIR
    if acc_root.is_dir():
        for d in sorted(acc_root.iterdir()):
            if d.is_dir() and (d / COOKIES_FILE).exists():
                accounts.append({
                    "name": d.name,
                    "dir": str(d),
                    "has_cookies": True,
                    "default": False,
                })
    return accounts


def account_exists(name: str) -> bool:
    return any(a["name"] == name for a in list_accounts())


def get_profile_dir() -> Path:
    """Cookie-store dir for the ACTIVE account.

    REDDIT_MCP_PROFILE_DIR relocates the data ROOT (advanced use); the named
    account (REDDIT_ACCOUNT) always selects within that root. Default account
    = the root itself (legacy top-level store, full back-compat).
    """
    d = account_dir(get_profile_root(), get_active_account())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unwrap(data: dict) -> dict:
    """Unwrap nested `cookies` keys (the daemon round-trips an already-wrapped
    format, producing {"cookies": {"cookies": {...}}})."""
    while isinstance(data, dict) and "cookies" in data and set(data) == {"cookies"}:
        data = data["cookies"]
    return data


def _has_usable_cookies(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    cookies = _unwrap(data)
    return isinstance(cookies, dict) and any(
        isinstance(v, str) and v for v in cookies.values()
    )


def _migrate_if_needed() -> None:
    """Copy cookies from ~/.reddit-httpx/ to ~/.reddit-lyr/ if the active file
    is missing or holds no usable cookies (e.g. a corrupt/empty file that would
    otherwise strand valid legacy cookies). Legacy migration applies ONLY to
    the default account — never seed a named account's store from the old
    single-account cookies (that would cross accounts)."""
    if get_active_account() != DEFAULT_ACCOUNT:
        return
    old = Path(OLD_PROFILE_DIR).expanduser() / COOKIES_FILE
    new = get_profile_dir() / COOKIES_FILE
    if old.exists() and (not new.exists() or not _has_usable_cookies(new)):
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, new)


def get_cookies_path() -> Path:
    _migrate_if_needed()
    return get_profile_dir() / COOKIES_FILE


def load_cookies() -> dict[str, str]:
    path = get_cookies_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return {}
    return _unwrap(data)


def save_cookies(cookies: dict[str, str]) -> None:
    # Normalize: if callers pass an already-wrapped dict (e.g. the result of
    # load_cookies or obscura's storage.load), unwrap first so repeated
    # write-back round-trips never nest cookies keys deeper.
    cookies = _unwrap(cookies)
    path = get_cookies_path()
    path.write_text(json.dumps({"cookies": cookies}, indent=2))


def clear_cookies() -> None:
    path = get_cookies_path()
    if path.exists():
        path.unlink()
