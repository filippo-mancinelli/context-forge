import asyncio

from src import reembed


def test_reembed_org_processes_all_tables(monkeypatch):
    updated = []

    async def fake_iter_chunks(table, org_id, batch_size):
        assert org_id == 1
        yield [(10, "hello"), (11, "world")]

    async def fake_embed_batch(texts, org_id):
        assert org_id == 1
        return [[0.1] * 4 for _ in texts]

    async def fake_update(table, pairs, org_id):
        updated.append((table, [p[0] for p in pairs], org_id))

    async def fake_job_update(job_id, status, result=None, error=None):
        pass

    monkeypatch.setattr(reembed, "_iter_chunks", fake_iter_chunks)
    monkeypatch.setattr(reembed, "embed_batch", fake_embed_batch)
    monkeypatch.setattr(reembed, "_update_embeddings", fake_update)
    monkeypatch.setattr(reembed, "_set_job_status", fake_job_update)

    result = asyncio.run(reembed.reembed_org(1, "job-1"))
    assert result == {"repo_chunks": 2, "kb_chunks": 2, "web_chunks": 2}
    assert [t for t, _, _ in updated] == ["repo_chunks", "kb_chunks", "web_chunks"]
    assert all(org_id == 1 for _, _, org_id in updated)


def test_reembed_org_failure_marks_job_failed_without_raising(monkeypatch):
    statuses = []

    async def fake_iter_chunks(table, org_id, batch_size):
        yield [(10, "hello")]

    async def fake_embed_batch(texts, org_id):
        raise RuntimeError("embedding backend unavailable")

    async def fake_update(table, pairs, org_id):
        pass

    async def fake_job_update(job_id, status, result=None, error=None):
        statuses.append((job_id, status, error))

    monkeypatch.setattr(reembed, "_iter_chunks", fake_iter_chunks)
    monkeypatch.setattr(reembed, "embed_batch", fake_embed_batch)
    monkeypatch.setattr(reembed, "_update_embeddings", fake_update)
    monkeypatch.setattr(reembed, "_set_job_status", fake_job_update)

    result = asyncio.run(reembed.reembed_org(1, "job-1"))

    assert result == {}
    assert ("job-1", "running", None) in statuses
    failed = [s for s in statuses if s[1] == "failed"]
    assert len(failed) == 1
    assert failed[0][0] == "job-1"
    assert "embedding backend unavailable" in failed[0][2]
