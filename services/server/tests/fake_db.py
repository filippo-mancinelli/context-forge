"""Fake asyncpg pool/connection so tests never need a live database."""
from __future__ import annotations


class FakeConn:
    """Records every statement; answers fetchval from a scripted queue."""

    def __init__(self, fetchval_results=None, fetch_rows=None, fetchrow_results=None):
        self.executed: list[tuple[str, tuple]] = []
        self.fetchval_results = list(fetchval_results or [])
        self.fetch_rows = list(fetch_rows or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.transactions = 0
        self.open_transactions = 0

    @property
    def sql(self) -> list[str]:
        return [query for query, _ in self.executed]

    async def execute(self, query, *args, **kwargs):
        self.executed.append((query, args))
        return "OK"

    async def fetchval(self, query, *args, **kwargs):
        self.executed.append((query, args))
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return None

    async def fetch(self, query, *args, **kwargs):
        self.executed.append((query, args))
        return self.fetch_rows

    async def fetchrow(self, query, *args, **kwargs):
        self.executed.append((query, args))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self_inner):
                conn.transactions += 1
                conn.open_transactions += 1
                return self_inner

            async def __aexit__(self_inner, *exc):
                conn.open_transactions -= 1
                return False

        return _Tx()


class FakePool:
    """Hands the same FakeConn to every acquire()."""

    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.acquired = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self_inner):
                pool.acquired += 1
                return pool.conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()
