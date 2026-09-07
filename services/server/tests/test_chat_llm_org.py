import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import chat as chat_routes
from src.org_settings import OrgSettings


def test_resolve_llm_uses_org_settings():
    s = OrgSettings(llm_provider="anthropic", llm_model="claude-haiku-4-5", anthropic_api_key="ak")
    llm = chat_routes._resolve_llm(s)
    assert llm == {"family": "anthropic", "model": "claude-haiku-4-5", "api_key": "ak"}


def test_resolve_llm_missing_key_raises():
    s = OrgSettings(llm_provider="anthropic", llm_model="claude-haiku-4-5", anthropic_api_key="")
    with pytest.raises(HTTPException) as exc:
        chat_routes._resolve_llm(s)
    assert exc.value.status_code == 400


def test_provider_api_key_reads_org_settings():
    s = OrgSettings(openai_api_key="ok", deepseek_api_key="dk")
    assert chat_routes._provider_api_key(s, "openai") == "ok"
    assert chat_routes._provider_api_key(s, "deepseek") == "dk"
