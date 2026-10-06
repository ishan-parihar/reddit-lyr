"""JWT liveness + token_v2 re-mint logic tests (no network).

Run: python3 -m pytest tests/test_session_health.py -v
"""
import base64
import json
import time
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from reddit_mcp_server.session_health import (
    decode_jwt_exp,
    is_jwt_expired,
    token_v2_stale,
    reddit_session_usable,
)


def _make_jwt(claims: dict) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def _exp(offset: float) -> float:
    return time.time() + offset


class TestDecodeJwtExp:
    def test_decodes_valid_jwt(self):
        tok = _make_jwt({"exp": _exp(3600), "sub": "t2_x"})
        assert decode_jwt_exp(tok) == pytest.approx(_exp(3600), abs=5)

    def test_none_for_missing_token(self):
        assert decode_jwt_exp(None) is None
        assert decode_jwt_exp("") is None

    def test_none_for_non_jwt(self):
        assert decode_jwt_exp("not-a-jwt") is None
        assert decode_jwt_exp("only.two") is None

    def test_none_for_garbage_payload(self):
        assert decode_jwt_exp("x.!!!.y") is None


class TestIsJwtExpired:
    def test_future_exp_not_expired(self):
        assert is_jwt_expired(_make_jwt({"exp": _exp(3600)})) is False

    def test_past_exp_expired(self):
        assert is_jwt_expired(_make_jwt({"exp": _exp(-3600)})) is True

    def test_margin_counts_as_expired(self):
        tok = _make_jwt({"exp": _exp(600)})  # 10 min out
        assert is_jwt_expired(tok, margin_seconds=1800) is True
        assert is_jwt_expired(tok, margin_seconds=60) is False

    def test_non_jwt_not_expired(self):
        # Unknown expiry -> NOT expired: live validation remains the authority
        assert is_jwt_expired("garbage") is False


class TestTokenV2Stale:
    def test_stale_when_expired(self):
        cookies = {"token_v2": _make_jwt({"exp": _exp(-60)})}
        assert token_v2_stale(cookies) is True

    def test_stale_within_margin(self):
        cookies = {"token_v2": _make_jwt({"exp": _exp(900)})}
        assert token_v2_stale(cookies) is True  # 30-min default margin

    def test_fresh_token_not_stale(self):
        cookies = {"token_v2": _make_jwt({"exp": _exp(86400)})}
        assert token_v2_stale(cookies) is False

    def test_missing_token_not_stale(self):
        assert token_v2_stale({}) is False

    def test_non_jwt_token_not_stale(self):
        # Non-JWT tokens may be opaque but valid; don't force re-mint
        assert token_v2_stale({"token_v2": "opaque"}) is False


class TestRedditSessionUsable:
    def test_usable_when_valid(self):
        cookies = {"reddit_session": _make_jwt({"exp": _exp(86400 * 180)})}
        assert reddit_session_usable(cookies) is True

    def test_not_usable_when_expired(self):
        cookies = {"reddit_session": _make_jwt({"exp": _exp(-86400)})}
        assert reddit_session_usable(cookies) is False

    def test_not_usable_when_missing(self):
        assert reddit_session_usable({}) is False

    def test_non_jwt_session_treated_usable(self):
        # Unknown expiry -> let the mint attempt decide (it verifies live)
        assert reddit_session_usable({"reddit_session": "opaque"}) is True
