"""JWT-based cookie liveness checks for Reddit session cookies.

Reddit's token_v2 and reddit_session cookies are JWTs whose payload carries
an `exp` claim. An expired token_v2 makes the client look "anonymous" even
when the long-lived reddit_session is still perfectly valid — and a fresh
token_v2 is minted simply by visiting reddit.com with that session. These
helpers let callers distinguish "needs re-login" from "needs a cheap re-mint".

Standard library only — this module must stay import-light.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Optional


def decode_jwt_exp(token: Optional[str]) -> Optional[float]:
    """Return the `exp` claim of a JWT, or None if not decodable."""
    if not token or not isinstance(token, str) or token.count(".") < 2:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # restore base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:
        # A JWT that cannot be decoded is treated as having unknown expiry.
        pass
    return None


def is_jwt_expired(token: Optional[str], margin_seconds: float = 0.0) -> bool:
    """True if the token's `exp` is in the past (plus optional margin).

    Tokens that do not decode are NOT assumed expired — callers should fall
    back to live-API validation for those.
    """
    exp = decode_jwt_exp(token)
    if exp is None:
        return False
    return exp <= time.time() + margin_seconds


def token_v2_stale(cookies: dict, margin_seconds: float = 1800.0) -> bool:
    """True if token_v2 is provably expired (or expiring within margin).

    A 30-minute default margin avoids minting a token seconds before its
    natural expiry. Missing/non-JWT token_v2 returns False — presence checks
    elsewhere handle that case, and live validation is the authority there.
    """
    return is_jwt_expired(cookies.get("token_v2"), margin_seconds)


def reddit_session_usable(cookies: dict) -> bool:
    """True if reddit_session exists and is not provably expired.

    This is the gate for a cheap token_v2 re-mint: if reddit_session is still
    valid, hitting reddit.com with it mints a fresh token_v2 WITHOUT a manual
    re-login. If it's expired, only a real re-login can recover.
    """
    session = cookies.get("reddit_session")
    if not session:
        return False
    return not is_jwt_expired(session)
