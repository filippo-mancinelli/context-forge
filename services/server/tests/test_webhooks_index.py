"""Tests for the /webhooks/index route's auto-memory namespace scoping."""
import inspect

from src.api.routes import webhooks


def test_auto_memory_uses_project_namespace_not_global_default():
    src_text = inspect.getsource(webhooks.webhook_index)
    assert "memory_namespace" in src_text
    assert "cfg.memory.user_id" not in src_text
