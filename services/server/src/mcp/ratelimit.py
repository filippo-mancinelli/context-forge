"""Per-API-key rate limiting: in-process sliding window over the last 60 seconds.

Semantica single-process: con più repliche serve uno store condiviso.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from .context import get_current_principal

WINDOW_SECONDS = 60.0

_windows: dict[int, deque] = defaultdict(deque)


def reset() -> None:
    _windows.clear()


def check(key_id: int, limit: int, now: float | None = None) -> bool:
    """True quando la chiamata rientra nel limite (e la registra)."""
    moment = time.monotonic() if now is None else now
    window = _windows[key_id]
    cutoff = moment - WINDOW_SECONDS
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= limit:
        return False
    window.append(moment)
    return True


def enforce_rate_limit() -> None:
    """Applica il limite della API key corrente; solleva ToolError se superato."""
    principal = get_current_principal()
    if principal.kind != "api_key" or principal.id is None:
        return
    limit = principal.rate_limit_per_minute
    if not limit or limit <= 0:
        return
    if not check(principal.id, limit):
        from .permissions import ToolError

        raise ToolError(f"Rate limit exceeded: {limit} calls per minute for this API key")
