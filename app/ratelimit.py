"""In-process rate limiting (Stage 6 Item 12 hardening, blueprint Section J).

Concrete targets from Section J:
* authentication endpoints  ~5 requests / minute / IP
* Gemini-backed endpoints  ~20 requests / minute / user

Implementation: fixed-window counters in a plain dict guarded by a lock.
This is deliberately dependency-free and appropriate for this project's
single-instance Flask + SQLite deployment.

DEPLOYMENT LIMITATION (documented for Section K.12 "deployment prep"):
counters live in process memory only. They are NOT shared across multiple
worker processes and are lost on restart, so the limits hold exactly when
the app runs as a single process (the deployment shape this capstone
targets). Horizontal scaling would require external infrastructure, which
the blueprint excludes.

The limiter is disabled while ``app.config["TESTING"]`` is true so test
suites exercise functional behaviour without tripping production windows;
dedicated rate-limit tests opt back in via ``RATELIMIT_ENABLED = True``.
"""

import threading
import time

from flask import abort, current_app, g, request

# Injectable clock: tests advance this to verify window reset behaviour.
_now = time.time

_counters = {}
_lock = threading.Lock()


def _reset_for_tests():
    """Clear every counter (rate-limit test suites start from zero)."""
    with _lock:
        _counters.clear()

# Window keys are pruned lazily whenever they expire; this caps growth from
# spoofed IPs by dropping state for any key idle longer than its window.
_MAX_TRACKED_KEYS = 10000


def _limit_state(key, limit, window_seconds):
    """Return (is_limited, remaining) for one fixed-window bucket."""
    now = _now()
    with _lock:
        if len(_counters) > _MAX_TRACKED_KEYS:
            for stale_key in [
                k for k, (start, _) in _counters.items()
                if now - start >= window_seconds
            ]:
                _counters.pop(stale_key, None)
        start, count = _counters.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        _counters[key] = (start, count)
        return count > limit, max(limit - count, 0)


def rate_limit_enabled():
    cfg = current_app.config
    return bool(cfg.get("RATELIMIT_ENABLED", not cfg.get("TESTING", False)))


def check_limit(key, limit, window_seconds):
    """Abort with 429 when `key` exceeds `limit` per `window_seconds`.

    The error text is deliberately generic — it never exposes internal
    details such as counter values or window arithmetic.
    """
    limited, _remaining = _limit_state(key, limit, window_seconds)
    if limited:
        abort(429)


def check_auth_limit():
    """Blueprint before_request hook: ~5 POSTs/min/IP on auth endpoints.

    Only state-changing attempts are counted — page loads stay unlimited.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if not rate_limit_enabled():
        return
    limit = current_app.config["RATELIMIT_AUTH_LIMIT"]
    window = current_app.config["RATELIMIT_AUTH_WINDOW"]
    check_limit(f"auth:{request.remote_addr}", limit, window)


def check_gemini_limit():
    """View precondition: ~20 AI calls/min/user (blueprint Section J).

    Call at the very top of every route that triggers Gemini work so a
    rejected request never reaches the API.
    """
    if not rate_limit_enabled():
        return
    limit = current_app.config["RATELIMIT_GEMINI_LIMIT"]
    window = current_app.config["RATELIMIT_GEMINI_WINDOW"]
    check_limit(f"gemini:{g.user['id']}", limit, window)
