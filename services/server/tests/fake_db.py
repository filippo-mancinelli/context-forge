"""Fake asyncpg pool/connection so tests never need a live database."""
from __future__ import annotations


class FakeConn:
    """Records every statement; answers fetchval from a scripted queue."""

    def __init__(
        self, fetchval_results=None, fetch_rows=None, fetchrow_results=None, execute_error=None,
        execute_result="OK",
    ):
        self.executed: list[tuple[str, tuple]] = []
        self.fetchval_results = list(fetchval_results or [])
        self.fetch_rows = list(fetch_rows or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.transactions = 0
        self.open_transactions = 0
        # Optional exception raised by execute() after recording the call, to
        # exercise callers that must tolerate a failed write.
        self.execute_error = execute_error
        # Command tag returned by execute(), e.g. "DELETE 4", for callers that
        # parse the affected-row count out of it.
        self.execute_result = execute_result

    @property
    def sql(self) -> list[str]:
        return [query for query, _ in self.executed]

    async def execute(self, query, *args, **kwargs):
        self.executed.append((query, args))
        if self.execute_error is not None:
            raise self.execute_error
        return self.execute_result

    async def fetchval(self, query, *args, **kwargs):
        self.executed.append((query, args))
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return None

    async def fetch(self, query, *args, **kwargs):
        self.executed.append((query, args))
        # A list of lists scripts one result set per successive fetch() call
        # (popped in order); a flat list of rows is returned as-is every call.
        if self.fetch_rows and isinstance(self.fetch_rows[0], list):
            return self.fetch_rows.pop(0)
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
