"""Sliding-window rate limit per API key."""
import asyncio

import pytest

from src.mcp import audit, ratelimit
from src.mcp.context import Principal, set_current_principal


def _drain():
    rows = []
    while True:
        try:
            rows.append(audit._queue.get_nowait())
        except asyncio.QueueEmpty:
            return rows


def setup_function():
    ratelimit.reset()
    set_current_principal(None)


def teardown_function():
    ratelimit.reset()
    set_current_principal(None)


def test_under_the_limit_is_allowed():
    assert ratelimit.check(1, 3, now=100.0) is True
    assert ratelimit.check(1, 3, now=100.1) is True
    assert ratelimit.check(1, 3, now=100.2) is True


def test_over_the_limit_is_rejected():
    for i in range(3):
        assert ratelimit.check(1, 3, now=100.0 + i) is True
    assert ratelimit.check(1, 3, now=103.0) is False


def test_rejected_calls_do_not_extend_the_window():
    for i in range(3):
        ratelimit.check(1, 3, now=100.0 + i)
    ratelimit.check(1, 3, now=103.0)
    # 60s after the first allowed call the window has room again.
    assert ratelimit.check(1, 3, now=161.0) is True


def test_window_slides():
    for i in range(3):
        ratelimit.check(1, 3, now=100.0 + i)
    assert ratelimit.check(1, 3, now=200.0) is True


def test_keys_are_independent():
    for i in range(3):
        ratelimit.check(1, 3, now=100.0 + i)
    assert ratelimit.check(2, 3, now=100.0) is True


def test_enforce_ignores_users_and_anonymous():
    set_current_principal(Principal(kind="user", id=5, label="mario", rate_limit_per_minute=1))
    assert ratelimit.enforce_rate_limit() is None
    assert ratelimit.enforce_rate_limit() is None
    assert 5 not in ratelimit._windows
    set_current_principal(None)
    assert ratelimit.enforce_rate_limit() is None
    assert not ratelimit._windows


def test_enforce_ignores_api_key_without_a_limit():
    set_current_principal(Principal(kind="api_key", id=9, label="ci-bot"))
    for _ in range(5):
        assert ratelimit.enforce_rate_limit() is None
    assert 9 not in ratelimit._windows


def test_enforce_raises_over_the_limit():
    set_current_principal(Principal(kind="api_key", id=9, label="ci-bot", rate_limit_per_minute=2))
    ratelimit.enforce_rate_limit()
    ratelimit.enforce_rate_limit()
    with pytest.raises(Exception, match="Rate limit exceeded: 2 calls per minute"):
        ratelimit.enforce_rate_limit()


def test_decorator_rate_limits_api_keys_only():
    from src.mcp import permissions as perms

    @perms.requires_permission("context-read")
    async def fake_tool() -> str:
        return "ok"

    perms.set_current_permissions(frozenset({"*"}))
    set_current_principal(Principal(kind="api_key", id=4, label="ci-bot", rate_limit_per_minute=1))
    try:
        assert asyncio.run(fake_tool()) == "ok"
        with pytest.raises(Exception, match="Rate limit exceeded"):
            asyncio.run(fake_tool())
        set_current_principal(Principal(kind="user", id=4, label="mario", rate_limit_per_minute=1))
        assert asyncio.run(fake_tool()) == "ok"
    finally:
        perms.set_current_permissions(None)


def test_decorator_audits_rate_limited_calls_as_rate_limited():
    from src.mcp import permissions as perms

    @perms.requires_permission("context-read")
    async def fake_tool() -> str:
        return "ok"

    perms.set_current_permissions(frozenset({"*"}))
    set_current_principal(Principal(kind="api_key", id=4, label="ci-bot", rate_limit_per_minute=1))
    _drain()
    try:
        assert asyncio.run(fake_tool()) == "ok"
        _drain()  # discard the "ok" row from the first, allowed call
        with pytest.raises(Exception, match="Rate limit exceeded"):
            asyncio.run(fake_tool())
        row = _drain()[0]
        assert row["outcome"] == "rate_limited"
        assert row["tool"] == "fake_tool"
        assert row["permission"] == "context-read"
    finally:
        perms.set_current_permissions(None)
